"""
Local Tour Guide Module 05 — Schema-Guarded Extensions for Proposed Migration 034:
- TR-18: Public WhatsApp contact with explicit opt-in & traveler privacy
- PR-17: Provider WhatsApp number & visibility opt-in management
- GS-01: Admin-verified years of guide experience (evidence-gated)
- GS-03: Package meeting instructions
- GS-06: Guide-authored package special requirements (fitness, clothing, equipment, other)

SAFETY GUARANTEE:
- Never adds unmigrated columns to SQLAlchemy ORM models (TourGuide / TourGuidePackage).
- Every database read/write against Migration 034 columns requires BOTH:
  1. Environment flag TOUR_GUIDE_ENABLE_M034_FIELDS=true (defaults to false), AND
  2. Live SQLAlchemy inspect() verification that the target columns exist in the active DB.
- Against an existing Migration 033 database (or when the flag is disabled), zero queries
  reference Migration 034 columns; read paths return safe defaults and write paths return
  HTTP 503 with a clear Database Coordinator pending-migration message.
"""

from __future__ import annotations

import json
import os
import re

from fastapi import HTTPException, status
from sqlalchemy import inspect as sa_inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.notification_service import create_notification
from app.models.tour_guide import TourGuide, TourGuideCredential, TourGuidePackage
from app.models.user import User
from app.schemas.tour_guide import (
    AdminVerifiedExperienceResponse,
    AdminVerifiedExperienceUpdate,
    PackageExtendedRequirementsResponse,
    PackageExtendedRequirementsUpdate,
    PackageSpecialRequirementsPayload,
    ProviderWhatsAppSettingsResponse,
    ProviderWhatsAppSettingsUpdate,
)
from app.services.tour_guide_public import get_public_guide_or_404
from app.services.tour_guide_sync import (
    classify_package_duration,
    record_tour_guide_admin_audit,
)

M034_FEATURE_FLAG_ENV = "TOUR_GUIDE_ENABLE_M034_FIELDS"
_E164_OR_SL_WHATSAPP_RE = re.compile(r"^(?:\+?[1-9]\d{6,14}|0\d{9})$")


def is_m034_feature_flag_enabled() -> bool:
    raw = os.getenv(M034_FEATURE_FLAG_ENV, "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _table_columns(db: Session, table_name: str) -> set[str]:
    bind = db.get_bind()
    if bind is None:
        return set()
    inspector = sa_inspect(bind)
    if not inspector.has_table(table_name):
        return set()
    return {col["name"].lower() for col in inspector.get_columns(table_name)}


def get_m034_schema_capabilities(db: Session) -> dict[str, bool]:
    flag = is_m034_feature_flag_enabled()
    tg_cols = _table_columns(db, "tour_guides")
    pkg_cols = _table_columns(db, "tour_guide_packages")
    bk_cols = _table_columns(db, "tour_guide_bookings")

    return {
        "feature_flag_enabled": flag,
        "whatsapp_contact": flag and {"whatsapp_number", "allow_whatsapp_contact"}.issubset(tg_cols),
        "verified_experience": flag and "verified_years_of_experience" in tg_cols,
        "meeting_instructions": flag and "meeting_instructions" in pkg_cols,
        "special_requirements": flag and "special_requirements" in pkg_cols,
        "package_type": flag and "package_type" in pkg_cols,
        "booking_payment_extensions": flag and {
            "approved_at",
            "payment_expires_at",
            "cash_received_at",
            "commission_rate",
            "commission_amount",
            "provider_payable_amount",
        }.issubset(bk_cols),
    }


def _require_m034_capability(db: Session, capability_key: str, feature_label: str) -> None:
    caps = get_m034_schema_capabilities(db)
    if not caps.get(capability_key, False):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"{feature_label} is currently disabled pending Database Coordinator approval "
                f"and rollout of Migration 034 (and {M034_FEATURE_FLAG_ENV}=true)."
            ),
        )


def _commit(db: Session) -> None:
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


# =============================================================================
# 1. TR-18 / PR-17: WHATSAPP VALIDATION, PRIVACY MASKING & SETTINGS
# =============================================================================

