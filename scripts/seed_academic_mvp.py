"""Plan, apply, or retire CeylonGo's Academic MVP demonstration catalog."""

import argparse
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from getpass import getpass
from hashlib import md5
from io import BytesIO
import json
from pathlib import Path
import re
import secrets
import sys
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageOps
from sqlalchemy import create_engine, func, inspect, insert, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database_guard import normalize_server_name
from app.core.security import hash_password
from app.models.mvp import (
    MVPAvailabilitySlot, MVPBooking, MVPCommunityPost, MVPInventoryUnit, MVPListing,
    MVPListingMedia, MVPListingVerification, MVPProviderProfile, MVPUser,
)
from app.schemas.mvp_activity import ActivityMetadata
from app.schemas.mvp_guide import GuidePackage, GuideProfile
from app.schemas.mvp_homestay import HomestayMetadata
from app.schemas.mvp_rental import RentalMetadata
from app.services.mvp_homestay import SRI_LANKA, utc_naive
from app.services.mvp_listing_verification import PHOTO_PREFIX, locked_listing

SERVER = r"VISHNUGA-KAJARASH\SQLEXPRESS"
DATABASE = "CeylonGoDB"
DOMAIN = "example.com"
LEGACY_DOMAIN = "academic-demo.invalid"
RECEIPT = ROOT / "uploads" / ".academic-mvp-seed.json"
LOCAL_PHOTOS = ROOT / "uploads" / "academic-mvp-seed-photos"
LOCAL_ILLUSTRATIONS = ROOT / "uploads" / "academic-mvp-seed-illustrations"
DAYS = 90
NOTICE = "Academic demo only. No real business verification or reservation is implied."


class SeedError(RuntimeError):
    pass


@dataclass(frozen=True)
class Location:
    district: str
    province: str
    city: str
    latitude: str
    longitude: str
    feature: str


# Approximate town centers or neighborhood markers, never fictional property addresses.
LOCATIONS = (
    Location("Jaffna", "Northern", "Jaffna", "9.6685", "80.0074", "fort and market lanes"),
    Location("Jaffna", "Northern", "Point Pedro", "9.8167", "80.2333", "coastal streets"),
    Location("Jaffna", "Northern", "Chunnakam", "9.7444", "80.0269", "market lanes"),
    Location("Mannar", "Northern", "Mannar", "8.9770", "79.9090", "island and lagoon roads"),
    Location("Kilinochchi", "Northern", "Kilinochchi", "9.3961", "80.3982", "town and tank roads"),
    Location("Mullaitivu", "Northern", "Mullaitivu", "9.2671", "80.8143", "coastal lanes"),
    Location("Vavuniya", "Northern", "Vavuniya", "8.7514", "80.4971", "town and market streets"),
    Location("Trincomalee", "Eastern", "Trincomalee", "8.5778", "81.2289", "harbour streets"),
    Location("Trincomalee", "Eastern", "Nilaveli", "8.6833", "81.1925", "beach-side lanes"),
    Location("Batticaloa", "Eastern", "Batticaloa", "7.7102", "81.6924", "lagoon and fort lanes"),
    Location("Batticaloa", "Eastern", "Kattankudy", "7.6826", "81.7313", "market and lagoon lanes"),
    Location("Ampara", "Eastern", "Pottuvil", "6.8756", "81.8273", "coastal village lanes"),
    Location("Kandy", "Central", "Kandy", "7.2906", "80.6337", "lake and market streets"),
    Location("Galle", "Southern", "Galle", "6.0329", "80.2168", "fort and waterfront lanes"),
    Location("Anuradhapura", "North Central", "Anuradhapura", "8.3122", "80.4131", "tank paths"),
)


@dataclass(frozen=True)
class Photo:
    filename: str
    author: str
    license: str | None = "by-sa"

    @property
    def source(self):
        return "https://commons.wikimedia.org/wiki/File:" + quote(self.filename)

    @property
    def download(self):
        # Wikimedia stores originals by the MD5 of the normalized file name.
        digest = md5(self.filename.encode(), usedforsecurity=False).hexdigest()
        return f"https://upload.wikimedia.org/wikipedia/commons/{digest[0]}/{digest[:2]}/" + quote(
            self.filename
        )

    @property
    def credit(self):
        if self.license is None:
            return f"AI-generated demo illustration by {self.author}. Fictional example."
        license_url = f"https://creativecommons.org/licenses/{self.license}/4.0/"
        return f"Illustrative photo (resized) by {self.author}. {self.source} · {license_url}"

    @property
    def alt_text(self):
        role = "photograph" if self.license else "AI-generated illustration"
        return f"Demo {role} by {self.author}"


