from datetime import date
import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.tour_guide import (
    TourGuide,
    TourGuideAvailability,
    TourGuideBooking,
    TourGuideCredential,
    TourGuideLanguage,
    TourGuidePackage,
    TourGuideServiceArea,
)
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.tour_guide import (
    TourGuideAvailabilityCreate,
    TourGuideAvailabilityResponse,
    TourGuideBookingProviderItem,
    TourGuideBookingProviderListResponse,
    TourGuideCredentialCreate,
    TourGuideCredentialResponse,
    TourGuideLanguageCreate,
    TourGuideLanguageResponse,
    TourGuidePackageCreate,
    TourGuidePackageResponse,
    TourGuidePackageUpdate,
    TourGuideProfileCreate,
    TourGuideProfileResponse,
    TourGuideProfileUpdate,
    TourGuideServiceAreaCreate,
    TourGuideServiceAreaResponse,
)

router = APIRouter(
    prefix="/api/v1/tour-guides",
    tags=["Tour Guides"],
)


# ==========================================
# AUTH / PROVIDER HELPERS
# ==========================================

def get_tour_guide_provider_and_profile(
    db: Session,
    current_user: User,
) -> tuple[ProviderProfile, TourGuide]:
    provider = (
        db.query(ProviderProfile)
        .filter(
            ProviderProfile.user_id == current_user.id,
            ProviderProfile.provider_type == "tour_guide",
            ProviderProfile.approval_status == "approved",
        )
        .first()
    )
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Approved tour_guide provider profile required",
        )

    guide = (
        db.query(TourGuide)
        .options(
            joinedload(TourGuide.languages),
            joinedload(TourGuide.service_areas),
            joinedload(TourGuide.packages),
        )
        .filter(TourGuide.provider_profile_id == provider.id)
        .first()
    )
    if not guide:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide profile has not been created yet",
        )

    return provider, guide


# ==========================================
# GUIDE PROFILE MANAGEMENT
# ==========================================

@router.post(
    "",
    response_model=TourGuideProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create tour guide profile",
)
def create_tour_guide_profile(
    payload: TourGuideProfileCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    provider = (
        db.query(ProviderProfile)
        .filter(
            ProviderProfile.user_id == current_user.id,
            ProviderProfile.provider_type == "tour_guide",
            ProviderProfile.approval_status == "approved",
        )
        .first()
    )
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Approved tour_guide provider profile required",
        )

    existing = (
        db.query(TourGuide)
        .filter(TourGuide.provider_profile_id == provider.id)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tour guide profile already exists for this provider",
        )

    guide = TourGuide(
        provider_profile_id=provider.id,
        bio=payload.bio,
        years_of_experience=payload.years_of_experience,
        specialties=payload.specialties,
        license_number=payload.license_number,
        license_type=payload.license_type,
        license_expiry_date=payload.license_expiry_date,
        hourly_rate=payload.hourly_rate,
        daily_rate=payload.daily_rate,
        currency=payload.currency,
        base_city=payload.base_city,
        base_district=payload.base_district,
        base_latitude=payload.base_latitude,
        base_longitude=payload.base_longitude,
        max_group_size=payload.max_group_size,
        cancellation_policy=payload.cancellation_policy,
        free_cancellation_hours=payload.free_cancellation_hours,
        is_available=payload.is_available,
        is_active=False,
        approval_status="draft",
        verification_status="unverified",
    )

    try:
        db.add(guide)
        db.commit()
        db.refresh(guide)
    except Exception:
        db.rollback()
        raise

    return guide


@router.get(
    "/me",
    response_model=TourGuideProfileResponse,
    summary="Get own tour guide profile",
)
def get_my_tour_guide_profile(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)
    return guide


@router.put(
    "/me",
    response_model=TourGuideProfileResponse,
    summary="Update own tour guide profile",
)
def update_my_tour_guide_profile(
    payload: TourGuideProfileUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)

    update_dict = payload.model_dump(exclude_unset=True)
    for field, val in update_dict.items():
        setattr(guide, field, val)

    try:
        db.commit()
        db.refresh(guide)
    except Exception:
        db.rollback()
        raise

    return guide


# ==========================================
# LANGUAGES
# ==========================================