def normalize_and_validate_whatsapp_number(raw: str | None) -> str | None:
    if raw is None:
        return None
    compact = re.sub(r"[\s\-()]+", "", raw.strip())
    if not compact:
        return None
    if not _E164_OR_SL_WHATSAPP_RE.match(compact):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Invalid WhatsApp number format. Provide a valid Sri Lankan number "
                "(e.g. 0771234567 or +94771234567) or E.164 international number."
            ),
        )
    if compact.startswith("0") and len(compact) == 10:
        return f"+94{compact[1:]}"
    if not compact.startswith("+"):
        return f"+{compact}"
    return compact


def mask_whatsapp_number(number: str) -> str:
    cleaned = number.strip()
    if len(cleaned) <= 6:
        return "*" * len(cleaned)
    return f"{cleaned[:4]}{'*' * (len(cleaned) - 7)}{cleaned[-3:]}"


def build_public_whatsapp_contact(
    *,
    whatsapp_number: str | None,
    allow_whatsapp_contact: bool,
    is_authenticated: bool,
    schema_enabled: bool,
) -> dict:
    if not schema_enabled or not allow_whatsapp_contact or not whatsapp_number:
        return {
            "whatsapp_available": False,
            "whatsapp_number": None,
            "whatsapp_link": None,
        }
    if not is_authenticated:
        return {
            "whatsapp_available": True,
            "whatsapp_number": mask_whatsapp_number(whatsapp_number),
            "whatsapp_link": None,
        }
    digits = re.sub(r"\D", "", whatsapp_number)
    return {
        "whatsapp_available": True,
        "whatsapp_number": whatsapp_number,
        "whatsapp_link": f"https://wa.me/{digits}" if digits else None,
    }


def _read_guide_whatsapp_row(db: Session, guide_id: int) -> tuple[str | None, bool]:
    row = db.execute(
        text(
            "SELECT whatsapp_number, allow_whatsapp_contact "
            "FROM tour_guides WHERE id = :gid"
        ),
        {"gid": guide_id},
    ).fetchone()
    if row is None:
        return None, False
    return row[0], bool(row[1])


def get_public_guide_whatsapp_contact(
    db: Session,
    *,
    guide: TourGuide,
    is_authenticated: bool,
) -> dict:
    caps = get_m034_schema_capabilities(db)
    if not caps["whatsapp_contact"]:
        return build_public_whatsapp_contact(
            whatsapp_number=None,
            allow_whatsapp_contact=False,
            is_authenticated=is_authenticated,
            schema_enabled=False,
        )
    wa_num, allow_wa = _read_guide_whatsapp_row(db, guide.id)
    return build_public_whatsapp_contact(
        whatsapp_number=wa_num,
        allow_whatsapp_contact=allow_wa,
        is_authenticated=is_authenticated,
        schema_enabled=True,
    )


def get_provider_whatsapp_settings(
    db: Session,
    *,
    guide: TourGuide,
) -> ProviderWhatsAppSettingsResponse:
    provider = guide.provider_profile
    caps = get_m034_schema_capabilities(db)
    if not caps["whatsapp_contact"]:
        return ProviderWhatsAppSettingsResponse(
            tour_guide_id=guide.id,
            primary_phone=provider.phone,
            secondary_phone=provider.secondary_phone,
            whatsapp_number=None,
            allow_whatsapp_contact=False,
            migration_034_active=False,
            status_note="WhatsApp contact settings are disabled until Migration 034 is approved and applied.",
        )

    wa_num, allow_wa = _read_guide_whatsapp_row(db, guide.id)
    return ProviderWhatsAppSettingsResponse(
        tour_guide_id=guide.id,
        primary_phone=provider.phone,
        secondary_phone=provider.secondary_phone,
        whatsapp_number=wa_num,
        allow_whatsapp_contact=allow_wa,
        migration_034_active=True,
        status_note="WhatsApp opt-in contact settings are active.",
    )


def update_provider_whatsapp_settings(
    db: Session,
    *,
    guide: TourGuide,
    payload: ProviderWhatsAppSettingsUpdate,
) -> ProviderWhatsAppSettingsResponse:
    normalized = normalize_and_validate_whatsapp_number(payload.whatsapp_number)
    if payload.allow_whatsapp_contact and not normalized:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A valid whatsapp_number is required when allow_whatsapp_contact is true.",
        )

    _require_m034_capability(db, "whatsapp_contact", "WhatsApp contact opt-in settings")

    db.execute(
        text(
            "UPDATE tour_guides "
            "SET whatsapp_number = :wa_num, allow_whatsapp_contact = :allow_wa "
            "WHERE id = :gid"
        ),
        {
            "wa_num": normalized,
            "allow_wa": 1 if payload.allow_whatsapp_contact else 0,
            "gid": guide.id,
        },
    )
    _commit(db)
    return get_provider_whatsapp_settings(db, guide=guide)


