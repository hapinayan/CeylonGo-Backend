"""
FastAPI Routers for Local Tour Guide Module 05 — Migration 034 Schema-Guarded Endpoints:
- PR-17: Provider WhatsApp opt-in contact settings
- GS-01: Admin-verified years of experience (evidence-gated)
- GS-03 & GS-06: Package meeting instructions & guide-authored special requirements

All endpoints remain safely guarded until Migration 034 is approved/applied and
TOUR_GUIDE_ENABLE_M034_FIELDS=true is enabled.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.core.dependencies import require_admin, require_operations_admin
from app.database.connection import get_db
from app.models.user import User
from app.routers.tour_guide import OwnedGuide
from app.schemas.tour_guide import (
    AdminVerifiedExperienceResponse,
    AdminVerifiedExperienceUpdate,
    PackageExtendedRequirementsResponse,
    PackageExtendedRequirementsUpdate,
    ProviderWhatsAppSettingsResponse,
    ProviderWhatsAppSettingsUpdate,
)
from app.services.tour_guide_m034_extensions import (
    admin_set_verified_experience_years,
    get_provider_whatsapp_settings,
    get_public_package_extended_requirements,
    update_provider_package_extended_requirements,
    update_provider_whatsapp_settings,
)

DatabaseSession = Annotated[Session, Depends(get_db)]

public_router = APIRouter(
    tags=["Tour Guides"],
)

provider_router = APIRouter(
    prefix="/api/v1/provider/tour-guides",
    tags=["Tour Guide Provider"],
)

admin_router = APIRouter(
    prefix="/api/v1/admin/tour-guides",
    tags=["Admin Tour Guides"],
)


@provider_router.get(
    "/contact-settings",
    response_model=ProviderWhatsAppSettingsResponse,
    summary="View provider phone and WhatsApp opt-in contact settings (PR-17)",
)
def provider_get_contact_settings(
    guide: OwnedGuide,
    db: DatabaseSession,
):
    return get_provider_whatsapp_settings(db, guide=guide)


@provider_router.put(
    "/contact-settings",
    response_model=ProviderWhatsAppSettingsResponse,
    summary="Update provider WhatsApp number and explicit opt-in visibility (PR-17; requires Migration 034)",
)
def provider_update_contact_settings(
    payload: ProviderWhatsAppSettingsUpdate,
    guide: OwnedGuide,
    db: DatabaseSession,
):
    return update_provider_whatsapp_settings(db, guide=guide, payload=payload)


@provider_router.put(
    "/packages/{package_id}/requirements",
    response_model=PackageExtendedRequirementsResponse,
    summary="Set package meeting instructions and guide-authored special requirements (GS-03, GS-06; requires Migration 034)",
)
def provider_update_package_requirements(
    package_id: Annotated[int, Path(gt=0)],
    payload: PackageExtendedRequirementsUpdate,
    guide: OwnedGuide,
    db: DatabaseSession,
):
    return update_provider_package_extended_requirements(
        db,
        guide=guide,
        package_id=package_id,
        payload=payload,
    )


@public_router.get(
    "/api/v1/tour-guides/packages/{package_id}/requirements",
    response_model=PackageExtendedRequirementsResponse,
    summary="Public read of package meeting instructions and special requirements (GS-03, GS-06)",
)
def public_get_package_requirements(
    package_id: Annotated[int, Path(gt=0)],
    db: DatabaseSession,
):
    return get_public_package_extended_requirements(db, package_id=package_id)


@admin_router.put(
    "/{guide_id}/verified-experience",
    response_model=AdminVerifiedExperienceResponse,
    summary="Admin: set evidence-gated verified years of experience for a guide (GS-01; requires Migration 034)",
)
def admin_update_guide_verified_experience(
    guide_id: Annotated[int, Path(gt=0)],
    payload: AdminVerifiedExperienceUpdate,
    admin: Annotated[User, Depends(require_operations_admin)],
    db: DatabaseSession,
):
    return admin_set_verified_experience_years(
        db,
        guide_id=guide_id,
        payload=payload,
        admin=admin,
    )