PHOTOS = {
    "north": Photo("Jaffna_Fort_Srilanka.jpg", "Hasindu Chamika"),
    "lagoon": Photo("Batticaloa_Lagoon.jpg", "Anton Croos"),
    "beach": Photo("Nilaveli_Beach.jpg", "Dulakshi"),
    "kandy": Photo("The_Kandy_lake.jpg", "Sleepyartz"),
    "room": Photo("Hotel_room_Sri_Lanka_Photo213.jpg", "psychoslave"),
    "bicycle": Photo("Bicycle_in_Sri_Lanka_2023-11-08.jpg", "Muunaa-Selva", "by"),
    "motorcycle": Photo("Royal_Enfield_Classic_350_(2017_Model_Year).jpg", "Samihasib"),
    "scooter": Photo("TVS_Ntorq_front.jpg", "M.parvage", "by"),
    "craft": Photo("Tradtional_Sri_Lankan_Pottery.jpg", "VinPrasad"),
}

ILLUSTRATIONS = {
    key: Photo(f"{key}.jpg", "OpenAI ImageGen for CeylonGo", None)
    for key in ("stay", "activity", "guide", "motorcycle", "scooter", "bicycle", "craft")
}


@dataclass(frozen=True)
class Unit:
    label: str
    capacity: int
    metadata: dict
    hour: int | None = None
    minutes: int = 0


@dataclass(frozen=True)
class Listing:
    key: str
    category: str
    provider: int
    title: str
    description: str
    price: int
    photo: str
    metadata: dict
    units: tuple[Unit, ...] = ()

    @property
    def location(self):
        return LOCATIONS[self.provider]

    @property
    def display_title(self):
        return f"[DEMO] {self.title} ({self.key})"


def catalog(artisan=True):
    listings = []
    plan = (
        ("stay", "homestay", 25), ("activity", "adventure", 30), ("guide", "guide", 15),
        ("motorcycle", "bike", 10), ("scooter", "bike", 10), ("bicycle", "bicycle", 10),
        ("craft", "artisan", 20 if artisan else 0),
    )
    for kind, category, count in plan:
        for index in range(count):
            listings.append(build_listing(kind, category, index))
    return listings


def illustration_catalog(listings):
    return [replace(listing, photo=f"illustration:{listing.key.rsplit('-', 1)[0]}")
            for listing in listings]


def photo_asset(key):
    if key.startswith("illustration:"):
        return ILLUSTRATIONS[key.removeprefix("illustration:")]
    return PHOTOS[key]