@router.post(
    "/me/languages",
    response_model=TourGuideLanguageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add spoken language",
)
def add_tour_guide_language(
    payload: TourGuideLanguageCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)

    existing = (
        db.query(TourGuideLanguage)
        .filter(
            TourGuideLanguage.tour_guide_id == guide.id,
            TourGuideLanguage.language_code == payload.language_code.strip().lower(),
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Language code '{payload.language_code}' already registered for this guide",
        )

    lang = TourGuideLanguage(
        tour_guide_id=guide.id,
        language_code=payload.language_code.strip().lower(),
        language_name=payload.language_name.strip(),
        proficiency_level=payload.proficiency_level,
        is_primary=payload.is_primary,
    )

    try:
        db.add(lang)
        db.commit()
        db.refresh(lang)
    except Exception:
        db.rollback()
        raise

    return lang


@router.get(
    "/me/languages",
    response_model=list[TourGuideLanguageResponse],
    summary="List languages spoken by guide",
)
def list_tour_guide_languages(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)
    return (
        db.query(TourGuideLanguage)
        .filter(TourGuideLanguage.tour_guide_id == guide.id)
        .order_by(TourGuideLanguage.is_primary.desc(), TourGuideLanguage.language_name.asc())
        .all()
    )


@router.delete(
    "/me/languages/{language_id}",
    summary="Delete language from guide profile",
)
def delete_tour_guide_language(
    language_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)

    lang = (
        db.query(TourGuideLanguage)
        .filter(
            TourGuideLanguage.id == language_id,
            TourGuideLanguage.tour_guide_id == guide.id,
        )
        .first()
    )
    if not lang:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Language not found on guide profile",
        )

    db.delete(lang)
    db.commit()
    return {"message": "Language deleted successfully", "id": language_id}


# ==========================================
# SERVICE AREAS
# ==========================================

@router.post(
    "/me/service-areas",
    response_model=TourGuideServiceAreaResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add guide service area",
)
def add_tour_guide_service_area(
    payload: TourGuideServiceAreaCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)

    existing = (
        db.query(TourGuideServiceArea)
        .filter(
            TourGuideServiceArea.tour_guide_id == guide.id,
            TourGuideServiceArea.district == payload.district.strip(),
            TourGuideServiceArea.city == payload.city.strip(),
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Service area already registered for this guide",
        )

    area = TourGuideServiceArea(
        tour_guide_id=guide.id,
        province=payload.province.strip(),
        district=payload.district.strip(),
        city=payload.city.strip(),
        is_primary=payload.is_primary,
    )

    try:
        db.add(area)
        db.commit()
        db.refresh(area)
    except Exception:
        db.rollback()
        raise

    return area


@router.get(
    "/me/service-areas",
    response_model=list[TourGuideServiceAreaResponse],
    summary="List guide service areas",
)
def list_tour_guide_service_areas(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)
    return (
        db.query(TourGuideServiceArea)
        .filter(TourGuideServiceArea.tour_guide_id == guide.id)
        .order_by(TourGuideServiceArea.is_primary.desc(), TourGuideServiceArea.district.asc())
        .all()
    )


@router.delete(
    "/me/service-areas/{area_id}",
    summary="Delete service area from guide profile",
)
def delete_tour_guide_service_area(
    area_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)

    area = (
        db.query(TourGuideServiceArea)
        .filter(
            TourGuideServiceArea.id == area_id,
            TourGuideServiceArea.tour_guide_id == guide.id,
        )
        .first()
    )
    if not area:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service area not found on guide profile",
        )

    db.delete(area)
    db.commit()
    return {"message": "Service area deleted successfully", "id": area_id}


# ==========================================
# TOUR PACKAGES
# ==========================================

@router.post(
    "/me/packages",
    response_model=TourGuidePackageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create tour package",
)
def create_tour_guide_package(
    payload: TourGuidePackageCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)

    pkg = TourGuidePackage(
        tour_guide_id=guide.id,
        title=payload.title.strip(),
        description=payload.description.strip(),
        itinerary=payload.itinerary,
        duration_hours=payload.duration_hours,
        pricing_type=payload.pricing_type,
        price=payload.price,
        currency=payload.currency,
        max_people=payload.max_people,
        included_services=payload.included_services,
        excluded_services=payload.excluded_services,
        meeting_point=payload.meeting_point.strip(),
        meeting_latitude=payload.meeting_latitude,
        meeting_longitude=payload.meeting_longitude,
        is_active=payload.is_active,
    )

    try:
        db.add(pkg)
        db.commit()
        db.refresh(pkg)
    except Exception:
        db.rollback()
        raise

    return pkg


