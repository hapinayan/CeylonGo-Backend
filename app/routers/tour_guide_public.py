"""
Public / Traveler Discovery Router for Local Tour Guide Module 05 (Phase 2).

Endpoints under /api/v1/tour-guides:
- GET  ""                                 Discover, search, filter, sort & paginate approved guides
- GET  "/categories"                      List official 4 Local Tour Guide categories
- GET  "/packages/{package_id}"           View an active public tour package and its guide summary
- GET  "/{guide_id}"                      View full public guide profile (verified badge, gallery,
                                          languages, service areas, verified credentials, packages)
- GET  "/{guide_id}/packages"             List active packages for an approved guide
- GET  "/{guide_id}/availability"         View anonymized public availability calendar
- POST "/{guide_id}/price-preview"        Server-authoritative package / hourly / daily price preview
- GET  "/{guide_id}/contact"              Privacy-aware guide contact information
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.database.connection import get_db
from app.models.user import User
from app.schemas.tour_guide import (
    GuideCategoryInfo,
    PackagePricingType,
    PriceFilterBasis,
    PublicGuideAvailabilityCalendarResponse,
    PublicGuideContactResponse,
    PublicGuideSortBy,
    PublicTourGuideDetailResponse,
    PublicTourGuideListResponse,
    PublicTourGuidePackageDetailResponse,
    PublicTourGuidePackageResponse,
    TourGuidePricePreviewRequest,
    TourGuidePricePreviewResponse,
)
from app.services.tour_guide_public import (
    calculate_tour_guide_price_preview,
    get_public_guide_availability_calendar,
    get_public_guide_contact,
    get_public_package_detail,
    get_public_tour_guide_detail,
    list_public_guide_packages,
    list_public_tour_guides,
)
from app.services.tour_guide_sync import GUIDE_CATEGORIES

router = APIRouter(
    prefix="/api/v1/tour-guides",
    tags=["Tour Guides"],
)

DatabaseSession = Annotated[Session, Depends(get_db)]

_optional_bearer_scheme = HTTPBearer(
    scheme_name="OptionalHTTPBearer",
    description="Optional JWT Bearer token for authenticated direct contact access",
    auto_error=False,
)


def get_optional_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_optional_bearer_scheme),
    ],
    db: DatabaseSession,
) -> User | None:
    """
    Resolve an authenticated active User when a Bearer token is supplied,
    or return None for anonymous visitors. Rejects tampered/expired/inactive tokens.
    """
    if credentials is None or not credentials.credentials:
        return None

    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id_raw = payload.get("sub")
    try:
        user_id = int(user_id_raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive. Please contact support.",
        )
    return user


# =============================================================================
# 1. PUBLIC DISCOVERY & COMBINED SEARCH (TR-01, TR-02, TR-03, TR-06, TR-07, TR-09, TR-20)
# =============================================================================

@router.get(
    "",
    response_model=PublicTourGuideListResponse,
    summary="Discover and filter approved, active local tour guides",
)
def discover_tour_guides(
    db: DatabaseSession,
    q: Annotated[str | None, Query(max_length=120, description="Keyword search")] = None,
    location: Annotated[
        str | None,
        Query(max_length=100, description="Match base or covered district/city/province"),
    ] = None,
    district: Annotated[str | None, Query(max_length=100)] = None,
    city: Annotated[str | None, Query(max_length=100)] = None,
    language: Annotated[
        str | None,
        Query(max_length=50, description="Language code (en/si/ta) or name"),
    ] = None,
    category: Annotated[
        str | None,
        Query(max_length=60, description="Guide category slug or canonical name"),
    ] = None,
    min_experience: Annotated[int | None, Query(ge=0, le=100)] = None,
    min_price: Annotated[Decimal | None, Query(ge=0)] = None,
    max_price: Annotated[Decimal | None, Query(ge=0)] = None,
    price_basis: PriceFilterBasis = "daily",
    currency: Annotated[str | None, Query(min_length=3, max_length=3)] = None,
    verified_only: bool = False,
    min_rating: Annotated[Decimal | None, Query(ge=0, le=5)] = None,
    available_only: bool = False,
    available_date: date | None = None,
    group_size: Annotated[int | None, Query(ge=1, le=500)] = None,
    sort_by: PublicGuideSortBy = "recommended",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return list_public_tour_guides(
        db,
        q=q,
        location=location,
        district=district,
        city=city,
        language=language,
        category=category,
        min_experience=min_experience,
        min_price=min_price,
        max_price=max_price,
        price_basis=price_basis,
        currency=currency,
        verified_only=verified_only,
        min_rating=min_rating,
        available_only=available_only,
        available_date=available_date,
        group_size=group_size,
        sort_by=sort_by,
        page=page,
        page_size=page_size,
    )


# =============================================================================
# 2. STATIC ROUTES (MUST PRECEDE /{guide_id})
# =============================================================================

@router.get(
    "/categories",
    response_model=list[GuideCategoryInfo],
    summary="List official Local Tour Guide categories",
)
def list_tour_guide_categories():
    return [GuideCategoryInfo(**cat) for cat in GUIDE_CATEGORIES]


@router.get(
    "/packages/{package_id}",
    response_model=PublicTourGuidePackageDetailResponse,
    summary="View details of an active public tour guide package",
)
def get_public_tour_guide_package(
    package_id: Annotated[int, Path(gt=0)],
    db: DatabaseSession,
):
    return get_public_package_detail(db, package_id)


# =============================================================================
# 3. PUBLIC GUIDE DETAIL, PACKAGES, AVAILABILITY, PRICE PREVIEW & CONTACT
# =============================================================================

@router.get(
    "/{guide_id}",
    response_model=PublicTourGuideDetailResponse,
    summary="View public profile of an approved, active local tour guide",
)
def get_public_tour_guide(
    guide_id: Annotated[int, Path(gt=0)],
    db: DatabaseSession,
):
    return get_public_tour_guide_detail(db, guide_id)


@router.get(
    "/{guide_id}/packages",
    response_model=list[PublicTourGuidePackageResponse],
    summary="List active tour packages offered by an approved guide",
)
def list_public_tour_guide_packages(
    guide_id: Annotated[int, Path(gt=0)],
    db: DatabaseSession,
    duration_category: Literal["half_day", "full_day", "multi_day"] | None = None,
    pricing_type: PackagePricingType | None = None,
    max_price: Annotated[Decimal | None, Query(ge=0)] = None,
    min_group_size: Annotated[int | None, Query(ge=1)] = None,
):
    return list_public_guide_packages(
        db,
        guide_id,
        duration_category=duration_category,
        pricing_type=pricing_type,
        max_price=max_price,
        min_group_size=min_group_size,
    )


@router.get(
    "/{guide_id}/availability",
    response_model=PublicGuideAvailabilityCalendarResponse,
    summary="View anonymized public availability calendar for an approved guide",
)
def get_public_tour_guide_availability(
    guide_id: Annotated[int, Path(gt=0)],
    db: DatabaseSession,
    date_from: date | None = None,
    date_to: date | None = None,
):
    return get_public_guide_availability_calendar(
        db,
        guide_id,
        date_from=date_from,
        date_to=date_to,
    )


@router.post(
    "/{guide_id}/price-preview",
    response_model=TourGuidePricePreviewResponse,
    summary="Calculate server-authoritative tour guide package or rate price preview",
)
def preview_tour_guide_price(
    guide_id: Annotated[int, Path(gt=0)],
    payload: TourGuidePricePreviewRequest,
    db: DatabaseSession,
):
    return calculate_tour_guide_price_preview(db, guide_id, payload)


@router.get(
    "/{guide_id}/contact",
    response_model=PublicGuideContactResponse,
    summary="Get privacy-aware contact information for an approved local tour guide",
)
def get_public_tour_guide_contact(
    guide_id: Annotated[int, Path(gt=0)],
    db: DatabaseSession,
    current_user: Annotated[User | None, Depends(get_optional_current_user)],
):
    return get_public_guide_contact(
        db,
        guide_id,
        authenticated_user=current_user,
    )
