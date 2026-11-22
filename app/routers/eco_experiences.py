"""
FastAPI Router for Module 04 — Eco-Tourism Experience & Verification APIs.

Provides:
1. Public Discovery APIs:
   - GET /api/v1/eco-experiences (approved & published only; filters: search, category, district,
     min_price, max_price, min_duration_hours, max_duration_hours, eco_badge_only, pagination)
   - GET /api/v1/eco-experiences/categories (6 approved Eco-Tourism categories)
   - GET /api/v1/eco-experiences/districts (available districts with approved & published listings)
   - GET /api/v1/eco-experiences/{id} (public details, safety rules, conservation info; never exposes
     private verification evidence or unverified badges)
   - GET /api/v1/eco-experiences/{id}/safety-rules (public guidelines for published listings)

2. Service Provider Eco Experience APIs (Approved 'eco_tourism' providers only):
   - GET /api/v1/eco-experiences/me/listings (list authenticated provider's own listings)
   - POST /api/v1/eco-experiences (create draft listing; defaults to draft, unpublished, no badge)
   - PUT /api/v1/eco-experiences/{id} (update owned listing)
   - POST /api/v1/eco-experiences/{id}/submit (submit draft or rejected listing for admin approval)
   - PATCH /api/v1/eco-experiences/{id}/publish (publish or deactivate an admin-approved listing)

3. Admin Listing Approval APIs (Authenticated admins only):
   - GET /api/v1/admin/eco-experiences (list pending/filtered eco experiences for moderation)
   - GET /api/v1/admin/eco-experiences/{id} (get full management details for admin review)
   - POST /api/v1/admin/eco-experiences/{id}/approve (approve submitted listing; never auto-grants eco badge)
   - POST /api/v1/admin/eco-experiences/{id}/reject (reject submitted listing with recorded reason)

4. Eco Safety Rules & Guidelines APIs (P0.3):
   - POST /api/v1/eco-experiences/{id}/safety-rules (provider adds guideline / safety rule)
   - DELETE /api/v1/eco-experiences/{id}/safety-rules/{rule_id} (provider removes safety rule)

5. Sustainability Practices & Private Evidence APIs (P0.3):
   - POST /api/v1/eco-experiences/{id}/practices (provider adds sustainability claim)
   - GET /api/v1/eco-experiences/{id}/practices (provider views practices with safe evidence metadata)
   - DELETE /api/v1/eco-experiences/{id}/practices/{practice_id} (provider removes practice and clean evidence)
   - POST /api/v1/eco-experiences/{id}/practices/{practice_id}/evidence (secure private evidence upload)
   - DELETE /api/v1/eco-experiences/{id}/practices/{practice_id}/evidence/{evidence_id} (delete evidence)
   - POST /api/v1/eco-experiences/{id}/submit-verification (provider requests eco badge verification)

6. Admin Eco Verification APIs (P0.3):
   - GET /api/v1/admin/eco-experiences/{id}/evidence/{evidence_id} (securely stream private evidence document)
   - POST /api/v1/admin/eco-experiences/{id}/badge-decision (grant, reject, or revoke eco badge with audit record)
   - GET /api/v1/admin/eco-experiences/{id}/verifications (view immutable verification decision history)
"""

from datetime import UTC, datetime
import io
import logging
import math
import os
from pathlib import Path
from typing import Annotated
import uuid

logger = logging.getLogger(__name__)

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from sqlalchemy import case, func, or_
from sqlalchemy import true
from sqlalchemy.sql import ColumnElement
from sqlalchemy.orm import Session, selectinload

from app.core.audit import record_admin_audit_log
from app.core.publication import public_eco
from app.routers.auth import get_optional_current_user
from app.core.dependencies import (
    require_admin,
    require_approved_eco_provider,
    require_approved_provider,
    require_operations_admin,
)
from app.core.email import send_eco_badge_decision_email
from app.core.location_validation import (
    ECO_LOCATION_FIELDS,
    ECO_REVIEW_STATE,
    validate_coordinate_pair,
    write_review_snapshot,
)
from app.core.security import create_sponsored_tracking_token
from app.database.connection import get_db
from app.models.eco_experience import (
    VALID_ECO_CATEGORIES,
    VALID_EVIDENCE_DOCUMENT_TYPES,
    EcoEvidence,
    EcoExperience,
    EcoExperienceImage,
    EcoPractice,
    EcoSafetyRule,
    EcoSeasonRestriction,
    EcoVerification,
)
from app.models.eco_promotion import EcoSponsoredPromotion
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.user import User
from app.schemas.eco_experience import (
    AdminRejectEcoExperienceRequest,
    EcoAdminCategoryBreakdown,
    EcoAdminDistrictBreakdown,
    EcoAdminOverviewResponse,
    EcoAdminStatusBreakdown,
    EcoEvidenceMetadataResponse,
    EcoExperienceAdminPagination,
    EcoExperienceCreate,
    EcoExperienceImageResponse,
    EcoExperienceManagementResponse,
    EcoExperiencePagination,
    EcoExperiencePublicDetailResponse,
    EcoExperiencePublicSummary,
    EcoExperiencePublishRequest,
    EcoExperienceUpdate,
    EcoPracticeInput,
    EcoPracticeManagementResponse,
    EcoProviderListingSummary,
    EcoSafetyRuleInput,
    EcoSafetyRuleResponse,
    EcoVerificationDecisionRequest,
    EcoVerificationHistoryResponse,
)

router = APIRouter(
    prefix="/api/v1/eco-experiences",
    tags=["Eco Experiences"],
)

admin_eco_router = APIRouter(
    prefix="/api/v1/admin/eco-experiences",
    tags=["Admin Eco Experiences"],
    dependencies=[Depends(require_admin), Depends(require_operations_admin)],
)

ALLOWED_EVIDENCE_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_EVIDENCE_MIMES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
}
MAX_EVIDENCE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB


def get_eco_evidence_dir() -> Path:
    base_dir = os.getenv(
        "ECO_EVIDENCE_UPLOAD_DIR",
        str(Path(os.getcwd()) / "uploads" / "private_eco_evidence"),
    )
    p = Path(base_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p.resolve()


ALLOWED_ECO_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_ECO_IMAGE_MIMES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}
ALLOWED_ECO_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_ECO_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MIN_ECO_IMAGE_DIMENSION = 50  # 50x50px minimum
MAX_ECO_IMAGE_DIMENSION = 8000  # 8000x8000px maximum
MAX_IMAGES_PER_EXPERIENCE = 20


def get_eco_experience_images_dir() -> Path:
    base_dir = os.getenv(
        "ECO_EXPERIENCE_IMAGES_DIR",
        str(Path(os.getcwd()) / "uploads" / "eco_experiences"),
    )
    p = Path(base_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p.resolve()


uploads_router = APIRouter(tags=["Uploads"])


# require_approved_eco_provider is imported from app.core.dependencies


def _load_full_eco_experience_query(db: Session):
    return db.query(EcoExperience).options(
        selectinload(EcoExperience.provider_profile),
        selectinload(EcoExperience.images),
        selectinload(EcoExperience.safety_rules),
        selectinload(EcoExperience.season_restrictions),
        selectinload(EcoExperience.practices).selectinload(EcoPractice.evidences),
        selectinload(EcoExperience.verifications),
    )


def _validate_linked_homestay(
    db: Session,
    linked_homestay_id: int | None,
    eco_category: str,
) -> None:
    if linked_homestay_id is None:
        return
    if eco_category != "Eco-Friendly Stays":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="linked_homestay_id can only be set when eco_category is 'Eco-Friendly Stays'",
        )
    homestay = (
        db.query(Homestay)
        .filter(
            Homestay.id == linked_homestay_id,
            Homestay.is_active == True,
        )
        .first()
    )
    if not homestay:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Linked homestay not found or inactive",
        )


# ==============================================================================
# 1. Public Discovery & Details Endpoints
# ==============================================================================


