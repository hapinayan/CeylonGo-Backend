import math
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.dependencies import require_approved_provider, require_provider
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.artisan import ArtisanReservation
from app.models.artisan_listing import ArtisanListing
from app.models.booking import Booking
from app.models.eco_experience import EcoExperience
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.rental import RentalVehicleDocument
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import Notification, ProviderSettlement
from app.models.tour_guide import TourGuide, TourGuideBooking, TourGuideCredential, TourGuidePackage
from app.models.user import User
from app.schemas.provider_dashboard import (
    BookingMetricsSummary,
    BookingReportSummary,
    ExpiringDocumentItem,
    ExpiringDocumentsResponse,
    ListingMetricsSummary,
    NotificationItem,
    NotificationListResponse,
    ProviderEarningsSummaryResponse,
    ProviderProfileSummary,
    ProviderSettlementItem,
    ProviderSettlementListResponse,
    RecentBookingSummary,
    SharedProviderDashboardResponse,
    UnreadCountResponse,
)

router = APIRouter(
    prefix="/api/v1/providers/me",
    tags=["Shared Provider Management"],
)


# ==========================================
# SHARED PROVIDER DASHBOARD
# ==========================================

@router.get(
    "/dashboard",
    response_model=SharedProviderDashboardResponse,
    summary="Unified provider dashboard summary",
)
def get_shared_provider_dashboard(
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Returns aggregated metrics for the authenticated approved provider across their respective domain:
    homestay, artisan, rental, activity, tour_guide, or eco_tourism.
    """
    p_summary = ProviderProfileSummary(
        id=provider_profile.id,
        business_name=provider_profile.business_name,
        provider_type=provider_profile.provider_type,
        approval_status=provider_profile.approval_status,
        district=provider_profile.district,
    )

    ptype = provider_profile.provider_type
    listings_m = ListingMetricsSummary()
    bookings_m = BookingMetricsSummary()
    recent_bookings: list[RecentBookingSummary] = []

    if ptype == "homestay":
        h_listings = db.query(Homestay).filter(
            or_(
                Homestay.provider_profile_id == provider_profile.id,
                Homestay.owner_id == provider_profile.user_id,
            )
        )
        listings_m.total = h_listings.count()
        listings_m.active = h_listings.filter(
            Homestay.is_active.is_(True), Homestay.approval_status == "approved"
        ).count()
        listings_m.pending = h_listings.filter(Homestay.approval_status == "pending").count()

        h_bookings = (
            db.query(Booking)
            .join(Homestay, Booking.homestay_id == Homestay.id)
            .filter(
                or_(
                    Homestay.provider_profile_id == provider_profile.id,
                    Homestay.owner_id == provider_profile.user_id,
                )
            )
        )
        bookings_m.total = h_bookings.count()
        bookings_m.pending = h_bookings.filter(Booking.status == "pending").count()
        bookings_m.confirmed = h_bookings.filter(
            Booking.status.in_(["confirmed", "in_progress"])
        ).count()
        bookings_m.completed = h_bookings.filter(Booking.status == "completed").count()
        bookings_m.cancelled = h_bookings.filter(Booking.status == "cancelled").count()

        recent_rows = h_bookings.order_by(Booking.created_at.desc()).limit(5).all()
        for r in recent_rows:
            u = db.get(User, r.user_id)
            recent_bookings.append(
                RecentBookingSummary(
                    id=r.id,
                    booking_date=r.check_in,
                    customer_name=u.full_name if u else None,
                    status=r.status,
                    total_price=r.total_price,
                    currency="LKR",
                )
            )

    elif ptype == "artisan":
        a_listings = db.query(ArtisanListing).filter(
            ArtisanListing.provider_profile_id == provider_profile.id
        )
        listings_m.total = a_listings.count()
        listings_m.active = a_listings.filter(
            ArtisanListing.is_active.is_(True), ArtisanListing.approval_status == "approved"
        ).count()
        listings_m.pending = a_listings.filter(ArtisanListing.approval_status == "pending").count()

        a_res = (
            db.query(ArtisanReservation)
            .join(ArtisanListing, ArtisanReservation.artisan_listing_id == ArtisanListing.id)
            .filter(ArtisanListing.provider_profile_id == provider_profile.id)
        )
        bookings_m.total = a_res.count()
        bookings_m.pending = a_res.filter(ArtisanReservation.reservation_status == "pending").count()
        bookings_m.confirmed = a_res.filter(ArtisanReservation.reservation_status == "confirmed").count()
        bookings_m.completed = a_res.filter(ArtisanReservation.reservation_status == "completed").count()
        bookings_m.cancelled = a_res.filter(ArtisanReservation.reservation_status == "cancelled").count()

        recent_rows = a_res.order_by(ArtisanReservation.created_at.desc()).limit(5).all()
        for r in recent_rows:
            u = db.get(User, r.traveler_id)
            recent_bookings.append(
                RecentBookingSummary(
                    id=r.id,
                    booking_date=r.pickup_date,
                    customer_name=u.full_name if u else None,
                    status=r.reservation_status,
                    total_price=r.total_price,
                    currency="LKR",
                )
            )

    elif ptype == "rental":
        r_vehicles = db.query(RentalVehicle).filter(RentalVehicle.provider_id == provider_profile.id)
        listings_m.total = r_vehicles.count()
        listings_m.active = r_vehicles.filter(RentalVehicle.status == "available").count()
        listings_m.pending = 0

        r_bookings = (
            db.query(RentalBooking)
            .join(RentalVehicle, RentalBooking.vehicle_id == RentalVehicle.id)
            .filter(RentalVehicle.provider_id == provider_profile.id)
        )
        bookings_m.total = r_bookings.count()
        bookings_m.pending = r_bookings.filter(RentalBooking.status == "pending").count()
        bookings_m.confirmed = r_bookings.filter(
            RentalBooking.status.in_(["confirmed", "in_progress"])
        ).count()
        bookings_m.completed = r_bookings.filter(RentalBooking.status == "completed").count()
        bookings_m.cancelled = r_bookings.filter(RentalBooking.status == "cancelled").count()

        recent_rows = r_bookings.order_by(RentalBooking.created_at.desc()).limit(5).all()
        for r in recent_rows:
            u = db.get(User, r.user_id)
            recent_bookings.append(
                RecentBookingSummary(
                    id=r.id,
                    booking_date=r.start_date,
                    customer_name=u.full_name if u else None,
                    status=r.status,
                    total_price=r.total_price,
                    currency=r.currency,
                )
            )

    elif ptype == "activity":
        act_query = db.query(Activity).filter(Activity.provider_id == provider_profile.id)
        listings_m.total = act_query.count()
        listings_m.active = act_query.filter(
            Activity.status == "approved", Activity.is_active.is_(True)
        ).count()
        listings_m.pending = act_query.filter(Activity.status == "pending").count()

        act_bookings = (
            db.query(ActivityBooking)
            .join(ActivitySlot, ActivityBooking.activity_slot_id == ActivitySlot.id)
            .join(Activity, ActivitySlot.activity_id == Activity.id)
            .filter(Activity.provider_id == provider_profile.id)
        )
        bookings_m.total = act_bookings.count()
        bookings_m.pending = act_bookings.filter(ActivityBooking.booking_status == "pending").count()
        bookings_m.confirmed = act_bookings.filter(ActivityBooking.booking_status == "confirmed").count()
        bookings_m.completed = act_bookings.filter(ActivityBooking.booking_status == "completed").count()
        bookings_m.cancelled = act_bookings.filter(ActivityBooking.booking_status == "cancelled").count()

        recent_rows = act_bookings.order_by(ActivityBooking.created_at.desc()).limit(5).all()
        for r in recent_rows:
            u = db.get(User, r.user_id)
            recent_bookings.append(
                RecentBookingSummary(
                    id=r.id,
                    booking_date=r.created_at.date() if r.created_at else None,
                    customer_name=u.full_name if u else None,
                    status=r.booking_status,
                    total_price=r.total_price,
                    currency="LKR",
                )
            )

    elif ptype == "tour_guide":
        guide = db.query(TourGuide).filter(TourGuide.provider_profile_id == provider_profile.id).first()
        if guide:
            pkg_query = db.query(TourGuidePackage).filter(TourGuidePackage.tour_guide_id == guide.id)
            listings_m.total = pkg_query.count()
            listings_m.active = pkg_query.filter(TourGuidePackage.is_active.is_(True)).count()
            listings_m.pending = 0

            tg_bookings = db.query(TourGuideBooking).filter(TourGuideBooking.tour_guide_id == guide.id)
            bookings_m.total = tg_bookings.count()
            bookings_m.pending = tg_bookings.filter(TourGuideBooking.booking_status == "pending").count()
            bookings_m.confirmed = tg_bookings.filter(
                TourGuideBooking.booking_status.in_(["confirmed", "in_progress"])
            ).count()
            bookings_m.completed = tg_bookings.filter(TourGuideBooking.booking_status == "completed").count()
            bookings_m.cancelled = tg_bookings.filter(TourGuideBooking.booking_status == "cancelled").count()

            recent_rows = tg_bookings.order_by(TourGuideBooking.start_datetime.desc()).limit(5).all()
            for r in recent_rows:
                u = db.get(User, r.traveler_id)
                recent_bookings.append(
                    RecentBookingSummary(
                        id=r.id,
                        booking_date=r.booking_date,
                        customer_name=u.full_name if u else None,
                        status=r.booking_status,
                        total_price=r.total_price,
                        currency=r.currency,
                    )
                )

    elif ptype == "eco_tourism":
        eco_query = db.query(EcoExperience).filter(EcoExperience.provider_id == provider_profile.id)
        listings_m.total = eco_query.count()
        listings_m.active = eco_query.filter(
            EcoExperience.status == "approved", EcoExperience.is_active.is_(True)
        ).count()
        listings_m.pending = eco_query.filter(EcoExperience.status == "pending").count()

    return SharedProviderDashboardResponse(
        provider=p_summary,
        listings=listings_m,
        bookings=bookings_m,
        recent_bookings=recent_bookings,
    )


# ==========================================
# PROVIDER NOTIFICATIONS MANAGEMENT
# ==========================================

@router.get(
    "/notifications",
    response_model=NotificationListResponse,
    summary="List provider notifications",
)
def list_provider_notifications(
    current_user: Annotated[User, Depends(require_provider)],
    db: Annotated[Session, Depends(get_db)],
    unread_only: bool = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    query = db.query(Notification).filter(Notification.user_id == current_user.id)
    unread_count = (
        db.query(func.count(Notification.id))
        .filter(Notification.user_id == current_user.id, Notification.is_read.is_(False))
        .scalar()
        or 0
    )

    if unread_only:
        query = query.filter(Notification.is_read.is_(False))

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size))

    items = (
        query.order_by(Notification.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return NotificationListResponse(
        items=items,
        unread_count=unread_count,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/notifications/unread-count",
    response_model=UnreadCountResponse,
    summary="Get unread notification count",
)
def get_unread_notification_count(
    current_user: Annotated[User, Depends(require_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    unread_count = (
        db.query(func.count(Notification.id))
        .filter(Notification.user_id == current_user.id, Notification.is_read.is_(False))
        .scalar()
        or 0
    )
    return UnreadCountResponse(unread_count=unread_count)


@router.patch(
    "/notifications/{notification_id}/read",
    response_model=NotificationItem,
    summary="Mark single notification as read",
)
def mark_notification_as_read(
    notification_id: int,
    current_user: Annotated[User, Depends(require_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    notification = (
        db.query(Notification)
        .filter(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,
        )
        .first()
    )
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found or not owned by you",
        )

    notification.is_read = True
    db.commit()
    db.refresh(notification)
    return notification


@router.post(
    "/notifications/read-all",
    summary="Mark all notifications as read",
)
def mark_all_notifications_as_read(
    current_user: Annotated[User, Depends(require_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    updated = (
        db.query(Notification)
        .filter(
            Notification.user_id == current_user.id,
            Notification.is_read.is_(False),
        )
        .update({Notification.is_read: True}, synchronize_session=False)
    )
    db.commit()
    return {"message": "All notifications marked as read", "updated_count": updated}


# ==========================================
# PROVIDER BOOKING REPORTS & ANALYTICS
# ==========================================

@router.get(
    "/reports/bookings",
    response_model=BookingReportSummary,
    summary="Date-filtered booking reports and revenue analytics",
)
def get_provider_booking_reports(
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
    start_date: Annotated[date | None, Query(description="Filter start date")] = None,
    end_date: Annotated[date | None, Query(description="Filter end date")] = None,
):
    if start_date and end_date and end_date < start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End date cannot precede start date",
        )

    ptype = provider_profile.provider_type
    total_b = 0
    pending_b = 0
    confirmed_b = 0
    completed_b = 0
    cancelled_b = 0
    total_rev = Decimal("0.00")
    currency = "LKR"

    if ptype == "homestay":
        q = (
            db.query(Booking)
            .join(Homestay, Booking.homestay_id == Homestay.id)
            .filter(
                or_(
                    Homestay.provider_profile_id == provider_profile.id,
                    Homestay.owner_id == provider_profile.user_id,
                )
            )
        )
        if start_date:
            q = q.filter(Booking.check_in >= start_date)
        if end_date:
            q = q.filter(Booking.check_in <= end_date)

        rows = q.all()
        total_b = len(rows)
        for r in rows:
            if r.status == "pending":
                pending_b += 1
            elif r.status in ("confirmed", "in_progress"):
                confirmed_b += 1
                total_rev += r.total_price or Decimal("0.00")
            elif r.status == "completed":
                completed_b += 1
                total_rev += r.total_price or Decimal("0.00")
            elif r.status == "cancelled":
                cancelled_b += 1

    elif ptype == "rental":
        q = (
            db.query(RentalBooking)
            .join(RentalVehicle, RentalBooking.vehicle_id == RentalVehicle.id)
            .filter(RentalVehicle.provider_id == provider_profile.id)
        )
        if start_date:
            q = q.filter(RentalBooking.start_date >= start_date)
        if end_date:
            q = q.filter(RentalBooking.start_date <= end_date)

        rows = q.all()
        total_b = len(rows)
        for r in rows:
            currency = r.currency
            if r.status == "pending":
                pending_b += 1
            elif r.status in ("confirmed", "in_progress"):
                confirmed_b += 1
                total_rev += r.total_price or Decimal("0.00")
            elif r.status == "completed":
                completed_b += 1
                total_rev += r.total_price or Decimal("0.00")
            elif r.status == "cancelled":
                cancelled_b += 1

    elif ptype == "artisan":
        q = (
            db.query(ArtisanReservation)
            .join(ArtisanListing, ArtisanReservation.artisan_listing_id == ArtisanListing.id)
            .filter(ArtisanListing.provider_profile_id == provider_profile.id)
        )
        if start_date:
            q = q.filter(ArtisanReservation.pickup_date >= start_date)
        if end_date:
            q = q.filter(ArtisanReservation.pickup_date <= end_date)

        rows = q.all()
        total_b = len(rows)
        for r in rows:
            if r.reservation_status == "pending":
                pending_b += 1
            elif r.reservation_status == "confirmed":
                confirmed_b += 1
                total_rev += r.total_price or Decimal("0.00")
            elif r.reservation_status == "completed":
                completed_b += 1
                total_rev += r.total_price or Decimal("0.00")
            elif r.reservation_status == "cancelled":
                cancelled_b += 1

    elif ptype == "activity":
        q = (
            db.query(ActivityBooking)
            .join(ActivitySlot, ActivityBooking.activity_slot_id == ActivitySlot.id)
            .join(Activity, ActivitySlot.activity_id == Activity.id)
            .filter(Activity.provider_id == provider_profile.id)
        )
        rows = q.all()
        total_b = len(rows)
        for r in rows:
            if r.booking_status == "pending":
                pending_b += 1
            elif r.booking_status == "confirmed":
                confirmed_b += 1
                total_rev += r.total_price or Decimal("0.00")
            elif r.booking_status == "completed":
                completed_b += 1
                total_rev += r.total_price or Decimal("0.00")
            elif r.booking_status == "cancelled":
                cancelled_b += 1

    elif ptype == "tour_guide":
        guide = db.query(TourGuide).filter(TourGuide.provider_profile_id == provider_profile.id).first()
        if guide:
            q = db.query(TourGuideBooking).filter(TourGuideBooking.tour_guide_id == guide.id)
            if start_date:
                q = q.filter(TourGuideBooking.booking_date >= start_date)
            if end_date:
                q = q.filter(TourGuideBooking.booking_date <= end_date)

            rows = q.all()
            total_b = len(rows)
            for r in rows:
                currency = r.currency
                if r.booking_status == "pending":
                    pending_b += 1
                elif r.booking_status in ("confirmed", "in_progress"):
                    confirmed_b += 1
                    total_rev += r.total_price or Decimal("0.00")
                elif r.booking_status == "completed":
                    completed_b += 1
                    total_rev += r.total_price or Decimal("0.00")
                elif r.booking_status == "cancelled":
                    cancelled_b += 1

    return BookingReportSummary(
        total_bookings=total_b,
        pending=pending_b,
        confirmed=confirmed_b,
        completed=completed_b,
        cancelled=cancelled_b,
        total_revenue=total_rev,
        currency=currency,
        start_date=start_date,
        end_date=end_date,
    )


# ==========================================
# PROVIDER SETTLEMENTS & EARNINGS
# ==========================================

@router.get(
    "/settlements",
    response_model=ProviderSettlementListResponse,
    summary="List provider settlements",
)
def list_provider_settlements(
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
    status: Annotated[str | None, Query(description="Filter by settlement status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    query = db.query(ProviderSettlement).filter(
        ProviderSettlement.provider_profile_id == provider_profile.id
    )
    if status and status.strip():
        query = query.filter(ProviderSettlement.status == status.strip().lower())

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size))

    items = (
        query.order_by(ProviderSettlement.period_end.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return ProviderSettlementListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/earnings/summary",
    response_model=ProviderEarningsSummaryResponse,
    summary="Provider earnings and settlement summary",
)
def get_provider_earnings_summary(
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    settlements = (
        db.query(ProviderSettlement)
        .filter(ProviderSettlement.provider_profile_id == provider_profile.id)
        .all()
    )

    gross = Decimal("0.00")
    comm = Decimal("0.00")
    net = Decimal("0.00")
    currency = "LKR"
    pending_c = 0
    completed_c = 0

    for s in settlements:
        gross += s.gross_earnings or Decimal("0.00")
        comm += s.platform_commission or Decimal("0.00")
        net += s.net_payable or Decimal("0.00")
        currency = s.currency
        if s.status == "pending":
            pending_c += 1
        elif s.status in ("paid", "completed", "settled"):
            completed_c += 1

    return ProviderEarningsSummaryResponse(
        gross_earnings=gross,
        platform_commission=comm,
        net_payable=net,
        currency=currency,
        total_settlements=len(settlements),
        pending_settlements=pending_c,
        completed_settlements=completed_c,
    )


# ==========================================
# DOCUMENT EXPIRY TRACKING & ALERTS (SH-07)
# ==========================================

@router.get(
    "/documents/expiring",
    response_model=ExpiringDocumentsResponse,
    summary="Track expiring provider documents and certificates",
)
def get_expiring_documents(
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
    days_ahead: Annotated[int, Query(ge=1, le=365, description="Lookahead window in days")] = 30,
):
    today = date.today()
    cutoff_date = today + timedelta(days=days_ahead)

    items: list[ExpiringDocumentItem] = []
    expired_c = 0
    urgent_c = 0
    soon_c = 0

    ptype = provider_profile.provider_type

    # 1. Rental Vehicle Documents
    if ptype == "rental":
        vehicles = (
            db.query(RentalVehicle.id)
            .filter(RentalVehicle.provider_id == provider_profile.id)
            .all()
        )
        vehicle_ids = [v[0] for v in vehicles]
        if vehicle_ids:
            docs = (
                db.query(RentalVehicleDocument)
                .filter(
                    RentalVehicleDocument.vehicle_id.in_(vehicle_ids),
                    RentalVehicleDocument.expiry_date.isnot(None),
                    RentalVehicleDocument.expiry_date <= cutoff_date,
                )
                .order_by(RentalVehicleDocument.expiry_date.asc())
                .all()
            )
            for d in docs:
                days_left = (d.expiry_date - today).days
                if days_left < 0:
                    status_str = "expired"
                    expired_c += 1
                elif days_left <= 7:
                    status_str = "urgent"
                    urgent_c += 1
                elif days_left <= days_ahead:
                    status_str = "expiring_soon"
                    soon_c += 1
                else:
                    status_str = "valid"

                items.append(
                    ExpiringDocumentItem(
                        document_id=d.id,
                        document_type=d.document_type,
                        title=f"{d.document_type.replace('_', ' ').title()} (Vehicle #{d.vehicle_id})",
                        entity_type="rental_vehicle",
                        entity_id=d.vehicle_id,
                        expiry_date=d.expiry_date,
                        days_remaining=days_left,
                        status=status_str,
                    )
                )

    # 2. Tour Guide Credentials
    elif ptype == "tour_guide":
        guide = (
            db.query(TourGuide)
            .filter(TourGuide.provider_profile_id == provider_profile.id)
            .first()
        )
        if guide:
            creds = (
                db.query(TourGuideCredential)
                .filter(
                    TourGuideCredential.tour_guide_id == guide.id,
                    TourGuideCredential.expiry_date.isnot(None),
                    TourGuideCredential.expiry_date <= cutoff_date,
                )
                .order_by(TourGuideCredential.expiry_date.asc())
                .all()
            )
            for c in creds:
                days_left = (c.expiry_date - today).days
                if days_left < 0:
                    status_str = "expired"
                    expired_c += 1
                elif days_left <= 7:
                    status_str = "urgent"
                    urgent_c += 1
                elif days_left <= days_ahead:
                    status_str = "expiring_soon"
                    soon_c += 1
                else:
                    status_str = "valid"

                items.append(
                    ExpiringDocumentItem(
                        document_id=c.id,
                        document_type=c.credential_type,
                        title=c.title,
                        entity_type="tour_guide",
                        entity_id=guide.id,
                        expiry_date=c.expiry_date,
                        days_remaining=days_left,
                        status=status_str,
                    )
                )

    return ExpiringDocumentsResponse(
        total_expiring=len(items),
        expired_count=expired_c,
        urgent_count=urgent_c,
        expiring_soon_count=soon_c,
        documents=items,
    )