@router.get(
    "/me/packages",
    response_model=list[TourGuidePackageResponse],
    summary="List tour packages for current guide",
)
def list_my_tour_guide_packages(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)
    return (
        db.query(TourGuidePackage)
        .filter(TourGuidePackage.tour_guide_id == guide.id)
        .order_by(TourGuidePackage.created_at.desc())
        .all()
    )


@router.put(
    "/me/packages/{package_id}",
    response_model=TourGuidePackageResponse,
    summary="Update tour package",
)
def update_tour_guide_package(
    package_id: int,
    payload: TourGuidePackageUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)

    pkg = (
        db.query(TourGuidePackage)
        .filter(
            TourGuidePackage.id == package_id,
            TourGuidePackage.tour_guide_id == guide.id,
        )
        .first()
    )
    if not pkg:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour package not found or not owned by you",
        )

    update_dict = payload.model_dump(exclude_unset=True)
    for field, val in update_dict.items():
        setattr(pkg, field, val)

    try:
        db.commit()
        db.refresh(pkg)
    except Exception:
        db.rollback()
        raise

    return pkg


@router.delete(
    "/me/packages/{package_id}",
    summary="Delete tour package",
)
def delete_tour_guide_package(
    package_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)

    pkg = (
        db.query(TourGuidePackage)
        .filter(
            TourGuidePackage.id == package_id,
            TourGuidePackage.tour_guide_id == guide.id,
        )
        .first()
    )
    if not pkg:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour package not found or not owned by you",
        )

    db.delete(pkg)
    db.commit()
    return {"message": "Tour package deleted successfully", "id": package_id}


# ==========================================
# CREDENTIALS & SLTDA VERIFICATION (TG-03)
# ==========================================

@router.post(
    "/me/credentials",
    response_model=TourGuideCredentialResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add tour guide credential / SLTDA license",
)
def create_tour_guide_credential(
    payload: TourGuideCredentialCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)

    cred = TourGuideCredential(
        tour_guide_id=guide.id,
        credential_type=payload.credential_type,
        title=payload.title,
        issuing_authority=payload.issuing_authority,
        certificate_number=payload.certificate_number,
        issue_date=payload.issue_date,
        expiry_date=payload.expiry_date,
        file_reference=payload.file_reference,
        is_confidential=payload.is_confidential,
        verification_status="pending",
    )
    db.add(cred)

    # Sync license details with guide profile if SLTDA license
    if payload.credential_type == "sltda_license" and payload.certificate_number:
        guide.license_number = payload.certificate_number
        if payload.expiry_date:
            guide.license_expiry_date = payload.expiry_date

    db.commit()
    db.refresh(cred)
    return cred


@router.get(
    "/me/credentials",
    response_model=list[TourGuideCredentialResponse],
    summary="List tour guide credentials",
)
def list_tour_guide_credentials(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)
    return (
        db.query(TourGuideCredential)
        .filter(TourGuideCredential.tour_guide_id == guide.id)
        .order_by(TourGuideCredential.created_at.desc())
        .all()
    )


@router.delete(
    "/me/credentials/{credential_id}",
    summary="Delete tour guide credential",
)
def delete_tour_guide_credential(
    credential_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)
    cred = (
        db.query(TourGuideCredential)
        .filter(
            TourGuideCredential.id == credential_id,
            TourGuideCredential.tour_guide_id == guide.id,
        )
        .first()
    )
    if not cred:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential not found or not owned by you",
        )
    db.delete(cred)
    db.commit()
    return {"message": "Credential deleted successfully", "id": credential_id}


# ==========================================
# AVAILABILITY SCHEDULE (TG-05)
# ==========================================

@router.post(
    "/me/availability",
    response_model=TourGuideAvailabilityResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Set tour guide availability or blocked date",
)
def create_tour_guide_availability(
    payload: TourGuideAvailabilityCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)

    avail = TourGuideAvailability(
        tour_guide_id=guide.id,
        available_date=payload.available_date,
        start_time=payload.start_time,
        end_time=payload.end_time,
        is_available=payload.is_available,
        reason=payload.reason,
    )
    db.add(avail)
    db.commit()
    db.refresh(avail)
    return avail