def _compute_interleaving_offsets(
    sponsored_count: int,
    organic_count: int,
    page: int,
    page_size: int,
    max_boost: int = 2,
) -> tuple[int, int, int, int, int, int]:
    """
    Computes deterministic pagination slices for mixed sponsored & organic listings:
    Returns (s_boost_start, n_s_boost, o_start, n_o, s_backfill_start, n_s_backfill).

    Guarantees:
    1. Up to min(max_boost, page_size) sponsored items receive boosted placement at the top of each page.
    2. Remaining page slots are filled with organic items in natural ranking order.
    3. If organic inventory is exhausted before the page is full, remaining page slots are
       backfilled from unplaced sponsored inventory in natural ranking order.
    4. Across all pages 1..total_pages:
       - Every matching listing appears EXACTLY once (0 duplicates, 0 missing).
       - Pages 1..(total_pages-1) are always full (length == page_size).
    """
    total = sponsored_count + organic_count
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    if page < 1 or page > total_pages:
        return 0, 0, 0, 0, 0, 0

    k = min(max_boost, page_size)
    s_idx = 0
    o_idx = 0

    for _ in range(1, page):
        n_sb = min(k, sponsored_count - s_idx)
        s_idx += n_sb
        n_org = min(page_size - n_sb, organic_count - o_idx)
        o_idx += n_org
        still = page_size - (n_sb + n_org)
        n_sbf = min(still, sponsored_count - s_idx)
        s_idx += n_sbf

    s_boost_start = s_idx
    n_s_boost = min(k, sponsored_count - s_idx)
    s_idx += n_s_boost

    o_start = o_idx
    n_o = min(page_size - n_s_boost, organic_count - o_idx)
    o_idx += n_o

    still_needed = page_size - (n_s_boost + n_o)
    s_backfill_start = s_idx
    n_s_backfill = min(still_needed, sponsored_count - s_idx)

    return s_boost_start, n_s_boost, o_start, n_o, s_backfill_start, n_s_backfill