def build_listing(kind, category, index):
    offsets = {"motorcycle": 0, "scooter": 4, "bicycle": 8, "craft": 2}
    provider = (index + offsets.get(kind, 0)) % len(LOCATIONS)
    place = LOCATIONS[provider]
    key = f"{kind}-{index + 1:02}"
    photo = "north" if place.province == "Northern" else (
        "lagoon" if place.district == "Batticaloa" else "beach"
    )
    if place.district == "Kandy":
        photo = "kandy"
    if kind == "stay":
        price = 4800 + (index % 8) * 650
        style = ("Courtyard Guesthouse", "Garden Homestay")[index // 15]
        rooms = tuple(Unit(label, capacity, {
            "room_type": label, "nightly_price": str(price + increment),
            "description": "Fictional private guest room for the academic showcase.",
        }) for label, capacity, increment in (("Double", 2, 0), ("Family", 4, 2200)))
        metadata = {
            "property": HomestayMetadata(
                property_type="guesthouse", amenities=["wifi", "fan", "free parking"],
            ).model_dump(mode="json"),
            "province": place.province,
            "address": f"Illustrative pickup area in {place.city}",
            "house_rules": "Quiet hours after 22:00. Smoke-free indoor spaces.",
            "check_in_time": "14:00", "check_out_time": "11:00", "contact": "Demo only",
        }
        return Listing(key, category, provider, f"{place.city} {style}",
                       "Sample private rooms with a shared sitting area and a breakfast option.",
                       price, "room", metadata, rooms)
    if kind == "activity":
        afternoon = index >= 15
        title = "Market and Village Walk" if afternoon else "Nature and Photo Walk"
        activity = ActivityMetadata(
            category="cultural-village" if afternoon else "nature-eco",
            meeting_point=f"{place.city} town center (demo meeting area)", duration_minutes=120,
            max_participants=8, minimum_age=10, highlights=[place.feature.capitalize()],
            included=["Demo itinerary", "Water stop"],
            safety_instructions="Bring drinking water and wear walking shoes.",
            cancellation_policy="Demo only. No cancellation fees are collected.",
        )
        return Listing(key, category, provider, f"{place.city} {title}",
                       f"Two-hour small-group itinerary exploring {place.feature}.",
                       1800 + (index % 7) * 350, photo,
                       {"activity": activity.model_dump(mode="json")},
                       (Unit("Activity group", 8, {}, 16 if afternoon else 11, 120),))
    if kind == "guide":
        profile = GuideProfile(
            categories=["cultural_heritage", "city_village"],
            languages=[{"language_code": "en", "language_name": "English"}],
            service_areas=[{"province": place.province, "district": place.district,
                            "city": place.city, "is_primary": True}],
        )
        packages = []
        for title, hour, length, price in (
            ("Morning orientation walk", 8, 2, 4500),
            ("Afternoon heritage route", 14, 3, 6500),
        ):
            package = GuidePackage(
                title=title, description=f"Sample group itinerary through {place.feature}.",
                duration_hours=length, pricing_type="per_group", price=price,
                max_people=6, meeting_point=f"{place.city} town center (demo meeting area)",
            )
            metadata = package.model_dump(
                mode="json", exclude={"title", "max_people", "is_active"},
            )
            packages.append(Unit(title, 6, {"guide_package": metadata}, hour, length * 60))
        return Listing(key, category, provider, f"{place.city} Local Guide",
                       f"Fictional local guide profile with two routes through {place.feature}.",
                       4500, photo, {"guide": profile.model_dump(mode="json")}, tuple(packages))
    if kind == "craft":
        craft = ("Incised Clay Bowl", "Hand-finished Clay Planter", "Clay Serving Set")[index % 3]
        return Listing(key, category, provider, f"{place.city} {craft}",
                       "Sample craft showcase in terracotta; the photograph illustrates the style.",
                       850 + (index % 8) * 450, "craft", {})

    subtype = "city" if kind == "bicycle" else kind
    rental = RentalMetadata(
        subtype=subtype, transmission="automatic" if kind == "scooter" else "manual",
        seating_capacity=1 if kind == "bicycle" else 2,
        pickup_name=f"{place.city} town center (demo pickup area)",
        specifications="Illustrative vehicle with a basic pre-ride condition check.",
        requirements=("Bring a valid motorcycle license and helmet."
                      if category == "bike" else "Wear a helmet and check the brakes."),
        rental_terms="Return the vehicle to the demo pickup location.",
        cancellation_policy="Demo only. No cancellation fees are collected.",
    )
    price = {"motorcycle": 3200, "scooter": 2400, "bicycle": 850}[kind] + (index % 4) * 200
    return Listing(key, category, provider, f"{place.city} {kind.title()} Hire",
                   "Fictional daily rental for short local trips. Vehicle photo is illustrative.",
                   price, kind, {"rental": rental.model_dump(mode="json")},
                   (Unit("Demo vehicle 01", 1, {}),))


def schedules(unit, first):
    if unit.hour is None:
        start = datetime.combine(first, time.min, SRI_LANKA)
        yield utc_naive(start), utc_naive(start + timedelta(days=DAYS))
        return
    for offset in range(DAYS):
        day = first + timedelta(days=offset)
        start = datetime.combine(day, time(unit.hour), SRI_LANKA)
        yield utc_naive(start), utc_naive(start + timedelta(minutes=unit.minutes))


def print_plan(listings, first, community):
    counts = Counter(item.key.rsplit("-", 1)[0] for item in listings)
    districts = Counter(item.location.district for item in listings)
    slots = sum(DAYS if unit.hour is not None else 1 for item in listings for unit in item.units)
    print(f"Travelers: 10 | Providers: {len(LOCATIONS)} | Listings: {len(listings)}")
    print("Listings by module:", ", ".join(f"{key}={value}" for key, value in counts.items()))
    print(f"Rooms: {counts['stay'] * 2} | Tour packages: {counts['guide'] * 2}")
    print(f"Photos: {len(listings)} | Availability windows: {slots} | Posts: {20 if community else 0}")
    print(f"Local availability: {first} through {first + timedelta(days=DAYS - 1)}")
    print("Listings by district:", ", ".join(f"{key}={value}" for key, value in districts.items()))
    if not counts["craft"]:
        print("Skipped artisan listings: the database does not expose the MVP artisan category.")
    if not community:
        print("Skipped community posts: the MVP community table is unavailable.")


def verify_target(connection):
    if connection.dialect.name != "mssql":
        raise SeedError("The seeder requires SQL Server.")
    database, server, identity, machine, instance = connection.execute(text(
        "SELECT DB_NAME(), @@SERVERNAME, "
        "CONVERT(nvarchar(128), SERVERPROPERTY('ServerName')), "
        "CONVERT(nvarchar(128), SERVERPROPERTY('MachineName')), "
        "CONVERT(nvarchar(128), SERVERPROPERTY('InstanceName'))"
    )).one()
    expected = normalize_server_name(SERVER)
    if (str(database).casefold() != DATABASE.casefold()
            or any(normalize_server_name(value) != expected for value in (
                server, identity, f"{machine}\\{instance}",
            ))):
        raise SeedError(f"Connected identity did not match {SERVER} / {DATABASE}.")


def schema_support(connection):
    inspector = inspect(connection)
    models = (
        MVPUser, MVPProviderProfile, MVPListing, MVPListingVerification,
        MVPListingMedia, MVPInventoryUnit, MVPAvailabilitySlot, MVPBooking,
    )
    for model in models:
        table = model.__table__
        if not inspector.has_table(table.name, schema="dbo"):
            raise SeedError(f"Required Academic MVP table missing: dbo.{table.name}.")
        actual = {column["name"] for column in inspector.get_columns(table.name, schema="dbo")}
        missing = set(table.columns.keys()) - actual
        if missing:
            raise SeedError(f"Required MVP columns missing on {table.name}: {', '.join(sorted(missing))}.")
    category_rule = connection.scalar(text(
        "SELECT definition FROM sys.check_constraints "
        "WHERE parent_object_id = OBJECT_ID(N'dbo.listings', N'U') "
        "AND name = N'ck_listings_category'"
    ))
    artisan = "'artisan'" in (category_rule or "").casefold()
    community = inspector.has_table("community_posts", schema="dbo")
    if community:
        actual = {
            column["name"] for column in inspector.get_columns("community_posts", schema="dbo")
        }
        community = set(MVPCommunityPost.__table__.columns.keys()).issubset(actual)
    return artisan, community


def connect_engine():
    from app.database.connection import DATABASE_URL

    if str(DATABASE_URL.database).casefold() != DATABASE.casefold():
        raise SeedError(f"Configured database must be {DATABASE}.")
    aliases = {normalize_server_name(SERVER), r".\sqlexpress", r"localhost\sqlexpress"}
    if normalize_server_name(DATABASE_URL.host) not in aliases:
        raise SeedError(f"Set DB_SERVER to {SERVER} or a local SQLEXPRESS alias.")
    return create_engine(
        DATABASE_URL,
        connect_args={"timeout": 6},
        hide_parameters=True,
        execution_options={"schema_translate_map": {None: "dbo"}},
    )


def lock_batch(connection):
    result = connection.execute(text(
        "IF @@TRANCOUNT = 0 BEGIN TRANSACTION; "
        "DECLARE @result int; "
        "EXEC @result = sys.sp_getapplock @Resource = 'ceylongo.academic-mvp.seed.v1', "
        "@LockMode = 'Exclusive', @LockOwner = 'Transaction', @LockTimeout = 8000; "
        "SELECT @result;"
    )).scalar_one()
    if result is None or result < 0:
        raise SeedError("Another seed operation holds the SQL Server transaction lock.")


def read_receipt(path, create=False):
    if not path.exists():
        if not create:
            raise SeedError("Seed receipt not found. Cleanup requires the original receipt.")
        path.parent.mkdir(parents=True, exist_ok=True)
        receipt = secrets.token_hex(16)
        with path.open("x", encoding="ascii") as stream:
            json.dump({"version": 1, "server": SERVER, "database": DATABASE,
                       "batch": receipt, "email_domain": DOMAIN}, stream)
        return receipt
    data = json.loads(path.read_text(encoding="ascii"))
    batch = data.get("batch")
    if (data.get("version") != 1 or data.get("server") != SERVER
            or data.get("database") != DATABASE
            or data.get("email_domain", LEGACY_DOMAIN) not in {DOMAIN, LEGACY_DOMAIN}
            or not re.fullmatch(r"[a-f0-9]{32}", batch or "")):
        raise SeedError("Seed receipt is invalid for this server and database.")
    return batch


def download_photos(keys, photo_dir=None):
    result = {}
    for key in sorted(keys):
        asset = photo_asset(key)
        try:
            limit = 8 * 1024 * 1024
            if photo_dir is None:
                if asset.license is None:
                    raise SeedError("Demo illustrations require a local source directory.")
                request = Request(asset.download, headers={
                    "User-Agent": "CeylonGoAcademicMVPSeeder/1.0 (Commons photo attribution)",
                })
                with urlopen(request, timeout=8) as response:
                    if urlsplit(response.url).hostname != "upload.wikimedia.org":
                        raise SeedError("Unexpected Wikimedia download host.")
                    raw = response.read(limit + 1)
            else:
                with (photo_dir / asset.filename).open("rb") as source:
                    raw = source.read(limit + 1)
        except OSError as error:
            status = getattr(error, "code", None)
            detail = f" (HTTP {status})" if status is not None else ""
            raise SeedError(f"Could not read {asset.filename}{detail}.") from None
        if len(raw) > limit:
            raise SeedError(f"Photograph exceeds the download limit: {asset.filename}.")
        with Image.open(BytesIO(raw)) as image:
            if image.format != "JPEG" or image.width * image.height > 25_000_000:
                raise SeedError(f"Unexpected photograph format or size: {asset.filename}.")
            photo = ImageOps.exif_transpose(image).convert("RGB")
            photo.thumbnail((1400, 1050))
            buffer = BytesIO()
            photo.save(buffer, "JPEG", quality=86, optimize=True)
            result[key] = buffer.getvalue()
    return result


def publish_photo(path, content):
    if path.exists():
        if path.is_symlink() or path.read_bytes() != content:
            raise SeedError("A different file occupies a reserved demo photo path.")
        return
    with path.open("xb") as stream:
        stream.write(content)


def demo_email(role, number):
    return f"ceylongo.{role}{number:02}@{DOMAIN}"


def expected_accounts(batch):
    for role, count in (("traveler", 10), ("provider", len(LOCATIONS))):
        for number in range(1, count + 1):
            email = demo_email(role, number)
            name = f"[DEMO] {role.title()} {number:02} [{batch[:12]}]"
            yield role, number, email, name


def known_accounts(db, batch):
    expected = {email: (role, name) for role, _, email, name in expected_accounts(batch)}
    legacy = {
        f"{role}{number:02}@{LEGACY_DOMAIN}": (role, name)
        for role, number, _, name in expected_accounts(batch)
    }
    users = db.scalars(select(MVPUser).where(
        func.lower(MVPUser.email).in_([*expected, *legacy])
    )).all()
    known = {}
    for user in users:
        address = user.email.casefold()
        identity = (user.role, user.full_name)
        if address in legacy:
            if identity == legacy[address]:
                raise SeedError("Legacy demo emails must be repaired before applying or cleaning up.")
            continue
        if identity != expected[address]:
            raise SeedError(
                f"Account collision for {user.email}. Existing account preserved."
            )
        known[address] = user
    return known


def password_for(role):
    if not sys.stdin.isatty():
        raise SeedError("Run in an interactive terminal to set the demo passwords.")
    secret = getpass(f"New {role} demo password (at least 16 characters): ")
    if len(secret) < 16 or secret != getpass(f"Confirm {role} demo password: "):
        raise SeedError("Password is too short or confirmation differs.")
    return secret


def seed_accounts(db, batch, get_password):
    users = known_accounts(db, batch)
    passwords = {}
    travelers, providers = [], []
    created = 0
    for role, number, email, name in expected_accounts(batch):
        user = users.get(email)
        if user is None:
            if role not in passwords:
                passwords[role] = get_password(role)
            user = MVPUser(email=email, role=role, full_name=name,
                           hashed_password=hash_password(passwords[role]), is_active=True)
            db.add(user)
            created += 1
        if role == "traveler":
            travelers.append(user)
            continue
        profile = user.provider_profile
        if profile is None:
            if email in users:
                raise SeedError("Demo provider profile has changed. Existing account preserved.")
            place = LOCATIONS[number - 1]
            profile = MVPProviderProfile(
                user=user, business_name=f"[DEMO] {place.city} Showcase {number:02}",
                district=place.district, phone="DEMO ONLY", verification_status="approved",
                decision_reason=NOTICE,
            )
            db.add(profile)
        providers.append(profile)
    db.flush()
    return travelers, providers, created


def known_listings(db, batch):
    extract = func.json_value if db.get_bind().dialect.name == "mssql" else func.json_extract
    rows = db.execute(select(MVPListing, MVPListingVerification.details).join(
        MVPListingVerification
    ).where(extract(MVPListingVerification.details, "$.academic_seed.batch") == batch))
    listings = {}
    for listing, raw in rows:
        marker = json.loads(raw)["academic_seed"]
        key = marker["key"]
        if key in listings:
            raise SeedError("Duplicate demo listing marker. Existing rows preserved.")
        listings[key] = listing
    return listings


def schedule_unit(db, unit, listing, spec, first):
    first_start, _ = next(schedules(spec, first))
    existing = list(db.scalars(select(MVPAvailabilitySlot).where(
        MVPAvailabilitySlot.unit_id == unit.id, MVPAvailabilitySlot.ends_at > first_start,
    )))
    starts = {slot.starts_at for slot in existing}
    rows = []
    for start, end in schedules(spec, first):
        if start in starts:
            continue
        if spec.hour is not None and any(
            slot.starts_at < end and slot.ends_at > start for slot in existing
        ):
            continue
        rows.append({
            "unit_id": unit.id, "listing_id": listing.id, "capacity": unit.capacity,
            "starts_at": start, "ends_at": end, "is_open": True,
        })
    if rows:
        db.execute(insert(MVPAvailabilitySlot), rows)
    return len(rows)


def create_listing(db, spec, provider, batch, uploads, photos):
    place = spec.location
    photo = photo_asset(spec.photo)
    listing = MVPListing(
        provider_id=provider.id, category=spec.category, title=spec.display_title,
        description=f"{spec.description}\n\n{NOTICE}\n\n{photo.credit}",
        district=place.district, locality=place.city,
        latitude=Decimal(place.latitude), longitude=Decimal(place.longitude),
        price=Decimal(spec.price), currency="LKR", approval_status="approved",
        is_active=True, decision_reason=NOTICE,
    )
    db.add(listing)
    db.flush()
    filename = f"academic_seed_{batch}_{spec.key}.jpg"
    publish_photo(uploads / filename, photos[spec.photo])
    media = MVPListingMedia(
        listing_id=listing.id, storage_key=PHOTO_PREFIX + filename, sort_order=0,
        is_visible=True, alt_text=photo.alt_text,
    )
    db.add(media)
    db.flush()
    data = dict(spec.metadata)
    property_data = data.pop("property", None)
    if property_data:
        listing.homestay_metadata = HomestayMetadata(
            **{**property_data, "cover_photo_id": media.id}
        ).model_dump_json()
    data.update({
        "academic_seed": {"batch": batch, "key": spec.key},
        "cover_photo_id": media.id,
    })
    db.add(MVPListingVerification(listing_id=listing.id, details=json.dumps(data)))
    for unit in spec.units:
        metadata = {**unit.metadata, "academic_seed": batch}
        if spec.category == "homestay":
            metadata["photo_ids"] = [media.id]
        db.add(MVPInventoryUnit(
            listing=listing, provider_id=provider.id, label=unit.label,
            capacity=unit.capacity, is_active=True, room_metadata=json.dumps(metadata),
        ))
    db.flush()
    return listing


def post_prefix(batch, index):
    return f"[DEMO post {index + 1:02} {batch[:12]}]"


def post_query(traveler_id, prefix):
    return select(MVPCommunityPost).where(
        MVPCommunityPost.author_id == traveler_id,
        func.substring(MVPCommunityPost.body, 1, len(prefix)) == prefix,
    )


def seed_posts(db, travelers, batch):
    created = 0
    for index in range(20):
        place = LOCATIONS[index % len(LOCATIONS)]
        traveler = travelers[index % len(travelers)]
        prefix = post_prefix(batch, index)
        if db.scalar(post_query(traveler.id, prefix).limit(1)) is not None:
            continue
        discussion = (
            f"Planning a sample visit to {place.city}. What would you add to an itinerary "
            f"around the {place.feature}?"
        )
        db.add(MVPCommunityPost(author_id=traveler.id, body=f"{prefix} {discussion}"))
        created += 1
    return created


def apply_seed(db, listings, first, community, batch, photos, uploads, get_password):
    travelers, providers, accounts_created = seed_accounts(db, batch, get_password)
    existing = known_listings(db, batch)
    listing_count, slot_count = 0, 0
    for spec in listings:
        provider = providers[spec.provider]
        listing = existing.get(spec.key)
        if listing is None:
            collision = db.scalar(select(MVPListing.id).where(
                MVPListing.title == spec.display_title,
            ).limit(1))
            if collision is not None:
                raise SeedError(f"Listing title collision for {spec.key}.")
            listing = create_listing(db, spec, provider, batch, uploads, photos)
            listing_count += 1
        elif listing.provider_id != provider.id or listing.category != spec.category:
            raise SeedError(f"Demo listing ownership or category changed: {spec.key}.")
        else:
            listing = locked_listing(db, listing.id)
            publish_photo(uploads / f"academic_seed_{batch}_{spec.key}.jpg", photos[spec.photo])
        if not listing.is_active:
            continue
        units = {unit.label: unit for unit in listing.units}
        for planned in spec.units:
            unit = units.get(planned.label)
            if (unit is not None and unit.is_active and unit.capacity == planned.capacity
                    and json.loads(unit.room_metadata or "{}").get("academic_seed") == batch):
                slot_count += schedule_unit(db, unit, listing, planned, first)
    posts = seed_posts(db, travelers, batch) if community else 0
    return {"accounts": accounts_created, "listings": listing_count, "slots": slot_count, "posts": posts}


def owned_posts(db, users, batch):
    posts = []
    for index in range(20):
        number = index % 10 + 1
        user = users.get(demo_email("traveler", number))
        if user is None:
            continue
        records = db.scalars(post_query(user.id, post_prefix(batch, index))).all()
        if len(records) > 1:
            raise SeedError("Duplicate demo post marker. Existing rows preserved.")
        posts.extend(records)
    return posts


def retire_seed(db, batch, community):
    users = known_accounts(db, batch)
    listings = known_listings(db, batch)
    expected = {spec.key: spec for spec in catalog()}
    providers = {
        email: user.provider_profile for email, user in users.items() if user.role == "provider"
    }
    for key, listing in listings.items():
        spec = expected.get(key)
        if spec is None:
            raise SeedError("Unknown demo listing marker. Cleanup stopped.")
        provider = providers.get(demo_email("provider", spec.provider + 1))
        if provider is None or listing.provider_id != provider.id or listing.category != spec.category:
            raise SeedError("Demo listing no longer matches its seeded owner or category.")
    listing_ids = [listing.id for listing in listings.values()]
    for listing_id in sorted(listing_ids):
        locked_listing(db, listing_id)
    provider_ids = [profile.id for profile in providers.values() if profile is not None]
    if db.scalar(select(MVPListing.id).where(
        MVPListing.provider_id.in_(provider_ids), MVPListing.id.not_in(listing_ids),
    ).limit(1)) is not None:
        raise SeedError("A demo provider owns unrelated listings. Cleanup stopped.")
    if db.scalar(select(MVPBooking.id).where(
        MVPBooking.status.in_(("pending", "confirmed")),
        or_(MVPBooking.listing_id.in_(listing_ids),
            MVPBooking.traveler_id.in_([user.id for user in users.values()])),
    ).limit(1)) is not None:
        raise SeedError("Pending or confirmed bookings involve this batch. Cleanup stopped.")
    posts = owned_posts(db, users, batch) if community else []
    retired = {
        "accounts": sum(user.is_active for user in users.values()),
        "listings": sum(listing.is_active for listing in listings.values()),
        "posts": sum(post.visibility == "visible" for post in posts),
    }
    for user in users.values():
        if user.is_active:
            user.is_active = False
            user.auth_version += 1
    for listing in listings.values():
        listing.is_active = False
    for post in posts:
        if post.visibility == "visible":
            post.visibility = "hidden"
    return retired


def run(args):
    first = datetime.now(SRI_LANKA).date() + timedelta(days=1)
    engine = None
    try:
        artisan, community = True, True
        existing_emails = set()
        if args.verify_live or args.apply or args.cleanup:
            engine = connect_engine()
            with engine.connect() as connection:
                verify_target(connection)
                artisan, community = schema_support(connection)
                if args.apply and args.receipt.exists():
                    batch = read_receipt(args.receipt)
                    with Session(bind=connection) as db:
                        existing_emails = set(known_accounts(db, batch))
            print(f"Verified SQL Server target: {SERVER} / {DATABASE}")
        listings = catalog(artisan)
        if args.illustrations:
            listings = illustration_catalog(listings)
        if not args.cleanup:
            print_plan(listings, first, community)
        photos = {}
        if args.check_photos or args.apply:
            photo_dir = args.photo_dir
            if photo_dir is None and args.illustrations:
                photo_dir = LOCAL_ILLUSTRATIONS
            elif photo_dir is None and LOCAL_PHOTOS.is_dir():
                photo_dir = LOCAL_PHOTOS
            photos = download_photos({item.photo for item in listings}, photo_dir)
            source = "local AI-generated demo illustrations" if args.illustrations else (
                "licensed source photographs"
            )
            print(f"Validated {len(photos)} {source}.")
        if not args.apply and not args.cleanup:
            print("Dry run. No database records or upload files were written.")
            return
        passwords = {}
        if args.apply:
            for role in ("traveler", "provider"):
                if any(expected_role == role and email not in existing_emails
                       for expected_role, _, email, _ in expected_accounts("0" * 32)):
                    passwords[role] = password_for(role)

        def new_password(role):
            if role not in passwords:
                raise SeedError("Demo account set changed during preflight. Run the seeder again.")
            return passwords[role]

        with engine.execution_options(isolation_level="SERIALIZABLE").begin() as connection:
            verify_target(connection)
            lock_batch(connection)
            batch = read_receipt(args.receipt, create=args.apply)
            with Session(bind=connection) as db:
                if args.cleanup:
                    counts = retire_seed(db, batch, community)
                else:
                    from app.routers.provider_registration import get_upload_directory

                    uploads = Path(get_upload_directory()).resolve()
                    counts = apply_seed(
                        db, listings, first, community, batch, photos, uploads, new_password,
                    )
                db.flush()
        print(("Retired" if args.cleanup else "Inserted") + f" demo records: {counts}")
    finally:
        if engine is not None:
            engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Print the plan (default).")
    mode.add_argument("--apply", action="store_true", help="Insert demo data into CeylonGoDB.")
    mode.add_argument("--cleanup", action="store_true", help="Retire the original demo batch.")
    mode.add_argument("--list-photos", action="store_true", help="Print Wikimedia originals and credits.")
    parser.add_argument("--verify-live", action="store_true", help="Read SQL Server identity and schema.")
    parser.add_argument("--check-photos", action="store_true", help="Download and decode source photos in memory.")
    parser.add_argument(
        "--photo-dir", type=Path, help="Use source files from this directory.",
    )
    parser.add_argument(
        "--illustrations", action="store_true",
        help="Use the local AI-generated demo illustration set instead of Wikimedia photos.",
    )
    parser.add_argument("--receipt", type=Path, default=RECEIPT, help="Keep this receipt for cleanup.")
    args = parser.parse_args()
    if args.list_photos:
        assets = ILLUSTRATIONS if args.illustrations else PHOTOS
        for photo in assets.values():
            if photo.license is None:
                print(f"{photo.filename} | {photo.credit}")
            else:
                print(f"{photo.filename} | {photo.author} | CC {photo.license.upper()} 4.0 | {photo.source}")
        return 0
    try:
        run(args)
    except SeedError as error:
        print(error, file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("SQL Server connection or transaction failed. No seed success recorded.", file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print("Could not read the receipt, retrieve photos, or stage upload files.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
