import hashlib
import json
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.mvp import MVPProviderDocument, MVPProviderProfile, MVPUser
from app.routers.provider_registration import get_upload_directory, validate_and_save_image
from app.schemas.provider_registration import DraftResponse, RegistrationSubmitResponse
from app.services.mvp_provider_otp import PASSWORD_UNSET, consume, utc_now

DOCUMENTS = ("selfie", "nic_front", "nic_back")


def require_draft_storage(db):
    if db.get_bind().dialect.name == "mssql" and db.scalar(text(
        "SELECT COL_LENGTH('dbo.provider_profiles', 'registration_details')"
    )) is None:
        raise HTTPException(503, "Provider draft storage requires the MVP schema upgrade.")


def draft_data(profile):
    return json.loads(profile.registration_details) if profile and profile.registration_details else {}


def draft_response(email, profile):
    details = draft_data(profile)
    personal = details.get("personal", {})
    business = details.get("business", {})
    documents = details.get("documents", {})
    complete = all(name in documents for name in DOCUMENTS)
    return DraftResponse(
        email=email, **personal, **business,
        **{f"{name}_uploaded": name in documents for name in DOCUMENTS},
        personal_completed=bool(personal), business_completed=bool(business),
        identity_completed=complete,
        current_step=("review" if complete else "identity" if business else
                      "business" if personal else "personal"),
        updated_at=profile.updated_at if profile else None,
    )


def editable(profile):
    if profile and profile.verification_status not in {"draft", "rejected"}:
        raise HTTPException(403, "This provider application is not editable")


def _save(db, profile, details):
    profile.registration_details = json.dumps(details, ensure_ascii=False)
    profile.updated_at = utc_now()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "The provider application changed. Retry the operation.") from None


def save_personal(db, challenge, user, profile, payload):
    editable(profile)
    if len(payload.full_name) > 120 or len(payload.permanent_district) > 80:
        raise HTTPException(422, "Name or district exceeds the MVP field limit")
    if user is None:
        user = MVPUser(email=challenge.email, role="provider", is_active=False,
                       hashed_password=PASSWORD_UNSET, full_name=payload.full_name)
        db.add(user)
    user.full_name, user.phone = payload.full_name, payload.phone
    if profile is None:
        profile = MVPProviderProfile(
            user=user, business_name="", phone=payload.phone,
            district=payload.permanent_district, verification_status="draft",
        )
        db.add(profile)
    details = draft_data(profile)
    details["personal"] = payload.model_dump(mode="json")
    _save(db, profile, details)
    return draft_response(challenge.email, profile)


def save_business(db, challenge, profile, payload):
    editable(profile)
    details = draft_data(profile)
    personal = details.get("personal")
    if not personal:
        raise HTTPException(409, "Complete personal details first")
    district = personal["permanent_district"] if payload.same_as_permanent else payload.business_district
    if not district or len(district) > 80:
        raise HTTPException(422, "A valid business district is required")
    profile.business_name = payload.business_name
    profile.district = district
    profile.phone = payload.secondary_phone or personal["phone"]
    details["business"] = payload.model_dump(mode="json")
    _save(db, profile, details)
    return draft_response(challenge.email, profile)


def save_documents(db, challenge, profile, files):
    editable(profile)
    details = draft_data(profile)
    if not details.get("business"):
        raise HTTPException(409, "Complete business details first")
    documents = details.setdefault("documents", {})
    saved = []
    try:
        for name, file in files.items():
            if file is None:
                continue
            filename = validate_and_save_image(file, name, None)
            path = Path(get_upload_directory()) / filename
            saved.append(path)
            db.add(MVPProviderDocument(
                provider_id=profile.id, kind="identity", status="pending",
                storage_key=filename, sha256=hashlib.sha256(path.read_bytes()).digest(),
            ))
            documents[name] = filename
        if not saved:
            raise HTTPException(422, "Supply at least one identity image")
        _save(db, profile, details)
    except Exception:
        db.rollback()
        for path in saved:
            path.unlink(missing_ok=True)
        raise
    return draft_response(challenge.email, profile)


def submit(db, challenge, profile):
    editable(profile)
    draft = draft_response(challenge.email, profile)
    if not (draft.personal_completed and draft.business_completed and draft.identity_completed):
        raise HTTPException(409, "Complete personal details, business details and all identity images")
    consume(db, challenge)
    profile.verification_status = "pending"
    profile.submitted_at = profile.updated_at = utc_now()
    db.commit()
    return RegistrationSubmitResponse()