@router.get(
    "",
    response_model=EcoExperiencePagination,
    summary="Browse approved and published eco experiences",
)
def list_eco_experiences(
    db: Annotated[Session, Depends(get_db)],
    search: str | None = Query(
        default=None,
        max_length=100,
        description="Search in title, description, city, or meeting point",
    ),
    category: str | None = Query(
        default=None,
        max_length=60,
        description="Filter by approved Eco-Tourism category",
    ),
    district: str | None = Query(
        default=None,
        max_length=100,
        description="Filter by Sri Lankan district",
    ),
    min_price: float | None = Query(
        default=None, ge=0, description="Minimum base price"
    ),
    max_price: float | None = Query(
        default=None, ge=0, description="Maximum base price"
    ),
    min_duration_hours: float | None = Query(
        default=None, gt=0, description="Minimum duration in hours"
    ),
    max_duration_hours: float | None = Query(
        default=None, gt=0, description="Maximum duration in hours"
    ),
    eco_badge_only: bool = Query(
        default=False,
        description="Filter only listings with verified Eco-Friendly Badge",
    ),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(
        default=12, ge=1, le=100, description="Items per page"
    ),
):
    """
    Public discovery endpoint.
    Strictly returns only eco experiences with listing_status == 'approved' and is_published == True.
    """
    if (
        min_price is not None
        and max_price is not None
        and min_price > max_price
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_price cannot exceed max_price",
        )

    if (
        min_duration_hours is not None
        and max_duration_hours is not None
        and min_duration_hours > max_duration_hours
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_duration_hours cannot exceed max_duration_hours",
        )

    query = (
        db.query(EcoExperience)
        .options(
            selectinload(EcoExperience.provider_profile),
            selectinload(EcoExperience.verifications),
        )
        .filter(
            public_eco(),
        )
    )

    if search and search.strip():
        pattern = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(EcoExperience.title).like(pattern),
                func.lower(EcoExperience.description).like(pattern),
                func.lower(EcoExperience.city).like(pattern),
                func.lower(EcoExperience.meeting_point).like(pattern),
            )
        )

    if category and category.strip():
        query = query.filter(
            func.lower(EcoExperience.eco_category) == category.strip().lower()
        )

    if district and district.strip():
        query = query.filter(
            func.lower(EcoExperience.district) == district.strip().lower()
        )

    if min_price is not None:
        query = query.filter(EcoExperience.base_price >= min_price)

    if max_price is not None:
        query = query.filter(EcoExperience.base_price <= max_price)

    if min_duration_hours is not None:
        query = query.filter(
            EcoExperience.duration_hours >= min_duration_hours
        )

    if max_duration_hours is not None:
        query = query.filter(
            EcoExperience.duration_hours <= max_duration_hours
        )

    now = datetime.now(UTC)
    active_campaign_exists = (
        db.query(EcoSponsoredPromotion.id)
        .filter(
            EcoSponsoredPromotion.eco_experience_id == EcoExperience.id,
            EcoSponsoredPromotion.campaign_status == "active",
            EcoSponsoredPromotion.payment_status == "paid",
            EcoSponsoredPromotion.starts_at <= now,
            EcoSponsoredPromotion.ends_at > now,
        )
        .correlate(EcoExperience)
        .exists()
    )

    if eco_badge_only:
        query = query.filter(EcoExperience.eco_badge_status == "granted")
        all_matches = [
            i
            for i in query.order_by(
                EcoExperience.created_at.desc(), EcoExperience.id.desc()
            ).all()
            if i.has_verified_eco_badge
        ]
        total = len(all_matches)
        total_pages = math.ceil(total / page_size) if total > 0 else 0

        if not all_matches:
            return EcoExperiencePagination(
                items=[],
                total=0,
                page=page,
                page_size=page_size,
                total_pages=0,
            )

        match_ids = [i.id for i in all_matches]
        spon_rows = (
            db.query(
                EcoSponsoredPromotion.eco_experience_id,
                EcoSponsoredPromotion.id,
            )
            .filter(
                EcoSponsoredPromotion.eco_experience_id.in_(match_ids),
                EcoSponsoredPromotion.campaign_status == "active",
                EcoSponsoredPromotion.payment_status == "paid",
                EcoSponsoredPromotion.starts_at <= now,
                EcoSponsoredPromotion.ends_at > now,
            )
            .all()
        )
        active_spon_id_set = {r[0] for r in spon_rows}
        active_spon_camp_map = {r[0]: r[1] for r in spon_rows}

        sponsored_list = [i for i in all_matches if i.id in active_spon_id_set]
        organic_list = [i for i in all_matches if i.id not in active_spon_id_set]

        sponsored_count = len(sponsored_list)
        organic_count = len(organic_list)

        (
            s_b_start,
            n_sb,
            o_start,
            n_o,
            s_bf_start,
            n_sbf,
        ) = _compute_interleaving_offsets(
            sponsored_count, organic_count, page, page_size, max_boost=2
        )
        page_sponsored_boost = sponsored_list[s_b_start : s_b_start + n_sb]
        page_organic = organic_list[o_start : o_start + n_o]
        page_sponsored_backfill = sponsored_list[
            s_bf_start : s_bf_start + n_sbf
        ]

        summaries = []
        for item in page_sponsored_boost:
            s = EcoExperiencePublicSummary.model_validate(item)
            s.is_sponsored = True
            c_id = active_spon_camp_map.get(item.id)
            if c_id:
                s.sponsored_tracking_token = create_sponsored_tracking_token(
                    campaign_id=c_id, eco_experience_id=item.id
                )
            summaries.append(s)
        for item in page_organic:
            s = EcoExperiencePublicSummary.model_validate(item)
            s.is_sponsored = False
            s.sponsored_tracking_token = None
            summaries.append(s)
        for item in page_sponsored_backfill:
            s = EcoExperiencePublicSummary.model_validate(item)
            s.is_sponsored = True
            s.sponsored_tracking_token = None
            summaries.append(s)

        return EcoExperiencePagination(
            items=summaries,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )
    else:
        total = query.count()
        total_pages = math.ceil(total / page_size) if total > 0 else 0

        if total == 0:
            return EcoExperiencePagination(
                items=[],
                total=0,
                page=page,
                page_size=page_size,
                total_pages=0,
            )

        sponsored_count = query.filter(active_campaign_exists).count()
        organic_count = total - sponsored_count

        if sponsored_count == 0:
            # Fast path when no active sponsored campaigns exist (e.g. current deferred payment baseline)
            items = (
                query.order_by(
                    EcoExperience.created_at.desc(), EcoExperience.id.desc()
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            summaries = []
            for item in items:
                s = EcoExperiencePublicSummary.model_validate(item)
                s.is_sponsored = False
                summaries.append(s)
        else:
            (
                s_b_start,
                n_sb,
                o_start,
                n_o,
                s_bf_start,
                n_sbf,
            ) = _compute_interleaving_offsets(
                sponsored_count, organic_count, page, page_size, max_boost=2
            )

            page_sponsored_boost = []
            if n_sb > 0:
                page_sponsored_boost = (
                    query.filter(active_campaign_exists)
                    .order_by(
                        EcoExperience.created_at.desc(), EcoExperience.id.desc()
                    )
                    .offset(s_b_start)
                    .limit(n_sb)
                    .all()
                )

            page_organic = []
            if n_o > 0:
                page_organic = (
                    query.filter(~active_campaign_exists)
                    .order_by(
                        EcoExperience.created_at.desc(), EcoExperience.id.desc()
                    )
                    .offset(o_start)
                    .limit(n_o)
                    .all()
                )

            page_sponsored_backfill = []
            if n_sbf > 0:
                page_sponsored_backfill = (
                    query.filter(active_campaign_exists)
                    .order_by(
                        EcoExperience.created_at.desc(), EcoExperience.id.desc()
                    )
                    .offset(s_bf_start)
                    .limit(n_sbf)
                    .all()
                )

            boost_exp_ids = [item.id for item in page_sponsored_boost]
            boost_camp_map = {}
            if boost_exp_ids:
                b_rows = (
                    db.query(
                        EcoSponsoredPromotion.eco_experience_id,
                        EcoSponsoredPromotion.id,
                    )
                    .filter(
                        EcoSponsoredPromotion.eco_experience_id.in_(
                            boost_exp_ids
                        ),
                        EcoSponsoredPromotion.campaign_status == "active",
                        EcoSponsoredPromotion.payment_status == "paid",
                        EcoSponsoredPromotion.starts_at <= now,
                        EcoSponsoredPromotion.ends_at > now,
                    )
                    .all()
                )
                boost_camp_map = {r[0]: r[1] for r in b_rows}

            summaries = []
            for item in page_sponsored_boost:
                s = EcoExperiencePublicSummary.model_validate(item)
                s.is_sponsored = True
                c_id = boost_camp_map.get(item.id)
                if c_id:
                    s.sponsored_tracking_token = (
                        create_sponsored_tracking_token(
                            campaign_id=c_id, eco_experience_id=item.id
                        )
                    )
                summaries.append(s)
            for item in page_organic:
                s = EcoExperiencePublicSummary.model_validate(item)
                s.is_sponsored = False
                s.sponsored_tracking_token = None
                summaries.append(s)
            for item in page_sponsored_backfill:
                s = EcoExperiencePublicSummary.model_validate(item)
                s.is_sponsored = True
                s.sponsored_tracking_token = None
                summaries.append(s)

        return EcoExperiencePagination(
            items=summaries,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )


@router.get(
    "/categories",
    response_model=list[str],
    summary="Get the six approved Eco-Tourism categories",
)
def get_eco_categories():
    """
    Returns the six official CeylonGo Eco-Tourism categories.
    """
    return list(VALID_ECO_CATEGORIES)


@router.get(
    "/districts",
    response_model=list[str],
    summary="Get districts with active approved eco experiences",
)
def get_eco_districts(db: Annotated[Session, Depends(get_db)]):
    """
    Returns sorted unique districts where at least one approved and published
    eco experience is currently available.
    """
    districts = (
        db.query(EcoExperience.district)
        .filter(
            public_eco(),
        )
        .distinct()
        .order_by(EcoExperience.district.asc())
        .all()
    )
    return [d[0] for d in districts if d[0]]


@router.get(
    "/{experience_id}",
    response_model=EcoExperiencePublicDetailResponse,
    summary="Get public details of an approved eco experience",
)
def get_eco_experience_detail(
    experience_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Public detail endpoint for travelers.
    SECURITY RULE: Only approved & published listings are returned publicly.
    Exposes environmental guidelines, wildlife safety rules, and structured conservation details.
    Never exposes private verification evidence or unverified eco badges.
    """
    experience = (
        _load_full_eco_experience_query(db)
        .filter(EcoExperience.id == experience_id, public_eco())
        .first()
    )
    if not experience or not experience.is_publicly_visible:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )

    now = datetime.now(UTC)
    is_sponsored = (
        db.query(EcoSponsoredPromotion.id)
        .filter(
            EcoSponsoredPromotion.eco_experience_id == experience.id,
            EcoSponsoredPromotion.campaign_status == "active",
            EcoSponsoredPromotion.payment_status == "paid",
            EcoSponsoredPromotion.starts_at <= now,
            EcoSponsoredPromotion.ends_at > now,
        )
        .first()
        is not None
    )

    resp = EcoExperiencePublicDetailResponse.model_validate(experience)
    resp.is_sponsored = is_sponsored
    return resp


# ==============================================================================
# 2. Service Provider Eco Experience APIs
# ==============================================================================


@router.get(
    "/me/listings",
    response_model=list[EcoExperienceManagementResponse],
    summary="List authenticated provider's own eco experience listings",
)
def list_my_eco_experiences(
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Returns all listings created by the authenticated provider
    across all lifecycle states (draft, pending_approval, approved, rejected, deactivated).
    """
    listings = (
        _load_full_eco_experience_query(db)
        .filter(EcoExperience.provider_profile_id == provider_profile.id)
        .order_by(EcoExperience.created_at.desc(), EcoExperience.id.desc())
        .all()
    )
    return [
        EcoExperienceManagementResponse.model_validate(item)
        for item in listings
    ]


@router.get(
    "/me/summary",
    response_model=EcoProviderListingSummary,
    summary="Get authenticated provider's eco experience listing summary metrics",
)
def get_my_eco_experiences_summary(
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Returns high-level listing lifecycle and badge metrics
    for the authenticated provider's dashboard (Requirement P01).
    Strictly isolated to listings belonging to the authenticated provider_profile_id.
    """
    metrics = (
        db.query(
            func.count(EcoExperience.id).label("total"),
            func.sum(
                case((EcoExperience.listing_status == "draft", 1), else_=0)
            ).label("draft"),
            func.sum(
                case(
                    (EcoExperience.listing_status == "pending_approval", 1),
                    else_=0,
                )
            ).label("pending_approval"),
            func.sum(
                case((EcoExperience.listing_status == "approved", 1), else_=0)
            ).label("approved"),
            func.sum(
                case((EcoExperience.listing_status == "rejected", 1), else_=0)
            ).label("rejected"),
            func.sum(
                case(
                    (EcoExperience.listing_status == "deactivated", 1),
                    else_=0,
                )
            ).label("deactivated"),
            func.sum(
                case((EcoExperience.is_published == True, 1), else_=0)
            ).label("published"),
            func.sum(
                case(
                    (EcoExperience.eco_badge_status == "granted", 1),
                    else_=0,
                )
            ).label("verified_badges"),
            func.sum(
                case(
                    (EcoExperience.eco_badge_status == "pending", 1),
                    else_=0,
                )
            ).label("pending_badges"),
        )
        .filter(EcoExperience.provider_profile_id == provider_profile.id)
        .first()
    )

    if not metrics or metrics.total is None:
        return EcoProviderListingSummary(
            total=0,
            draft=0,
            pending_approval=0,
            approved=0,
            rejected=0,
            deactivated=0,
            published=0,
            verified_badges=0,
            pending_badges=0,
        )

    return EcoProviderListingSummary(
        total=int(metrics.total or 0),
        draft=int(metrics.draft or 0),
        pending_approval=int(metrics.pending_approval or 0),
        approved=int(metrics.approved or 0),
        rejected=int(metrics.rejected or 0),
        deactivated=int(metrics.deactivated or 0),
        published=int(metrics.published or 0),
        verified_badges=int(metrics.verified_badges or 0),
        pending_badges=int(metrics.pending_badges or 0),
    )


@router.post(
    "",
    response_model=EcoExperienceManagementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new draft eco experience listing",
)
def create_eco_experience(
    payload: EcoExperienceCreate,
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Allows an approved eco_tourism provider to create a new listing.
    SECURITY RULES:
    1. provider_profile_id is strictly derived from the authenticated session.
    2. listing_status defaults to 'draft'.
    3. is_published defaults to False.
    4. eco_badge_status defaults to 'none' (never granted automatically).
    """
    _validate_linked_homestay(
        db, payload.linked_homestay_id, payload.eco_category
    )

    validate_coordinate_pair(payload.latitude, payload.longitude)
    experience = EcoExperience(
        provider_profile_id=provider_profile.id,
        linked_homestay_id=payload.linked_homestay_id,
        title=payload.title,
        eco_category=payload.eco_category,
        description=payload.description,
        itinerary=payload.itinerary,
        duration_hours=payload.duration_hours,
        district=payload.district,
        city=payload.city,
        address=payload.address,
        meeting_point=payload.meeting_point,
        directions_info=payload.directions_info,
        latitude=payload.latitude,
        longitude=payload.longitude,
        pricing_type=payload.pricing_type,
        base_price=payload.base_price,
        currency=payload.currency,
        max_group_size=payload.max_group_size,
        min_participants=payload.min_participants,
        min_age=payload.min_age,
        environmental_guidelines=payload.environmental_guidelines,
        wildlife_safety_summary=payload.wildlife_safety_summary,
        conservation_factual_contribution=payload.conservation_factual_contribution,
        conservation_marketing_statement=payload.conservation_marketing_statement,
        protected_area_permit_required=payload.protected_area_permit_required,
        certified_guide_required=payload.certified_guide_required,
        permit_details=payload.permit_details,
        cancellation_policy=payload.cancellation_policy,
        free_cancellation_hours=payload.free_cancellation_hours,
        listing_status="draft",
        is_published=False,
        eco_badge_status="none",
    )
    db.add(experience)
    db.flush()

    for img in payload.images:
        db.add(
            EcoExperienceImage(
                eco_experience_id=experience.id,
                image_url=img.image_url,
                caption=img.caption,
                is_primary=img.is_primary,
                sort_order=img.sort_order,
            )
        )

    for rule in payload.safety_rules:
        db.add(
            EcoSafetyRule(
                eco_experience_id=experience.id,
                rule_type=rule.rule_type,
                title=rule.title,
                description=rule.description,
                is_mandatory=rule.is_mandatory,
            )
        )

    for restriction in payload.season_restrictions:
        db.add(
            EcoSeasonRestriction(
                eco_experience_id=experience.id,
                start_date=restriction.start_date,
                end_date=restriction.end_date,
                restriction_type=restriction.restriction_type,
                reason=restriction.reason,
                is_active=restriction.is_active,
                created_by=provider_profile.user_id,
            )
        )

    for practice in payload.practices:
        db.add(
            EcoPractice(
                eco_experience_id=experience.id,
                practice_category=practice.practice_category,
                claim_description=practice.claim_description,
                is_verified=False,
            )
        )

    db.commit()
    db.refresh(experience)

    loaded = (
        _load_full_eco_experience_query(db)
        .filter(EcoExperience.id == experience.id)
        .first()
    )
    return EcoExperienceManagementResponse.model_validate(loaded)


@router.put(
    "/{experience_id}",
    response_model=EcoExperienceManagementResponse,
    summary="Update an owned eco experience listing",
)
def update_eco_experience(
    experience_id: int,
    payload: EcoExperienceUpdate,
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Updates an existing eco experience owned by the caller.
    SECURITY RULES:
    1. Strict provider profile ownership check (403 if caller is not the owner).
    2. Editing an approved listing does NOT automatically grant badges or change status to approved.
    3. If listing was rejected, editing allows the provider to fix deficiencies.
    """
    experience = (
        _load_full_eco_experience_query(db)
        .filter(EcoExperience.id == experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )

    if experience.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this eco experience",
        )

    update_data = payload.model_dump(exclude_unset=True)

    target_category = update_data.get("eco_category", experience.eco_category)
    target_homestay = update_data.get(
        "linked_homestay_id", experience.linked_homestay_id
    )
    _validate_linked_homestay(db, target_homestay, target_category)

    target_min = update_data.get(
        "min_participants", experience.min_participants
    )
    target_max = update_data.get("max_group_size", experience.max_group_size)
    if target_min > target_max:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_participants cannot exceed max_group_size",
        )

    factual = update_data.get(
        "conservation_factual_contribution",
        experience.conservation_factual_contribution,
    )
    marketing = update_data.get(
        "conservation_marketing_statement",
        experience.conservation_marketing_statement,
    )
    if (
        factual
        and marketing
        and str(factual).strip().lower() == str(marketing).strip().lower()
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="conservation_factual_contribution must be distinct from conservation_marketing_statement",
        )

    if {"latitude", "longitude"} & update_data.keys():
        validate_coordinate_pair(
            update_data.get("latitude", experience.latitude),
            update_data.get("longitude", experience.longitude),
        )
    if any(
        field in update_data and update_data[field] != getattr(experience, field)
        for field in ECO_LOCATION_FIELDS
    ):
        if experience.listing_status == "pending_approval":
            raise HTTPException(409, detail="Location is currently under review")
        if experience.listing_status == "approved":
            update_data.update(
                listing_status="draft", is_published=False,
                approval_reviewed_by=None, approval_reviewed_at=None,
            )
        write_review_snapshot(db, experience, ECO_REVIEW_STATE, update_data)
    else:
        for key, value in update_data.items():
            setattr(experience, key, value)

    experience.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(experience)

    loaded = (
        _load_full_eco_experience_query(db)
        .filter(EcoExperience.id == experience.id)
        .first()
    )
    return EcoExperienceManagementResponse.model_validate(loaded)


@router.post(
    "/{experience_id}/submit",
    response_model=EcoExperienceManagementResponse,
    summary="Submit a draft or rejected eco experience for admin approval",
)
def submit_eco_experience_for_approval(
    experience_id: int,
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Transitions an owned 'draft' or 'rejected' eco experience
    to 'pending_approval' for administrative review. Keeps is_published=False.
    """
    experience = (
        _load_full_eco_experience_query(db)
        .filter(EcoExperience.id == experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )

    if experience.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this eco experience",
        )

    if experience.listing_status not in ("draft", "rejected"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only 'draft' or 'rejected' eco experiences can be submitted for approval (current status: '{experience.listing_status}')",
        )

    experience.listing_status = "pending_approval"
    experience.is_published = False
    experience.approval_rejection_reason = None
    experience.updated_at = datetime.now(UTC)

    db.commit()
    db.refresh(experience)

    loaded = (
        _load_full_eco_experience_query(db)
        .filter(EcoExperience.id == experience.id)
        .first()
    )
    return EcoExperienceManagementResponse.model_validate(loaded)


@router.patch(
    "/{experience_id}/publish",
    response_model=EcoExperienceManagementResponse,
    summary="Publish or deactivate an admin-approved eco experience",
)
def toggle_eco_experience_publication(
    experience_id: int,
    payload: EcoExperiencePublishRequest,
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Allows the owning eco_tourism provider to publish or deactivate
    an eco experience ONLY after it has been approved by an administrator.
    Draft, pending, or rejected listings cannot be published.
    """
    experience = (
        _load_full_eco_experience_query(db)
        .filter(EcoExperience.id == experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )

    if experience.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this eco experience",
        )

    if payload.is_published and experience.listing_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot publish an eco experience with status '{experience.listing_status}'. Only admin-approved listings can be published.",
        )

    write_review_snapshot(db, experience, ECO_REVIEW_STATE, {
        "is_published": payload.is_published,
        "listing_status": "approved" if payload.is_published else "deactivated",
        "updated_at": datetime.now(UTC),
    })

    db.commit()
    db.refresh(experience)

    loaded = (
        _load_full_eco_experience_query(db)
        .filter(EcoExperience.id == experience.id)
        .first()
    )
    return EcoExperienceManagementResponse.model_validate(loaded)


# ==============================================================================
# 3. Admin Listing Approval APIs
# ==============================================================================


@admin_eco_router.get(
    "",
    response_model=EcoExperienceAdminPagination,
    summary="List eco experiences for admin review and moderation",
)
def admin_list_eco_experiences(
    db: Annotated[Session, Depends(get_db)],
    status_filter: str | None = Query(
        default=None,
        description="Filter by listing status: 'pending_approval', 'approved', 'rejected', 'draft', 'deactivated'",
    ),
    badge_status: str | None = Query(
        default=None,
        description="Filter by badge status: 'none', 'pending', 'granted', 'rejected', 'revoked'",
    ),
    category: str | None = Query(
        default=None, description="Filter by Eco-Tourism category"
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """
    Admin-only endpoint: Lists eco experiences across all statuses with comprehensive
    moderation metadata, verification requests, and provider contact details.
    """
    query = _load_full_eco_experience_query(db)

    if status_filter and status_filter.strip():
        query = query.filter(
            EcoExperience.listing_status == status_filter.strip().lower()
        )

    if badge_status and badge_status.strip():
        query = query.filter(
            EcoExperience.eco_badge_status == badge_status.strip().lower()
        )

    if category and category.strip():
        query = query.filter(
            func.lower(EcoExperience.eco_category) == category.strip().lower()
        )

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    items = (
        query.order_by(EcoExperience.created_at.desc(), EcoExperience.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return EcoExperienceAdminPagination(
        items=[
            EcoExperienceManagementResponse.model_validate(item)
            for item in items
        ],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@admin_eco_router.get(
    "/overview",
    response_model=EcoAdminOverviewResponse,
    summary="Get platform-wide eco-tourism metrics and breakdown overview",
)
def get_admin_eco_experiences_overview(
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Admin-only endpoint: Returns platform-wide eco-tourism summary statistics (Requirement A08),
    including listing counts by status, verified vs pending badges, active eco provider counts,
    and breakdowns by category and district.
    """
    # 1. Headline listing counts and status breakdown via conditional aggregation
    overall = (
        db.query(
            func.count(EcoExperience.id).label("total"),
            func.sum(
                case((EcoExperience.is_published == True, 1), else_=0)
            ).label("published"),
            func.sum(
                case(
                    (EcoExperience.eco_badge_status == "granted", 1),
                    else_=0,
                )
            ).label("verified_badges"),
            func.sum(
                case(
                    (EcoExperience.eco_badge_status == "pending", 1),
                    else_=0,
                )
            ).label("pending_badges"),
            func.sum(
                case((EcoExperience.listing_status == "draft", 1), else_=0)
            ).label("draft"),
            func.sum(
                case(
                    (EcoExperience.listing_status == "pending_approval", 1),
                    else_=0,
                )
            ).label("pending_approval"),
            func.sum(
                case((EcoExperience.listing_status == "approved", 1), else_=0)
            ).label("approved"),
            func.sum(
                case((EcoExperience.listing_status == "rejected", 1), else_=0)
            ).label("rejected"),
            func.sum(
                case(
                    (EcoExperience.listing_status == "deactivated", 1),
                    else_=0,
                )
            ).label("deactivated"),
        )
        .first()
    )

    total_listings = int(overall.total or 0) if overall else 0
    published_listings = int(overall.published or 0) if overall else 0
    verified_badges = int(overall.verified_badges or 0) if overall else 0
    pending_badges = int(overall.pending_badges or 0) if overall else 0

    by_status = EcoAdminStatusBreakdown(
        draft=int(overall.draft or 0) if overall else 0,
        pending_approval=int(overall.pending_approval or 0) if overall else 0,
        approved=int(overall.approved or 0) if overall else 0,
        rejected=int(overall.rejected or 0) if overall else 0,
        deactivated=int(overall.deactivated or 0) if overall else 0,
    )

    # 2. Count of active approved eco-tourism providers
    total_active_providers = (
        db.query(func.count(ProviderProfile.id))
        .join(User, ProviderProfile.user_id == User.id)
        .filter(
            ProviderProfile.provider_type == "eco_tourism",
            ProviderProfile.approval_status == "approved",
            User.is_active == True,
        )
        .scalar()
    ) or 0

    # 3. Category breakdown — guaranteeing all 6 official categories are represented
    category_counts = dict(
        db.query(
            EcoExperience.eco_category,
            func.count(EcoExperience.id),
        )
        .group_by(EcoExperience.eco_category)
        .all()
    )

    seen_categories = set()
    by_category: list[EcoAdminCategoryBreakdown] = []
    for cat in VALID_ECO_CATEGORIES:
        by_category.append(
            EcoAdminCategoryBreakdown(
                category=cat,
                count=int(category_counts.get(cat, 0)),
            )
        )
        seen_categories.add(cat)

    for cat, count in category_counts.items():
        if cat not in seen_categories and cat is not None:
            by_category.append(
                EcoAdminCategoryBreakdown(
                    category=cat,
                    count=int(count or 0),
                )
            )

    # 4. District breakdown — sorted descending by count, with ascending alphabetical tie-breaking
    district_rows = (
        db.query(
            EcoExperience.district,
            func.count(EcoExperience.id).label("count"),
        )
        .filter(EcoExperience.district.isnot(None))
        .group_by(EcoExperience.district)
        .order_by(
            func.count(EcoExperience.id).desc(),
            EcoExperience.district.asc(),
        )
        .all()
    )
    by_district = [
        EcoAdminDistrictBreakdown(
            district=str(row.district),
            count=int(row.count or 0),
        )
        for row in district_rows
    ]

    return EcoAdminOverviewResponse(
        total_listings=total_listings,
        published_listings=published_listings,
        verified_badges=verified_badges,
        pending_badges=pending_badges,
        total_active_providers=int(total_active_providers),
        by_status=by_status,
        by_category=by_category,
        by_district=by_district,
    )


@admin_eco_router.get(
    "/{experience_id}",
    response_model=EcoExperienceManagementResponse,
    summary="Get full eco experience details for admin review",
)
def admin_get_eco_experience(
    experience_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    experience = (
        _load_full_eco_experience_query(db)
        .filter(EcoExperience.id == experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )
    return EcoExperienceManagementResponse.model_validate(experience)


@admin_eco_router.post(
    "/{experience_id}/approve",
    response_model=EcoExperienceManagementResponse,
    summary="Approve a submitted eco experience listing",
)
def admin_approve_eco_experience(
    experience_id: int,
    request: Request,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Admin-only endpoint: Approves a submitted ('pending_approval') eco experience listing.
    Sets listing_status='approved' and records approval_reviewed_by and approval_reviewed_at.
    SECURITY RULE: Never automatically grants the Eco-Friendly Badge (eco_badge_status remains unchanged).
    """
    experience = (
        _load_full_eco_experience_query(db)
        .filter(EcoExperience.id == experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )

    if experience.listing_status != "pending_approval":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only 'pending_approval' eco experiences can be approved (current status: '{experience.listing_status}')",
        )

    now = datetime.now(UTC)
    write_review_snapshot(db, experience, ECO_REVIEW_STATE, {
        "listing_status": "approved",
        "approval_reviewed_by": admin_user.id,
        "approval_reviewed_at": now,
        "approval_rejection_reason": None,
        "updated_at": now,
    })
    record_admin_audit_log(
        db,
        admin_user_id=admin_user.id,
        action="eco_experience_approved",
        entity_name="eco_experiences",
        entity_id=experience.id,
        details={"title": experience.title, "listing_status": "approved"},
        request=request,
    )

    db.commit()
    db.refresh(experience)

    loaded = (
        _load_full_eco_experience_query(db)
        .filter(EcoExperience.id == experience.id)
        .first()
    )
    return EcoExperienceManagementResponse.model_validate(loaded)


@admin_eco_router.post(
    "/{experience_id}/reject",
    response_model=EcoExperienceManagementResponse,
    summary="Reject a submitted eco experience listing with a stated reason",
)
def admin_reject_eco_experience(
    experience_id: int,
    payload: AdminRejectEcoExperienceRequest,
    request: Request,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Admin-only endpoint: Rejects a submitted ('pending_approval') eco experience listing
    with a mandatory reason, ensuring is_published is False so it never appears publicly.
    """
    experience = (
        _load_full_eco_experience_query(db)
        .filter(EcoExperience.id == experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )

    if experience.listing_status != "pending_approval":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only 'pending_approval' eco experiences can be rejected (current status: '{experience.listing_status}')",
        )

    now = datetime.now(UTC)
    experience.listing_status = "rejected"
    experience.is_published = False
    experience.approval_reviewed_by = admin_user.id
    experience.approval_reviewed_at = now
    experience.approval_rejection_reason = payload.reason
    experience.updated_at = now

    record_admin_audit_log(
        db,
        admin_user_id=admin_user.id,
        action="eco_experience_rejected",
        entity_name="eco_experiences",
        entity_id=experience.id,
        details={"title": experience.title, "reason": payload.reason},
        request=request,
    )

    db.commit()
    db.refresh(experience)

    loaded = (
        _load_full_eco_experience_query(db)
        .filter(EcoExperience.id == experience.id)
        .first()
    )
    return EcoExperienceManagementResponse.model_validate(loaded)


# ==============================================================================
# 4. Eco Safety Rules Endpoints (P0.3)
# ==============================================================================


@router.post(
    "/{experience_id}/safety-rules",
    response_model=EcoSafetyRuleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an eco safety rule or wildlife guideline to an owned listing",
)
def add_eco_safety_rule(
    experience_id: int,
    payload: EcoSafetyRuleInput,
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Allows an approved eco_tourism provider to add
    environmental guidelines, wildlife safety rules, or operator instructions
    to an owned listing.
    """
    experience = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )
    if experience.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this eco experience",
        )

    rule = EcoSafetyRule(
        eco_experience_id=experience.id,
        rule_type=payload.rule_type,
        title=payload.title,
        description=payload.description,
        is_mandatory=payload.is_mandatory,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return EcoSafetyRuleResponse.model_validate(rule)


@router.get(
    "/{experience_id}/safety-rules",
    response_model=list[EcoSafetyRuleResponse],
    summary="Get safety rules and guidelines for an eco experience",
)
def get_eco_safety_rules(
    experience_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Public discovery endpoint: Returns safety rules and environmental guidelines
    for an approved and published eco experience.
    """
    experience = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == experience_id, public_eco())
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )
    if not experience.is_publicly_visible:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )

    rules = (
        db.query(EcoSafetyRule)
        .filter(EcoSafetyRule.eco_experience_id == experience.id)
        .order_by(EcoSafetyRule.id.asc())
        .all()
    )
    return [EcoSafetyRuleResponse.model_validate(r) for r in rules]


@router.delete(
    "/{experience_id}/safety-rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an eco safety rule from an owned listing",
)
def delete_eco_safety_rule(
    experience_id: int,
    rule_id: int,
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Allows the owning provider to remove a safety rule.
    """
    experience = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )
    if experience.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this eco experience",
        )

    rule = (
        db.query(EcoSafetyRule)
        .filter(
            EcoSafetyRule.id == rule_id,
            EcoSafetyRule.eco_experience_id == experience.id,
        )
        .first()
    )
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco safety rule not found",
        )

    db.delete(rule)
    db.commit()
    return None


# ==============================================================================
# 5. Sustainability Practices & Private Evidence Endpoints (P0.3)
# ==============================================================================


@router.post(
    "/{experience_id}/practices",
    response_model=EcoPracticeManagementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a sustainability practice claim to an owned listing",
)
def add_eco_practice(
    experience_id: int,
    payload: EcoPracticeInput,
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Allows the owning eco provider to declare a sustainability practice
    (e.g., waste reduction, renewable energy, community employment, wildlife protection).
    Defaults to is_verified=False.
    """
    experience = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )
    if experience.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this eco experience",
        )

    practice = EcoPractice(
        eco_experience_id=experience.id,
        practice_category=payload.practice_category,
        claim_description=payload.claim_description,
        is_verified=False,
    )
    db.add(practice)
    db.commit()
    db.refresh(practice)
    return EcoPracticeManagementResponse.model_validate(practice)


@router.get(
    "/{experience_id}/practices",
    response_model=list[EcoPracticeManagementResponse],
    summary="List sustainability practices with safe evidence metadata for owned listing",
)
def list_eco_practices(
    experience_id: int,
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Allows the owning provider to view their declared practices
    along with non-sensitive evidence metadata (never reveals raw server filesystem paths).
    """
    experience = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )
    if experience.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this eco experience",
        )

    practices = (
        db.query(EcoPractice)
        .options(selectinload(EcoPractice.evidences))
        .filter(EcoPractice.eco_experience_id == experience.id)
        .order_by(EcoPractice.id.asc())
        .all()
    )
    return [
        EcoPracticeManagementResponse.model_validate(p) for p in practices
    ]


@router.delete(
    "/{experience_id}/practices/{practice_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a sustainability practice from an owned listing",
)
def delete_eco_practice(
    experience_id: int,
    practice_id: int,
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Removes a practice and securely cleans up any attached evidence files from disk.
    """
    experience = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )
    if experience.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this eco experience",
        )

    practice = (
        db.query(EcoPractice)
        .options(selectinload(EcoPractice.evidences))
        .filter(
            EcoPractice.id == practice_id,
            EcoPractice.eco_experience_id == experience.id,
        )
        .first()
    )
    if not practice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco practice not found",
        )

    upload_dir = get_eco_evidence_dir()
    for ev in practice.evidences:
        fp = upload_dir / ev.file_reference
        if fp.is_file():
            try:
                fp.unlink()
            except OSError:
                pass

    db.delete(practice)
    db.commit()
    return None


@router.post(
    "/{experience_id}/practices/{practice_id}/evidence",
    response_model=EcoEvidenceMetadataResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Securely upload private supporting verification evidence",
)
async def upload_eco_evidence(
    experience_id: int,
    practice_id: int,
    file: UploadFile = File(...),
    document_type: str = Form(...),
    notes: str | None = Form(default=None),
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ] = None,
    db: Annotated[Session, Depends(get_db)] = None,
):
    """
    Protected endpoint: Securely uploads supporting evidence for an eco practice.
    SECURITY RULES:
    1. Only the owning approved eco provider may upload evidence.
    2. File type must be in (.pdf, .jpg, .jpeg, .png, .webp).
    3. File size must not exceed 5 MB.
    4. Stored in a private directory outside any public static mounts using a randomized UUID filename.
    5. Returns only metadata (id, document_type, notes, timestamp); NEVER reveals raw filesystem paths.
    """
    experience = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )
    if experience.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this eco experience",
        )

    practice = (
        db.query(EcoPractice)
        .filter(
            EcoPractice.id == practice_id,
            EcoPractice.eco_experience_id == experience.id,
        )
        .first()
    )
    if not practice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco practice not found for this experience",
        )

    cleaned_doc_type = document_type.strip().lower()
    if cleaned_doc_type not in VALID_EVIDENCE_DOCUMENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid document_type '{document_type}'. Allowed: {', '.join(VALID_EVIDENCE_DOCUMENT_TYPES)}",
        )

    original_filename = file.filename or ""
    ext = Path(original_filename).suffix.lower()
    if ext not in ALLOWED_EVIDENCE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file extension '{ext}'. Allowed extensions: {', '.join(sorted(ALLOWED_EVIDENCE_EXTENSIONS))}",
        )

    if file.content_type and file.content_type not in ALLOWED_EVIDENCE_MIMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid Content-Type '{file.content_type}'. Must be one of: {', '.join(sorted(ALLOWED_EVIDENCE_MIMES))}",
        )

    contents = await file.read()
    if len(contents) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded evidence file is empty",
        )
    if len(contents) > MAX_EVIDENCE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Uploaded file exceeds maximum allowed size of {MAX_EVIDENCE_SIZE_BYTES // (1024 * 1024)} MB",
        )

    secure_filename = f"evidence_{uuid.uuid4().hex}{ext}"
    target_dir = get_eco_evidence_dir()
    target_file = target_dir / secure_filename

    with open(target_file, "wb") as f:
        f.write(contents)

    cleaned_notes = notes.strip() if notes and notes.strip() else None

    evidence = EcoEvidence(
        eco_practice_id=practice.id,
        file_reference=secure_filename,
        document_type=cleaned_doc_type,
        notes=cleaned_notes,
    )
    db.add(evidence)
    db.commit()
    db.refresh(evidence)

    return EcoEvidenceMetadataResponse.model_validate(evidence)