@router.get(
    "/me/availability",
    response_model=list[TourGuideAvailabilityResponse],
    summary="List tour guide availability entries",
)
def list_tour_guide_availability(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    start_date: Annotated[date | None, Query(description="Filter from date")] = None,
    end_date: Annotated[date | None, Query(description="Filter to date")] = None,
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)
    query = db.query(TourGuideAvailability).filter(
        TourGuideAvailability.tour_guide_id == guide.id
    )
    if start_date:
        query = query.filter(TourGuideAvailability.available_date >= start_date)
    if end_date:
        query = query.filter(TourGuideAvailability.available_date <= end_date)

    return (
        query.order_by(
            TourGuideAvailability.available_date.asc(),
            TourGuideAvailability.start_time.asc(),
        )
        .all()
    )


@router.delete(
    "/me/availability/{availability_id}",
    summary="Delete tour guide availability entry",
)
def delete_tour_guide_availability(
    availability_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)
    avail = (
        db.query(TourGuideAvailability)
        .filter(
            TourGuideAvailability.id == availability_id,
            TourGuideAvailability.tour_guide_id == guide.id,
        )
        .first()
    )
    if not avail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Availability entry not found or not owned by you",
        )
    db.delete(avail)
    db.commit()
    return {"message": "Availability entry deleted successfully", "id": availability_id}


# ==========================================
# PROVIDER BOOKING WORKSPACE FOR TOUR GUIDES
# ==========================================

@router.get(
    "/me/bookings",
    response_model=TourGuideBookingProviderListResponse,
    summary="Provider view of guide bookings",
)
def list_my_tour_guide_bookings(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    booking_status: Annotated[str | None, Query()] = None,
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)

    query = db.query(TourGuideBooking).filter(TourGuideBooking.tour_guide_id == guide.id)

    if booking_status and booking_status.strip():
        clean_status = booking_status.strip().lower()
        query = query.filter(TourGuideBooking.booking_status == clean_status)

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size))

    bookings = (
        query.order_by(TourGuideBooking.start_datetime.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for b in bookings:
        traveler = db.get(User, b.traveler_id)
        pkg = db.get(TourGuidePackage, b.package_id) if b.package_id else None

        items.append(
            TourGuideBookingProviderItem(
                id=b.id,
                tour_guide_id=b.tour_guide_id,
                traveler_id=b.traveler_id,
                package_id=b.package_id,
                booking_date=b.booking_date,
                start_datetime=b.start_datetime,
                end_datetime=b.end_datetime,
                number_of_people=b.number_of_people,
                pickup_location=b.pickup_location,
                special_requests=b.special_requests,
                total_price=b.total_price,
                currency=b.currency,
                booking_status=b.booking_status,
                payment_method=b.payment_method,
                payment_status=b.payment_status,
                package_title=pkg.title if pkg else None,
                traveler_name=traveler.full_name if traveler else None,
                traveler_email=traveler.email if traveler else None,
            )
        )

    return TourGuideBookingProviderListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post(
    "/me/bookings/{booking_id}/confirm",
    response_model=TourGuideBookingProviderItem,
    summary="Guide confirms pending booking",
)
def confirm_tour_guide_booking(
    booking_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)

    booking = (
        db.query(TourGuideBooking)
        .filter(
            TourGuideBooking.id == booking_id,
            TourGuideBooking.tour_guide_id == guide.id,
        )
        .first()
    )
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide booking not found or not owned by you",
        )

    if booking.booking_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot confirm booking with status '{booking.booking_status}'. Must be 'pending'",
        )

    booking.booking_status = "confirmed"
    db.commit()
    db.refresh(booking)

    traveler = db.get(User, booking.traveler_id)
    pkg = db.get(TourGuidePackage, booking.package_id) if booking.package_id else None

    return TourGuideBookingProviderItem(
        id=booking.id,
        tour_guide_id=booking.tour_guide_id,
        traveler_id=booking.traveler_id,
        package_id=booking.package_id,
        booking_date=booking.booking_date,
        start_datetime=booking.start_datetime,
        end_datetime=booking.end_datetime,
        number_of_people=booking.number_of_people,
        pickup_location=booking.pickup_location,
        special_requests=booking.special_requests,
        total_price=booking.total_price,
        currency=booking.currency,
        booking_status=booking.booking_status,
        payment_method=booking.payment_method,
        payment_status=booking.payment_status,
        package_title=pkg.title if pkg else None,
        traveler_name=traveler.full_name if traveler else None,
        traveler_email=traveler.email if traveler else None,
    )


