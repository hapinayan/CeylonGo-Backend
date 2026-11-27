"""Consent and first-party activity for the Academic MVP recommender."""

import hashlib
import json
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.orm import undefer

from app.models.mvp_account import TravelerSettings
from app.models.mvp_recommendations import TravelerActivityEvent as Event
from app.schemas.mvp_recommendations import ActivityResult, ViewEvent
from app.services import mvp_account as account

RETENTION = timedelta(days=90)
DEDUPE_WINDOW = timedelta(minutes=10)
RATE_WINDOW = timedelta(minutes=1)
RATE_LIMIT = 20
HISTORY_LIMIT = 100


def installed(db):
    if db.get_bind().dialect.name == "mssql":
        return bool(
            db.scalar(
                text(
                    "SELECT CASE "
                    "WHEN OBJECT_ID(N'dbo.traveler_activity_events', N'U') IS NOT NULL "
                    "AND COL_LENGTH(N'dbo.traveler_settings', "
                    "N'behavioral_personalization') IS NOT NULL THEN 1 ELSE 0 END"
                )
            )
        )
    return inspect(db.connection()).has_table(Event.__tablename__)


def require_schema(db):
    if not installed(db):
        raise HTTPException(503, "Recommendation activity requires MVP schema 1.0.9")


def consent(db, traveler_id):
    return bool(
        db.scalar(
            select(TravelerSettings.behavioral_personalization).where(
                TravelerSettings.user_id == traveler_id,
            )
        )
    )


def preferences_and_consent(db, traveler_id):
    if not account.available(db):
        return "{}", False
    query = select(TravelerSettings).where(TravelerSettings.user_id == traveler_id)
    enabled = installed(db)
    if enabled:
        query = query.options(undefer(TravelerSettings.behavioral_personalization))
    settings = db.scalar(query)
    return (
        settings.preferences if settings else "{}",
        bool(settings and enabled and settings.behavioral_personalization),
    )


def set_consent(db, traveler_id, enabled):
    row = account.settings(db, traveler_id, write=True)
    row.behavioral_personalization = enabled
    db.commit()


def history(db, traveler_id, now):
    # Give each event type its own bound so a busy search history cannot hide views.
    return [
        event
        for kind in ("search", "view")
        for event in db.scalars(
            select(Event)
            .where(
                Event.traveler_id == traveler_id,
                Event.event_type == kind,
                Event.created_at > now - RETENTION,
            )
            .order_by(Event.created_at.desc(), Event.id.desc())
            .limit(HISTORY_LIMIT)
        )
    ]


def clear(db, traveler_id):
    account.lock_user(db, traveler_id)
    result = db.execute(delete(Event).where(Event.traveler_id == traveler_id))
    db.commit()
    return result.rowcount


def purge_expired(db, now):
    return db.execute(delete(Event).where(Event.created_at < now - RETENTION)).rowcount


def record(db, traveler_id, payload, eligible_category):
    # The existing SQL Server user lock serializes consent, writes and history clearing.
    account.lock_user(db, traveler_id)
    if not consent(db, traveler_id):
        return ActivityResult(recorded=False, reason="personalization_disabled")

    data = payload.model_dump(exclude_none=True)
    if isinstance(payload, ViewEvent):
        category = eligible_category(payload.listing_id)
        if category is None:
            raise HTTPException(404, "Published listing not found")
        if payload.category is not None and payload.category != category:
            raise HTTPException(422, "Category does not match the listing")
        data["category"] = category
    elif "filters" in data:
        data["filters"] = json.dumps(data["filters"], sort_keys=True)
        if data["filters"] == "{}":
            del data["filters"]

    fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).digest()
    now = account.now()
    owner = Event.traveler_id == traveler_id
    duplicate = db.scalar(
        select(Event.id)
        .where(
            owner,
            Event.fingerprint == fingerprint,
            Event.created_at > now - DEDUPE_WINDOW,
        )
        .order_by(Event.id)
        .limit(1)
    )
    if duplicate is not None:
        return ActivityResult(recorded=False, reason="duplicate")
    count = db.scalar(
        select(func.count())
        .select_from(Event)
        .where(
            owner,
            Event.created_at > now - RATE_WINDOW,
        )
    )
    if count >= RATE_LIMIT:
        raise HTTPException(
            429, "Activity rate limit reached", headers={"Retry-After": "60"}
        )
    db.add(
        Event(traveler_id=traveler_id, created_at=now, fingerprint=fingerprint, **data)
    )
    db.commit()
    return ActivityResult(recorded=True, reason="recorded")
