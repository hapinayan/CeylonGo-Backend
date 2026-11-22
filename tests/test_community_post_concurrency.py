from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.models.community import CommunityComment, CommunityPost, CommunityPostMedia, CommunityReport
from app.models.shared import AdminAuditLog
from app.models.user import Base, User
from app.schemas.community import AdminCommunityPostModerationRequest, CommunityPostUpdate, CommunityReportCreate
from app.services import community_moderation, community_posts


@pytest.fixture
def race_db(tmp_path, monkeypatch):
    monkeypatch.setenv("COMMUNITY_UPLOAD_DIR", str(tmp_path))
    engine = create_engine(f"sqlite:///{(tmp_path / 'race.sqlite').as_posix()}", connect_args={"timeout": 15})

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.create_function("sysutcdatetime", 0, lambda: "2026-01-01 00:00:00")

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for user_id, role in ((1, "traveler"), (2, "content_moderator"), (3, "traveler")):
            db.add(User(id=user_id, role=role, full_name=role, email=f"user{user_id}@example.com"))
        db.flush()
        db.add(CommunityPost(id=1, author_id=1, content="Evidence", is_featured=True))
        db.flush()
        db.add_all([
            CommunityComment(id=1, post_id=1, author_id=1, content="Comment evidence"),
            CommunityPostMedia(id=1, post_id=1, media_url="/api/v1/uploads/community/evidence.jpg"),
        ])
        db.commit()
    media_file = tmp_path / "evidence.jpg"
    media_file.write_bytes(b"test evidence")
    community_moderation.reset_community_report_limiter()
    try:
        yield engine, media_file
    finally:
        engine.dispose()
        community_moderation.reset_community_report_limiter()


def run_interleaved(engine, monkeypatch, commit_module, first, second):
    first_holds_lock, second_at_lock, allow_commit = Event(), Event(), Event()
    commit = commit_module._commit

    def hold_first_commit(db):
        if db.info["operation"] == "first":
            first_holds_lock.set()
            assert allow_commit.wait(10)
        commit(db)

    def observe_waiter(_conn, _cursor, statement, _params, context, _many):
        if context.execution_options.get("race_waiter") and statement.startswith("UPDATE community_posts"):
            second_at_lock.set()

    def invoke(operation, label):
        with Session(engine) as db:
            db.info["operation"] = label
            db.connection(execution_options={"race_waiter": label == "second"})
            try:
                return operation(db)
            except HTTPException as rejected:
                return rejected.status_code

    monkeypatch.setattr(commit_module, "_commit", hold_first_commit)
    event.listen(engine, "before_cursor_execute", observe_waiter)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            winner = workers.submit(invoke, first, "first")
            try:
                assert first_holds_lock.wait(10)
                waiter = workers.submit(invoke, second, "second")
                assert second_at_lock.wait(10)
                assert not waiter.done()
            finally:
                allow_commit.set()
            return winner.result(timeout=15), waiter.result(timeout=15)
    finally:
        event.remove(engine, "before_cursor_execute", observe_waiter)


@pytest.mark.parametrize("hide_first", [True, False])
def test_author_edit_and_moderator_hide_are_serialized(race_db, monkeypatch, hide_first):
    engine, _ = race_db

    def hide(db):
        return community_moderation.moderate_admin_community_post(
            db, post_id=1, admin_user=db.get(User, 2),
            payload=AdminCommunityPostModerationRequest(is_published=False, reason="Moderation"),
        )

    def edit(db):
        cached = db.get(CommunityPost, 1)
        assert cached.is_published
        return community_posts.update_community_post(
            db, post_id=1, current_user=db.get(User, 1),
            payload=CommunityPostUpdate(content="Stale edit", is_published=True),
        )

    if hide_first:
        hidden, rejected = run_interleaved(engine, monkeypatch, community_moderation, hide, edit)
        assert rejected == 403
    else:
        edited, hidden = run_interleaved(engine, monkeypatch, community_posts, edit, hide)
        assert edited.content == "Stale edit"
    assert hidden.is_published is False
    with Session(engine) as db:
        post = db.get(CommunityPost, 1)
        assert (post.content, post.is_published, post.is_featured, post.moderation_status) == (
            "Evidence" if hide_first else "Stale edit", False, False, "hidden_by_admin",
        )
        assert db.query(AdminAuditLog).one().action == "community_post_hide"
        with pytest.raises(HTTPException) as again:
            community_posts.update_community_post(db, post_id=1, current_user=db.get(User, 1),
                                                  payload=CommunityPostUpdate(is_published=True))
        assert again.value.status_code == 403


@pytest.mark.parametrize("target", ["post", "comment"])
@pytest.mark.parametrize("report_first", [True, False])
def test_post_delete_and_report_are_serialized(race_db, monkeypatch, target, report_first):
    engine, media_file = race_db

    def report(db):
        return community_moderation.submit_community_report(
            db, current_user=db.get(User, 3),
            payload=CommunityReportCreate(entity_type=target, entity_id=1, report_reason="Evidence"),
        )

    def delete(db):
        return community_posts.delete_community_post(db, post_id=1, current_user=db.get(User, 2))

    if report_first:
        accepted, deleted = run_interleaved(engine, monkeypatch, community_moderation, report, delete)
        assert accepted.report_status == "pending"
        assert deleted.deletion_mode == "soft_unpublished_moderation_preserved"
    else:
        deleted, rejected = run_interleaved(engine, monkeypatch, community_posts, delete, report)
        assert deleted.deletion_mode == "hard_deleted"
        assert rejected == 404

    with Session(engine) as db:
        assert db.query(CommunityReport).count() == int(report_first)
        assert (db.get(CommunityPost, 1) is not None) is report_first
        assert (db.get(CommunityComment, 1) is not None) is report_first
        assert (db.get(CommunityPostMedia, 1) is not None) is report_first
        assert media_file.exists() is report_first
        if report_first:
            assert db.get(CommunityPost, 1).is_published is False
            assert media_file.read_bytes() == b"test evidence"
            assert delete(db).deletion_mode == "soft_unpublished_moderation_preserved"
        assert db.query(AdminAuditLog).one().action == (
            "community_post_hide" if report_first else "community_post_delete"
        )


def test_concurrent_duplicate_reports_create_one_open_report(race_db, monkeypatch):
    engine, _ = race_db

    def report(db):
        return community_moderation.submit_community_report(
            db, current_user=db.get(User, 3),
            payload=CommunityReportCreate(entity_type="post", entity_id=1, report_reason="Evidence"),
        )

    accepted, rejected = run_interleaved(engine, monkeypatch, community_moderation, report, report)
    assert accepted.report_status == "pending"
    assert rejected == 409
    with Session(engine) as db:
        assert db.query(CommunityReport).one().id == accepted.id