@router.post(
    "/me/bookings/{booking_id}/reject",
    response_model=TourGuideBookingProviderItem,
    summary="Guide rejects pending booking",
)
def reject_tour_guide_booking(
    booking_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)

    booking = (
        db.query(TourGuideBooking)
        .filter(
            TourGuideBooking.id == booking_id,
            TourGuideBooking.tour_guide_id == guide.id,
        )
        .first()
    )
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide booking not found or not owned by you",
        )

    if booking.booking_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot reject booking with status '{booking.booking_status}'. Must be 'pending'",
        )

    booking.booking_status = "rejected"
    db.commit()
    db.refresh(booking)

    traveler = db.get(User, booking.traveler_id)
    pkg = db.get(TourGuidePackage, booking.package_id) if booking.package_id else None

    return TourGuideBookingProviderItem(
        id=booking.id,
        tour_guide_id=booking.tour_guide_id,
        traveler_id=booking.traveler_id,
        package_id=booking.package_id,
        booking_date=booking.booking_date,
        start_datetime=booking.start_datetime,
        end_datetime=booking.end_datetime,
        number_of_people=booking.number_of_people,
        pickup_location=booking.pickup_location,
        special_requests=booking.special_requests,
        total_price=booking.total_price,
        currency=booking.currency,
        booking_status=booking.booking_status,
        payment_method=booking.payment_method,
        payment_status=booking.payment_status,
        package_title=pkg.title if pkg else None,
        traveler_name=traveler.full_name if traveler else None,
        traveler_email=traveler.email if traveler else None,
    )


@router.post(
    "/me/bookings/{booking_id}/complete",
    response_model=TourGuideBookingProviderItem,
    summary="Guide marks booking as completed",
)
def complete_tour_guide_booking(
    booking_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    _, guide = get_tour_guide_provider_and_profile(db, current_user)

    booking = (
        db.query(TourGuideBooking)
        .filter(
            TourGuideBooking.id == booking_id,
            TourGuideBooking.tour_guide_id == guide.id,
        )
        .first()
    )
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide booking not found or not owned by you",
        )

    if booking.booking_status not in ("confirmed", "in_progress"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot complete booking with status '{booking.booking_status}'. Must be 'confirmed' or 'in_progress'",
        )

    booking.booking_status = "completed"
    db.commit()
    db.refresh(booking)

    traveler = db.get(User, booking.traveler_id)
    pkg = db.get(TourGuidePackage, booking.package_id) if booking.package_id else None

    return TourGuideBookingProviderItem(
        id=booking.id,
        tour_guide_id=booking.tour_guide_id,
        traveler_id=booking.traveler_id,
        package_id=booking.package_id,
        booking_date=booking.booking_date,
        start_datetime=booking.start_datetime,
        end_datetime=booking.end_datetime,
        number_of_people=booking.number_of_people,
        pickup_location=booking.pickup_location,
        special_requests=booking.special_requests,
        total_price=booking.total_price,
        currency=booking.currency,
        booking_status=booking.booking_status,
        payment_method=booking.payment_method,
        payment_status=booking.payment_status,
        package_title=pkg.title if pkg else None,
        traveler_name=traveler.full_name if traveler else None,
        traveler_email=traveler.email if traveler else None,
    )


# ==========================================
# PUBLIC TOUR GUIDE PROFILE VIEW
# ==========================================

@router.get(
    "/{guide_id}",
    response_model=TourGuideProfileResponse,
    summary="Get public tour guide profile",
)
def get_public_tour_guide_profile(
    guide_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    guide = (
        db.query(TourGuide)
        .options(
            joinedload(TourGuide.languages),
            joinedload(TourGuide.service_areas),
            joinedload(TourGuide.packages),
        )
        .filter(
            TourGuide.id == guide_id,
            TourGuide.approval_status == "approved",
            TourGuide.is_active.is_(True),
        )
        .first()
    )
    if not guide:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide profile not found or not currently active",
        )

    return guide