@router.delete(
    "/{experience_id}/practices/{practice_id}/evidence/{evidence_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an uploaded evidence document",
)
def delete_eco_evidence(
    experience_id: int,
    practice_id: int,
    evidence_id: int,
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Removes an uploaded evidence record and deletes the physical file.
    """
    experience = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )
    if experience.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this eco experience",
        )

    practice = (
        db.query(EcoPractice)
        .filter(
            EcoPractice.id == practice_id,
            EcoPractice.eco_experience_id == experience.id,
        )
        .first()
    )
    if not practice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco practice not found",
        )

    evidence = (
        db.query(EcoEvidence)
        .filter(
            EcoEvidence.id == evidence_id,
            EcoEvidence.eco_practice_id == practice.id,
        )
        .first()
    )
    if not evidence:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evidence record not found",
        )

    upload_dir = get_eco_evidence_dir()
    fp = upload_dir / evidence.file_reference
    if fp.is_file():
        try:
            fp.unlink()
        except OSError:
            pass

    db.delete(evidence)
    db.commit()
    return None


@router.post(
    "/{experience_id}/submit-verification",
    response_model=EcoExperienceManagementResponse,
    summary="Submit sustainability practices and evidence for Admin Eco Badge review",
)
def submit_eco_verification_request(
    experience_id: int,
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Allows the owning eco provider to formally submit
    their sustainability claims and private evidence for admin Eco-Friendly Badge review.
    Enforces that at least one practice and supporting evidence are provided.
    Sets eco_badge_status='pending' and appends an immutable audit record.
    """
    experience = (
        _load_full_eco_experience_query(db)
        .filter(EcoExperience.id == experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )
    if experience.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this eco experience",
        )

    if experience.eco_badge_status == "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A verification request is already pending review for this listing",
        )

    if not experience.practices:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one sustainability practice must be declared before requesting eco badge verification",
        )

    total_evidence = sum(len(p.evidences) for p in experience.practices)
    if total_evidence == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one sustainability practice must have supporting evidence uploaded before requesting eco badge verification",
        )

    now = datetime.now(UTC)
    experience.eco_badge_status = "pending"
    experience.updated_at = now

    verification = EcoVerification(
        eco_experience_id=experience.id,
        submitted_by_provider_id=provider_profile.id,
        decision="submitted",
        reason="Submitted by provider for eco-friendly badge verification",
        created_at=now,
    )
    db.add(verification)
    db.commit()
    db.refresh(experience)

    loaded = (
        _load_full_eco_experience_query(db)
        .filter(EcoExperience.id == experience.id)
        .first()
    )
    return EcoExperienceManagementResponse.model_validate(loaded)


