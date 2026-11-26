from datetime import date, timedelta
from io import BytesIO

from PIL import Image
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models.mvp import MVPBase
from scripts import seed_academic_mvp as seed

BATCH = "a" * 32
FIRST = date(2030, 1, 1)


@pytest.fixture
def rehearsal(tmp_path):
    engine = create_engine("sqlite://")
    MVPBase.metadata.create_all(engine)
    image = BytesIO()
    Image.new("RGB", (16, 16)).save(image, "JPEG")
    photos = {key: image.getvalue() for key in seed.PHOTOS}
    yield engine, tmp_path, photos
    engine.dispose()


def apply(rehearsal, first=FIRST):
    engine, directory, photos = rehearsal
    with engine.begin() as connection, Session(bind=connection) as db:
        result = seed.apply_seed(
            db, seed.catalog(), first, True, BATCH, photos, directory,
            lambda _: "isolated test password only",
        )
        db.flush()
        return result


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_seed_counts_rerun_and_cleanup_preserve_other_users(rehearsal):
    engine, _, _ = rehearsal
    assert apply(rehearsal) == {"accounts": 25, "listings": 120, "slots": 5480, "posts": 20}
    assert apply(rehearsal) == {"accounts": 0, "listings": 0, "slots": 0, "posts": 0}
    assert apply(rehearsal, FIRST + timedelta(days=1)) == {
        "accounts": 0, "listings": 0, "slots": 140, "posts": 0,
    }
    with engine.begin() as connection, Session(bind=connection) as db:
        assert count(db, seed.MVPInventoryUnit) == 140
        assert count(db, seed.MVPListingMedia) == 120
        hashes = db.scalars(select(seed.MVPUser.hashed_password)).all()
        assert len(set(hashes)) == 25
        other = seed.MVPUser(
            email="existing@example.invalid", role="traveler", full_name="Existing account",
            is_active=True, hashed_password="preserve-existing-value",
        )
        db.add(other)
        db.flush()
        assert seed.retire_seed(db, BATCH, True) == {
            "accounts": 25, "listings": 120, "posts": 20,
        }
        assert other.is_active and other.hashed_password == "preserve-existing-value"
        db.flush()
        assert count(db, seed.MVPUser) == 26
        assert count(db, seed.MVPListing) == 120
        assert count(db, seed.MVPCommunityPost) == 20


def test_reserved_email_collision_preserves_existing_account(rehearsal):
    engine, _, _ = rehearsal
    with Session(engine) as db:
        db.add(seed.MVPUser(
            email=seed.demo_email("traveler", 1), role="traveler", full_name="Existing account",
            is_active=True, hashed_password="preserve-existing-value",
        ))
        db.commit()
    with pytest.raises(seed.SeedError, match="Account collision"):
        apply(rehearsal)
    with Session(engine) as db:
        user = db.scalar(select(seed.MVPUser))
        assert user.is_active and user.hashed_password == "preserve-existing-value"
        assert count(db, seed.MVPListing) == 0


def test_failed_seed_rolls_back_and_offline_photos_decode(rehearsal):
    engine, directory, photos = rehearsal
    selected = seed.catalog()[0]
    asset = seed.PHOTOS[selected.photo]
    (directory / asset.filename).write_bytes(photos[selected.photo])
    decoded = seed.download_photos({selected.photo}, directory)
    assert decoded[selected.photo].startswith(b"\xff\xd8")
    with pytest.raises(seed.SeedError, match="reserved demo photo"):
        conflict = directory / f"academic_seed_{BATCH}_{selected.key}.jpg"
        conflict.write_bytes(b"Existing file")
        with engine.begin() as connection, Session(bind=connection) as db:
            seed.apply_seed(
                db, [selected], FIRST, True, BATCH, photos, directory,
                lambda _: "isolated test password only",
            )
    assert conflict.read_bytes() == b"Existing file"
    with Session(engine) as db:
        assert count(db, seed.MVPUser) == 0
        assert count(db, seed.MVPListing) == 0
