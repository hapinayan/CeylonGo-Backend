import hashlib
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import false, text
from sqlalchemy.orm import Session

from app.models.provider_registration_authorization import (
    ProviderRegistrationAuthorization,
)


def lock_onboarding(db: Session, value: str, *, scope: str = "email") -> None:
    """Serialize onboarding writers for one identity until the SQL transaction ends."""
    if db.get_bind().dialect.name != "mssql":
        return
    digest = hashlib.sha256(value.strip().casefold().encode()).hexdigest()
    result = db.execute(
        text(
            "SET NOCOUNT ON; DECLARE @result int; "
            "EXEC @result = sp_getapplock @Resource=:resource, @LockMode='Exclusive', "
            "@LockOwner='Transaction', @LockTimeout=4000; SET NOCOUNT OFF; SELECT @result"
        ),
        {"resource": f"provider-onboarding:{scope}:{digest}"},
    ).scalar()
    if result is None or result < 0:
        db.rollback()
        raise HTTPException(409, detail="Provider application is busy. Please retry.")


def active_authorization(db: Session, record):
    model = ProviderRegistrationAuthorization
    return db.query(model).filter(
        model.id == record.id,
        model.email == record.email,
        model.purpose == record.purpose,
        model.is_consumed == false(),
        model.is_revoked == false(),
        model.expires_at > datetime.now(UTC),
    )


def require_live_authorization(db: Session, record, *, consume: bool = False) -> None:
    query = active_authorization(db, record)
    if consume:
        valid = (
            query.update(
                {"is_consumed": True, "consumed_at": datetime.now(UTC)},
                synchronize_session=False,
            )
            == 1
        )
    else:
        valid = (
            query.with_entities(ProviderRegistrationAuthorization.id).first()
            is not None
        )
    if not valid:
        db.rollback()
        raise HTTPException(
            401,
            detail="Provider authorization is expired, revoked, or already used.",
            headers={"WWW-Authenticate": "Bearer"},
        )