# ==============================================================================
# 6. Admin Eco Verification Endpoints (P0.3)
# ==============================================================================


@admin_eco_router.get(
    "/{experience_id}/evidence/{evidence_id}",
    summary="Admin secure download/stream of private sustainability evidence document",
)
def admin_get_eco_evidence_file(
    experience_id: int,
    evidence_id: int,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Admin-only endpoint: Securely streams the private evidence document for inspection.
    Enforces path traversal defenses and includes Cache-Control: no-store and nosniff headers.
    """
    experience = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )

    evidence = (
        db.query(EcoEvidence)
        .join(EcoPractice, EcoEvidence.eco_practice_id == EcoPractice.id)
        .filter(
            EcoEvidence.id == evidence_id,
            EcoPractice.eco_experience_id == experience.id,
        )
        .first()
    )
    if not evidence:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evidence document not found for this experience",
        )

    upload_dir = get_eco_evidence_dir()
    file_path = (upload_dir / evidence.file_reference).resolve()

    # Path traversal protection
    if not str(file_path).startswith(str(upload_dir)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid evidence file reference",
        )

    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evidence file not found in storage",
        )

    suffix = file_path.suffix.lower()
    media_type = "application/octet-stream"
    if suffix == ".pdf":
        media_type = "application/pdf"
    elif suffix in (".jpg", ".jpeg"):
        media_type = "image/jpeg"
    elif suffix == ".png":
        media_type = "image/png"
    elif suffix == ".webp":
        media_type = "image/webp"

    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=f"eco_evidence_{experience_id}_{evidence.id}{suffix}",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "X-Content-Type-Options": "nosniff",
        },
    )


@admin_eco_router.post(
    "/{experience_id}/badge-decision",
    response_model=EcoExperienceManagementResponse,
    summary="Admin grant, reject, or revoke Eco-Friendly Badge with recorded reason",
)
@admin_eco_router.post(
    "/{experience_id}/badge/decision",
    response_model=EcoExperienceManagementResponse,
    include_in_schema=False,
)
def admin_badge_decision(
    experience_id: int,
    payload: EcoVerificationDecisionRequest,
    request: Request,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Admin-only endpoint: Grants, rejects, or revokes the Eco-Friendly Badge for an eco listing.
    Records admin reviewer ID, timestamp, and mandatory decision reason in an immutable audit entry.
    SECURITY & VISIBILITY RULES:
    - 'granted': sets eco_badge_status='granted' and marks practices as verified.
    - 'rejected': sets eco_badge_status='rejected' and practices remain unverified.
    - 'revoked': sets eco_badge_status='revoked' and marks practices unverified.
      Badge immediately disappears from public discovery responses.
    """
    experience = (
        _load_full_eco_experience_query(db)
        .filter(EcoExperience.id == experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )

    now = datetime.now(UTC)
    decision = payload.decision
    previous_badge_status = experience.eco_badge_status

    if decision == "granted":
        experience.eco_badge_status = "granted"
        for practice in experience.practices:
            practice.is_verified = True
    elif decision == "rejected":
        experience.eco_badge_status = "rejected"
        for practice in experience.practices:
            practice.is_verified = False
    elif decision == "revoked":
        experience.eco_badge_status = "revoked"
        for practice in experience.practices:
            practice.is_verified = False
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid decision '{decision}'. Allowed: granted, rejected, revoked",
        )

    experience.updated_at = now

    verification = EcoVerification(
        eco_experience_id=experience.id,
        submitted_by_provider_id=experience.provider_profile_id,
        reviewed_by_admin_id=admin_user.id,
        decision=decision,
        reason=payload.reason,
        valid_until=payload.valid_until,
        created_at=now,
    )
    db.add(verification)
    record_admin_audit_log(
        db,
        admin_user_id=admin_user.id,
        action=f"eco_badge_{decision}",
        entity_name="eco_experiences",
        entity_id=experience.id,
        details={
            "previous_badge_status": previous_badge_status,
            "new_badge_status": decision,
            "reason": payload.reason,
        },
        request=request,
    )
    db.commit()
    db.refresh(experience)

    # Post-commit notification dispatch (best-effort, non-blocking)
    # Avoid duplicate notifications for unchanged decisions:
    if previous_badge_status != decision:
        try:
            provider_user = (
                db.query(User)
                .join(ProviderProfile, ProviderProfile.user_id == User.id)
                .filter(ProviderProfile.id == experience.provider_profile_id)
                .first()
            )
            if provider_user and provider_user.email:
                business_name = (
                    experience.provider_profile.business_name
                    if experience.provider_profile
                    else None
                )
                send_eco_badge_decision_email(
                    to_email=provider_user.email,
                    experience_title=experience.title,
                    decision=decision,
                    provider_name=provider_user.full_name,
                    business_name=business_name,
                    reason=payload.reason,
                    valid_until=payload.valid_until,
                )
        except Exception as exc:
            logger.error(
                "Failed to dispatch eco badge decision notification for experience_id %s: %s",
                experience.id,
                exc.__class__.__name__,
            )

    loaded = (
        _load_full_eco_experience_query(db)
        .filter(EcoExperience.id == experience.id)
        .first()
    )
    return EcoExperienceManagementResponse.model_validate(loaded)


@admin_eco_router.get(
    "/{experience_id}/verifications",
    response_model=list[EcoVerificationHistoryResponse],
    summary="List complete eco badge verification decision history",
)
def admin_get_verification_history(
    experience_id: int,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Admin-only endpoint: Retrieves the immutable verification decision history
    for an eco experience, including submission requests, grants, rejections, and revocations.
    """
    experience = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )

    verifications = (
        db.query(EcoVerification)
        .filter(EcoVerification.eco_experience_id == experience.id)
        .order_by(EcoVerification.created_at.desc(), EcoVerification.id.desc())
        .all()
    )
    return [
        EcoVerificationHistoryResponse.model_validate(v)
        for v in verifications
    ]


# ==============================================================================
# 7. Eco Experience Photo Gallery Management APIs (P0.5)
# ==============================================================================


@router.post(
    "/{experience_id}/images",
    response_model=EcoExperienceImageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an image to an eco experience photo gallery",
)
async def upload_eco_experience_image(
    experience_id: int,
    file: UploadFile = File(...),
    caption: str | None = Form(default=None),
    is_primary: bool = Form(default=False),
    sort_order: int = Form(default=0),
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ] = None,
    db: Annotated[Session, Depends(get_db)] = None,
):
    """
    Protected endpoint: Allows the owning eco_tourism provider to upload gallery photos.
    SECURITY & VALIDATION RULES:
    1. Authenticated provider must own the EcoExperience.
    2. Enforces maximum image count per experience (20).
    3. Validates file extension, MIME type, and maximum file size (<= 5 MB).
    4. Pillow structural verification and raster decode checks:
       - Confirms format is JPEG, PNG, or WebP.
       - Confirms dimensions are within sensible limits (50x50px to 8000x8000px).
       - Rejects corrupted or malicious files (e.g. decompression bombs or script payloads).
    5. Generates randomized server-side UUID filename (never trusts client filename).
    6. Stores in dedicated public gallery directory (uploads/eco_experiences/).
    7. Prevents orphan files if database commit fails.
    """
    experience = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )
    if experience.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this eco experience",
        )

    current_image_count = (
        db.query(EcoExperienceImage)
        .filter(EcoExperienceImage.eco_experience_id == experience.id)
        .count()
    )
    if current_image_count >= MAX_IMAGES_PER_EXPERIENCE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum image limit of {MAX_IMAGES_PER_EXPERIENCE} reached for this eco experience",
        )

    clean_caption = caption.strip() if caption and caption.strip() else None
    if clean_caption and len(clean_caption) > 255:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Caption cannot exceed 255 characters",
        )

    if sort_order < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sort_order cannot be negative",
        )

    original_filename = file.filename or ""
    client_ext = Path(original_filename).suffix.lower()
    if client_ext and client_ext not in ALLOWED_ECO_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file extension '{client_ext}'. Allowed extensions: {', '.join(sorted(ALLOWED_ECO_IMAGE_EXTENSIONS))}",
        )

    if file.content_type and file.content_type not in ALLOWED_ECO_IMAGE_MIMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid Content-Type '{file.content_type}'. Must be one of: {', '.join(sorted(ALLOWED_ECO_IMAGE_MIMES))}",
        )

    try:
        contents = await file.read()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not read uploaded image file",
        ) from exc

    if len(contents) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded image file is empty",
        )
    if len(contents) > MAX_ECO_IMAGE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Uploaded file exceeds maximum allowed size of {MAX_ECO_IMAGE_SIZE_BYTES // (1024 * 1024)} MB",
        )

    try:
        with Image.open(io.BytesIO(contents)) as img:
            img.verify()
            detected_format = img.format
            if detected_format not in ALLOWED_ECO_IMAGE_FORMATS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unsupported image format '{detected_format}'. Supported formats: JPEG, PNG, WebP.",
                )

        with Image.open(io.BytesIO(contents)) as img:
            img.load()
            width, height = img.size
            if width < MIN_ECO_IMAGE_DIMENSION or height < MIN_ECO_IMAGE_DIMENSION:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Image dimensions ({width}x{height}) are too small. Minimum required: {MIN_ECO_IMAGE_DIMENSION}x{MIN_ECO_IMAGE_DIMENSION} pixels.",
                )
            if width > MAX_ECO_IMAGE_DIMENSION or height > MAX_ECO_IMAGE_DIMENSION:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Image dimensions ({width}x{height}) exceed maximum allowed {MAX_ECO_IMAGE_DIMENSION}x{MAX_ECO_IMAGE_DIMENSION} pixels.",
                )
    except HTTPException:
        raise
    except (UnidentifiedImageError, SyntaxError, ValueError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or corrupted image content",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to decode image content",
        ) from exc

    ext = ".jpg" if detected_format == "JPEG" else f".{detected_format.lower()}"
    safe_filename = f"eco_img_{experience.id}_{uuid.uuid4().hex}{ext}"
    target_dir = get_eco_experience_images_dir()
    target_file = (target_dir / safe_filename).resolve()

    if not str(target_file).startswith(str(target_dir.resolve())):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file path detected",
        )

    with open(target_file, "wb") as f:
        f.write(contents)

    if is_primary:
        db.query(EcoExperienceImage).filter(
            EcoExperienceImage.eco_experience_id == experience.id,
            EcoExperienceImage.is_primary == True,
        ).update({"is_primary": False})

    image_url = f"/uploads/eco_experiences/{safe_filename}"
    new_image = EcoExperienceImage(
        eco_experience_id=experience.id,
        image_url=image_url,
        caption=clean_caption,
        is_primary=bool(is_primary),
        sort_order=int(sort_order),
    )

    try:
        db.add(new_image)
        experience.updated_at = datetime.now(UTC)
        db.commit()
        db.refresh(new_image)
    except Exception:
        db.rollback()
        if target_file.is_file():
            try:
                target_file.unlink()
            except OSError:
                pass
        raise

    return EcoExperienceImageResponse.model_validate(new_image)


@router.delete(
    "/{experience_id}/images/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an image from an eco experience photo gallery",
)
def delete_eco_experience_image(
    experience_id: int,
    image_id: int,
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Removes an image database record and securely deletes the physical file.
    SECURITY & INTEGRITY RULES:
    1. Authenticated provider must own the EcoExperience.
    2. Image must belong to the specified EcoExperience.
    3. Safely removes the file from disk with path-traversal protection.
    4. Handles already-missing files gracefully without crashing.
    """
    experience = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )
    if experience.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this eco experience",
        )

    image = (
        db.query(EcoExperienceImage)
        .filter(
            EcoExperienceImage.id == image_id,
            EcoExperienceImage.eco_experience_id == experience.id,
        )
        .first()
    )
    if not image:
        other_image = (
            db.query(EcoExperienceImage)
            .filter(EcoExperienceImage.id == image_id)
            .first()
        )
        if other_image:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Image does not belong to this eco experience",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience image not found",
        )

    if image.image_url:
        upload_dir = get_eco_experience_images_dir().resolve()
        filename = os.path.basename(image.image_url)
        target_path = (upload_dir / filename).resolve()
        if (
            str(target_path).startswith(str(upload_dir))
            and target_path.is_file()
            and target_path != upload_dir
        ):
            try:
                target_path.unlink()
            except OSError:
                pass

    db.delete(image)
    experience.updated_at = datetime.now(UTC)
    db.commit()
    return None