# =============================================================================
# 2. GS-01: ADMIN-VERIFIED YEARS OF GUIDE EXPERIENCE (EVIDENCE-GATED)
# =============================================================================

def get_guide_verified_experience_years(db: Session, guide_id: int) -> int | None:
    caps = get_m034_schema_capabilities(db)
    if not caps["verified_experience"]:
        return None
    row = db.execute(
        text("SELECT verified_years_of_experience FROM tour_guides WHERE id = :gid"),
        {"gid": guide_id},
    ).fetchone()
    return int(row[0]) if row is not None and row[0] is not None else None


def admin_set_verified_experience_years(
    db: Session,
    *,
    guide_id: int,
    payload: AdminVerifiedExperienceUpdate,
    admin: User,
) -> AdminVerifiedExperienceResponse:
    guide = db.query(TourGuide).filter(TourGuide.id == guide_id).first()
    if guide is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide not found.",
        )

    # Evidence gate: setting non-null verified years requires at least one approved credential
    if payload.verified_years_of_experience is not None:
        approved_cred = (
            db.query(TourGuideCredential.id)
            .filter(
                TourGuideCredential.tour_guide_id == guide.id,
                TourGuideCredential.verification_status == "approved",
            )
            .first()
        )
        if approved_cred is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Cannot record admin-verified years of experience without at least "
                    "one approved guide credential on file."
                ),
            )

    _require_m034_capability(db, "verified_experience", "Admin-verified years of experience")

    db.execute(
        text(
            "UPDATE tour_guides "
            "SET verified_years_of_experience = :years "
            "WHERE id = :gid"
        ),
        {"years": payload.verified_years_of_experience, "gid": guide.id},
    )
    record_tour_guide_admin_audit(
        db,
        admin_user_id=admin.id,
        action="set_guide_verified_experience",
        entity_name="tour_guide",
        entity_id=guide.id,
        details={
            "self_reported_years": guide.years_of_experience,
            "verified_years_of_experience": payload.verified_years_of_experience,
            "notes": payload.notes,
        },
    )
    create_notification(
        db=db,
        user_id=guide.provider_profile.user_id,
        title="Guide Experience Verification Updated",
        message=(
            f"Admin verified your guide experience as {payload.verified_years_of_experience} year(s)."
            if payload.verified_years_of_experience is not None
            else "Admin cleared your verified years of experience."
        ),
        notification_type="guide_experience_verified",
        link_url="/provider/tour-guides/verification-status",
    )
    _commit(db)

    return AdminVerifiedExperienceResponse(
        tour_guide_id=guide.id,
        self_reported_years_of_experience=guide.years_of_experience,
        verified_years_of_experience=payload.verified_years_of_experience,
        experience_is_verified=(payload.verified_years_of_experience is not None),
        migration_034_active=True,
        message="Verified years of experience updated.",
    )


# =============================================================================
# 3. GS-03 & GS-06: PACKAGE MEETING INSTRUCTIONS & SPECIAL REQUIREMENTS
# =============================================================================

def serialize_special_requirements(
    payload: PackageSpecialRequirementsPayload | None,
) -> str | None:
    if payload is None:
        return None
    data = payload.model_dump(exclude_none=True)
    if not data:
        return None
    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) > 2000:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Serialized special_requirements exceeds maximum storage length of 2000 characters.",
        )
    return serialized


def parse_special_requirements(
    raw: str | None,
) -> PackageSpecialRequirementsPayload | None:
    if not raw or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return PackageSpecialRequirementsPayload.model_validate(parsed)
    except Exception:
        pass
    return PackageSpecialRequirementsPayload(other_notes=raw.strip()[:600])


def _read_package_m034_fields(
    db: Session,
    *,
    pkg: TourGuidePackage,
) -> tuple[str, str | None, PackageSpecialRequirementsPayload | None, bool]:
    caps = get_m034_schema_capabilities(db)
    active = caps["meeting_instructions"] and caps["special_requirements"] and caps["package_type"]
    derived_type = classify_package_duration(float(pkg.duration_hours))
    if not active:
        return derived_type, None, None, False

    row = db.execute(
        text(
            "SELECT package_type, meeting_instructions, special_requirements "
            "FROM tour_guide_packages WHERE id = :pid"
        ),
        {"pid": pkg.id},
    ).fetchone()
    if row is None:
        return derived_type, None, None, True
    return row[0] or derived_type, row[1], parse_special_requirements(row[2]), True