def eco_media_visibility(
    user: Annotated[User | None, Depends(get_optional_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    if user is None:
        return public_eco()
    if user.role == "admin":
        return true()
    if user.role == "provider":
        own_profile = db.query(ProviderProfile.id).filter(
            ProviderProfile.user_id == user.id,
            ProviderProfile.provider_type == "eco_tourism",
            ProviderProfile.approval_status == "approved",
        )
        return or_(public_eco(), EcoExperience.provider_profile_id.in_(own_profile.statement))
    return public_eco()


EcoMediaVisibility = Annotated[ColumnElement[bool], Depends(eco_media_visibility)]


@router.get("/{experience_id}/images", response_model=list[EcoExperienceImageResponse])
def list_eco_experience_images(
    experience_id: int, db: Annotated[Session, Depends(get_db)],
    visible: EcoMediaVisibility,
):
    experience = db.query(EcoExperience.id).filter(
        EcoExperience.id == experience_id, visible,
    ).first()
    if experience is None:
        raise HTTPException(404, detail="Eco experience not found")
    images = (
        db.query(EcoExperienceImage)
        .filter(EcoExperienceImage.eco_experience_id == experience_id)
        .order_by(EcoExperienceImage.sort_order.asc(), EcoExperienceImage.id.asc())
        .all()
    )
    return [EcoExperienceImageResponse.model_validate(img) for img in images]


@router.get("/images/{filename}")
@uploads_router.get("/uploads/eco_experiences/{filename}")
def get_eco_experience_image(
    filename: str, db: Annotated[Session, Depends(get_db)], visible: EcoMediaVisibility,
):
    root = get_eco_experience_images_dir().resolve()
    target = (root / filename).resolve()
    if (
        filename != os.path.basename(filename) or target.parent != root
        or target.suffix.lower() not in ALLOWED_ECO_IMAGE_EXTENSIONS
    ):
        raise HTTPException(404, detail="Eco experience image not found")
    registered = db.query(EcoExperienceImage.id).join(EcoExperience).filter(
        EcoExperienceImage.image_url.in_((
            f"/uploads/eco_experiences/{filename}",
            f"/api/v1/eco-experiences/images/{filename}",
        )),
        visible,
    ).first()
    if not registered or not target.is_file():
        raise HTTPException(404, detail="Eco experience image not found")
    ext = target.suffix.lower()
    media_type = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
    return FileResponse(target, media_type=media_type, headers={"Cache-Control": "private, no-store"})