def get_packages_m034_fields_map(
    db: Session,
    package_ids: list[int],
) -> dict[int, tuple[str | None, PackageSpecialRequirementsPayload | None]]:
    """
    Safely read Migration 034 meeting_instructions & special_requirements for a
    batch of package IDs ONLY when Migration 034 is active. Returns {} on Migration 033.
    """
    if not package_ids:
        return {}
    caps = get_m034_schema_capabilities(db)
    if not (caps["meeting_instructions"] and caps["special_requirements"]):
        return {}

    result: dict[int, tuple[str | None, PackageSpecialRequirementsPayload | None]] = {}
    for pid in package_ids:
        row = db.execute(
            text(
                "SELECT meeting_instructions, special_requirements "
                "FROM tour_guide_packages WHERE id = :pid"
            ),
            {"pid": pid},
        ).fetchone()
        if row is not None:
            result[pid] = (row[0], parse_special_requirements(row[1]))
    return result


def get_public_package_extended_requirements(
    db: Session,
    *,
    package_id: int,
) -> PackageExtendedRequirementsResponse:
    pkg = (
        db.query(TourGuidePackage)
        .filter(TourGuidePackage.id == package_id, TourGuidePackage.is_active == True)
        .first()
    )
    if pkg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour package not found.",
        )
    # Enforce strict 4-condition public visibility gate on owning guide
    get_public_guide_or_404(db, pkg.tour_guide_id)

    pkg_type, instructions, special_reqs, m034_active = _read_package_m034_fields(db, pkg=pkg)
    return PackageExtendedRequirementsResponse(
        package_id=pkg.id,
        tour_guide_id=pkg.tour_guide_id,
        meeting_point=pkg.meeting_point,
        package_type=pkg_type,
        meeting_instructions=instructions,
        special_requirements=special_reqs,
        migration_034_active=m034_active,
        status_note=(
            "Extended package meeting instructions and special requirements are active."
            if m034_active
            else "Extended meeting instructions and special requirements are disabled pending Migration 034."
        ),
    )


def update_provider_package_extended_requirements(
    db: Session,
    *,
    guide: TourGuide,
    package_id: int,
    payload: PackageExtendedRequirementsUpdate,
) -> PackageExtendedRequirementsResponse:
    pkg = (
        db.query(TourGuidePackage)
        .filter(
            TourGuidePackage.id == package_id,
            TourGuidePackage.tour_guide_id == guide.id,
        )
        .first()
    )
    if pkg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour package not found.",
        )

    # Validate serialization before checking schema gate so invalid inputs always return 422
    serialized_reqs = serialize_special_requirements(payload.special_requirements)

    _require_m034_capability(
        db, "meeting_instructions", "Package meeting instructions and special requirements"
    )
    _require_m034_capability(
        db, "special_requirements", "Package meeting instructions and special requirements"
    )
    _require_m034_capability(
        db, "package_type", "Package meeting instructions and special requirements"
    )

    updates = payload.model_dump(exclude_unset=True)
    set_clauses: list[str] = []
    params: dict = {"pid": pkg.id}

    if "package_type" in updates:
        set_clauses.append("package_type = :package_type")
        params["package_type"] = payload.package_type
    if "meeting_instructions" in updates:
        set_clauses.append("meeting_instructions = :meeting_instructions")
        params["meeting_instructions"] = payload.meeting_instructions
    if "special_requirements" in updates:
        set_clauses.append("special_requirements = :special_requirements")
        params["special_requirements"] = serialized_reqs

    if set_clauses:
        db.execute(
            text(f"UPDATE tour_guide_packages SET {', '.join(set_clauses)} WHERE id = :pid"),
            params,
        )
        _commit(db)

    pkg_type, instructions, special_reqs, m034_active = _read_package_m034_fields(db, pkg=pkg)
    return PackageExtendedRequirementsResponse(
        package_id=pkg.id,
        tour_guide_id=pkg.tour_guide_id,
        meeting_point=pkg.meeting_point,
        package_type=pkg_type,
        meeting_instructions=instructions,
        special_requirements=special_reqs,
        migration_034_active=m034_active,
        status_note="Extended package meeting instructions and special requirements updated.",
    )
