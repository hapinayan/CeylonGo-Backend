from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import (
    Integer,
    Unicode,
    and_,
    case,
    cast,
    false,
    func,
    literal,
    null,
    or_,
    select,
    true,
    union_all,
)
from sqlalchemy.orm import Session

from app.core.booking_transactions import CAPTURED
from app.core.publication import (
    public_activity,
    public_artisan,
    public_eco,
    public_homestay,
    public_rental,
)
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.artisan import ArtisanReservation
from app.models.artisan_listing import ArtisanListing
from app.models.booking import Booking
from app.models.eco_experience import EcoExperience
from app.models.eco_promotion import EcoPromotionAnalytics, EcoSponsoredPromotion
from app.models.eco_reservation import EcoAvailabilitySlot, EcoReservation
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import (
    BookingReference,
    BookingStatusHistory,
    ListingReference,
    Notification,
    PaymentTransaction,
    Review,
)
from app.models.showcase_item import ShowcaseItem
from app.models.tour_guide import TourGuide, TourGuideBooking, TourGuidePackage
from app.schemas.provider_workspace import (
    ActivityItem,
    ActivityPage,
    BookingMetrics,
    CashCollection,
    DashboardAnalytics,
    DashboardPeriod,
    ListingMetrics,
    MonthlyMetrics,
    ProviderDashboard,
    ProviderWorkspace,
    PublishedReviews,
    ReservationSummary,
    WorkspaceBusiness,
    WorkspaceNavigation,
    WorkspacePerson,
)
from app.services.provider_dashboard_finance import (
    finance_totals,
    month_key,
    period_conditions,
    settlement_totals,
)

# Only existing management operations appear in the navigation.
MANAGEMENT = {
    "homestay": (
        ("listings:manage", "GET", "/api/v1/homestays/mine"),
        ("bookings:manage", "GET", "/api/v1/bookings/provider"),
        (
            "availability:manage",
            "GET",
            "/api/v1/homestays/{homestay_id}/availability-blocks",
        ),
        ("finance:read", "GET", "/api/v1/payments/provider/earnings"),
        ("settlements:read", "GET", "/api/v1/payments/provider/settlements"),
        ("reviews:read", "GET", "/api/v1/homestays/reviews/mine"),
        ("promotions:manage", "GET", "/api/v1/homestays/campaigns/mine"),
    ),
    "artisan": (
        ("reservations:act", "POST", "/api/v1/provider/bookings/{booking_id}/action"),
        ("listings:manage", "GET", "/api/v1/artisan-listings/me/listings"),
        ("showcase:manage", "GET", "/api/v1/showcase-items/me/items"),
        ("promotions:manage", "GET", "/api/v1/promotions/me/items"),
    ),
    "activity": (
        ("reservations:act", "POST", "/api/v1/provider/bookings/{booking_id}/action"),
        ("listings:manage", "GET", "/api/v1/provider/activities"),
        (
            "availability:manage",
            "GET",
            "/api/v1/provider/activities/{activity_id}/slots",
        ),
    ),
    "eco_tourism": (
        ("reservations:act", "POST", "/api/v1/provider/bookings/{booking_id}/action"),
        (
            "availability:manage",
            "GET",
            "/api/v1/provider/eco-experiences/{experience_id}/slots",
        ),
        ("listings:manage", "GET", "/api/v1/eco-experiences/me/listings"),
        ("promotions:manage", "GET", "/api/v1/eco-promotions/me/campaigns"),
        ("promotions:analytics", "GET", "/api/v1/eco-promotions/me/analytics"),
    ),
    "tour_guide": (
        ("reservations:act", "POST", "/api/v1/provider/bookings/{booking_id}/action"),
        ("listings:manage", "GET", "/api/v1/provider/tour-guides"),
        ("listings:submit", "POST", "/api/v1/provider/tour-guides/submit"),
    ),
    "rental": (
        ("listings:create", "POST", "/api/v1/rental-vehicles"),
        ("pickup_locations:manage", "GET", "/api/v1/provider/rental-locations"),
        ("bookings:transition", "PATCH", "/api/v1/rental-bookings/{booking_id}/status"),
    ),
}
LISTING_MODELS = {
    "homestay": (Homestay, "homestay"),
    "artisan": (ArtisanListing, "artisan"),
    "activity": (Activity, "activity"),
    "eco_tourism": (EcoExperience, "eco_experience"),
    "tour_guide": (TourGuide, "tour_guide"),
    "rental": (RentalVehicle, "rental_vehicle"),
}
COMMON_NAVIGATION = (
    ("bookings:read", "GET", "/api/v1/provider/bookings"),
    ("workspace:read", "GET", "/api/v1/provider/workspace"),
    ("dashboard:read", "GET", "/api/v1/provider/dashboard"),
    ("dashboard:analytics", "GET", "/api/v1/provider/dashboard/analytics"),
    ("dashboard:activity", "GET", "/api/v1/provider/dashboard/recent-activity"),
    ("notifications:read", "GET", "/api/v1/notifications"),
)
SRI_LANKA_OFFSET = timedelta(hours=5, minutes=30)


def booking_reference_module(category):
    return {
        "artisan": "artisan_reservation",
        "rental": "rental_vehicle",
        "eco_tourism": "eco_experience",
    }.get(category, category)


def next_month(value):
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1)


def utc_timestamp(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class WorkspaceReader:
    def __init__(
        self,
        db: Session,
        provider: ProviderProfile,
        *,
        now: datetime | None = None,
    ):
        self.db = db
        self.provider = provider
        self.category = provider.provider_type
        if self.category not in LISTING_MODELS:
            raise HTTPException(
                403, detail="Provider category has no approved workspace"
            )
        self.now = (now or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None)
        self.today = (self.now + SRI_LANKA_OFFSET).date()
        self.model, self.module = LISTING_MODELS[self.category]
        self.owned = self._ownership()

    def _ownership(self):
        p, model = self.provider, self.model
        if model is Homestay:
            return or_(
                and_(
                    model.provider_profile_id == p.id,
                    or_(
                        model.owner_id.is_(None),
                        model.owner_id == p.user_id,
                    ),
                ),
                and_(model.provider_profile_id.is_(None), model.owner_id == p.user_id),
            )
        if model is RentalVehicle:
            return model.provider_id == p.id
        return model.provider_profile_id == p.id

    def listing_metrics(self):
        public = {
            "homestay": public_homestay,
            "artisan": public_artisan,
            "activity": public_activity,
            "eco_tourism": public_eco,
            "rental": public_rental,
        }.get(self.category)
        if self.model is EcoExperience:
            pending = self.model.listing_status == "pending_approval"
        else:
            pending = self.model.approval_status == "pending"
        if public:
            active = public()
        else:
            active = and_(
                self.model.approval_status == "approved",
                self.model.is_active == true(),
            )
        counts = (
            select(func.count(self.model.id)).select_from(self.model).where(self.owned)
        )
        return ListingMetrics(
            total=self.db.scalar(counts),
            active=self.db.scalar(counts.where(active)),
            pending_approval=self.db.scalar(counts.where(pending)),
        )

    def _bookings(self):
        """Use module records exactly once. The shared reference is a ledger link only."""
        if self.category == "homestay":
            return (
                select(
                    Booking.id,
                    Booking.homestay_id.label("listing_id"),
                    Booking.status,
                    Booking.created_at,
                    Booking.user_id,
                    Booking.payment_status,
                    Booking.total_price,
                    Booking.currency,
                    Homestay.name.label("title"),
                    Booking.check_in.label("starts"),
                    Booking.check_out.label("ends"),
                )
                .join(Homestay, Homestay.id == Booking.homestay_id)
                .where(self.owned)
                .subquery()
            )
        if self.category == "activity":
            return (
                select(
                    ActivityBooking.id,
                    Activity.id.label("listing_id"),
                    ActivityBooking.booking_status.label("status"),
                    ActivityBooking.created_at,
                    ActivityBooking.user_id,
                    ActivityBooking.payment_status,
                    ActivityBooking.total_price,
                    ActivityBooking.currency,
                    Activity.title.label("title"),
                    ActivitySlot.starts_at.label("starts"),
                    ActivitySlot.ends_at.label("ends"),
                )
                .join(ActivitySlot, ActivitySlot.id == ActivityBooking.activity_slot_id)
                .join(
                    Activity,
                    Activity.id == ActivitySlot.activity_id,
                )
                .where(self.owned)
                .subquery()
            )
        if self.category == "rental":
            return (
                select(
                    RentalBooking.id,
                    RentalVehicle.id.label("listing_id"),
                    RentalBooking.status,
                    RentalBooking.created_at,
                    RentalBooking.user_id,
                    RentalBooking.payment_status,
                    RentalBooking.total_price,
                    RentalBooking.currency,
                    RentalVehicle.vehicle_name.label("title"),
                    RentalBooking.pickup_at.label("starts"),
                    RentalBooking.return_at.label("ends"),
                )
                .join(RentalVehicle, RentalVehicle.id == RentalBooking.vehicle_id)
                .where(
                    self.owned,
                )
                .subquery()
            )
        if self.category == "tour_guide":
            row = TourGuideBooking
            hold = and_(
                row.booking_status == "pending",
                row.payment_method == "online",
                row.expires_at.is_not(None),
            )
            expired = and_(hold, row.expires_at <= self.now)
            return (
                select(
                    row.id,
                    row.tour_guide_id.label("listing_id"),
                    case(
                        (expired, "expired"),
                        (hold, "awaiting_payment"),
                        else_=row.booking_status,
                    ).label("status"),
                    row.created_at,
                    row.traveler_id.label("user_id"),
                    case((expired, "failed"), else_=row.payment_status).label(
                        "payment_status"
                    ),
                    row.payment_method,
                    row.expires_at.label("payment_expires_at"),
                    row.total_price,
                    row.currency,
                    func.coalesce(TourGuidePackage.title, TourGuide.base_city).label(
                        "title"
                    ),
                    row.start_datetime.label("starts"),
                    row.end_datetime.label("ends"),
                )
                .join(TourGuide, TourGuide.id == row.tour_guide_id)
                .outerjoin(
                    TourGuidePackage,
                    (TourGuidePackage.id == row.package_id)
                    & (TourGuidePackage.tour_guide_id == row.tour_guide_id),
                )
                .where(self.owned)
                .subquery()
            )
        if self.category == "artisan":
            row = ArtisanReservation
            payment_status = (
                select(BookingReference.payment_status)
                .where(
                    BookingReference.module_type == "artisan_reservation",
                    BookingReference.target_booking_id == row.id,
                    BookingReference.user_id == row.traveler_id,
                    BookingReference.provider_profile_id == self.provider.id,
                )
                .order_by(BookingReference.id.desc())
                .limit(1)
                .scalar_subquery()
            )
            return (
                select(
                    row.id,
                    row.artisan_listing_id.label("listing_id"),
                    row.reservation_status.label("status"),
                    row.created_at,
                    row.traveler_id.label("user_id"),
                    payment_status.label("payment_status"),
                    row.total_price,
                    row.currency,
                    ShowcaseItem.title.label("title"),
                    row.pickup_date.label("starts"),
                    row.pickup_date.label("ends"),
                    row.expires_at,
                )
                .join(ArtisanListing, ArtisanListing.id == row.artisan_listing_id)
                .join(
                    ShowcaseItem,
                    (ShowcaseItem.id == row.showcase_item_id)
                    & (ShowcaseItem.artisan_listing_id == row.artisan_listing_id),
                )
                .where(self.owned)
                .subquery()
            )
        if self.category == "eco_tourism":
            row, slot = EcoReservation, EcoAvailabilitySlot
            return (
                select(
                    row.id,
                    slot.eco_experience_id.label("listing_id"),
                    row.status,
                    row.created_at,
                    row.traveler_id.label("user_id"),
                    row.payment_status,
                    row.total_price,
                    row.currency,
                    EcoExperience.title.label("title"),
                    slot.starts_at.label("starts"),
                    slot.ends_at.label("ends"),
                )
                .join(slot, slot.id == row.slot_id)
                .join(EcoExperience, EcoExperience.id == slot.eco_experience_id)
                .where(self.owned)
                .subquery()
            )
        return None

    def _upcoming(self, columns):
        cutoff = self.today if self.category in ("homestay", "artisan") else self.now
        return and_(
            columns.status.in_(("confirmed", "ready_for_pickup")),
            columns.starts > cutoff,
        )

    def active_booking(self, columns):
        if self.category == "artisan":
            return and_(
                columns.status.in_(("confirmed", "ready_for_pickup")),
                columns.starts == self.today,
            )
        cutoff = self.today if self.category == "homestay" else self.now
        return or_(
            columns.status.in_(("active", "in_progress")),
            and_(
                columns.status == "confirmed",
                columns.starts <= cutoff,
                columns.ends > cutoff,
            ),
        )

    def booking_metrics(self, period):
        source = self._bookings()
        if source is None:
            return None
        c = source.c
        active = self.active_booking(c)
        row = (
            self.db.execute(
                select(
                    func.count(c.id).label("total"),
                    func.count(
                        case((c.status.in_(("pending", "awaiting_payment")), 1))
                    ).label("pending"),
                    func.count(case((self._upcoming(c), 1))).label("upcoming"),
                    func.count(case((active, 1))).label("active"),
                    func.count(case((c.status == "completed", 1))).label("completed"),
                    func.count(case((c.status == "cancelled", 1))).label("cancelled"),
                ).where(*period_conditions(c.created_at, period))
            )
            .mappings()
            .one()
        )
        return BookingMetrics(**row)

    def upcoming_reservations(self, period, limit):
        source = self._bookings()
        if source is None:
            return None
        c = source.c
        rows = self.db.execute(
            select(source)
            .where(
                self._upcoming(c),
                *period_conditions(c.created_at, period),
            )
            .order_by(c.starts, c.id)
            .limit(limit)
        )
        return [
            ReservationSummary(
                booking_id=row.id,
                listing_id=row.listing_id,
                status=row.status,
                **(
                    {"starts_on": row.starts, "ends_on": row.ends}
                    if self.category in ("homestay", "artisan")
                    else {
                        "starts_at": utc_timestamp(row.starts),
                        "ends_at": utc_timestamp(row.ends),
                    }
                ),
            )
            for row in rows
        ]

    def review_metrics(self, period):
        owned_refs = (
            select(ListingReference.id)
            .join(
                self.model,
                self.model.id == ListingReference.target_id,
            )
            .where(
                ListingReference.module_type == self.module,
                ListingReference.provider_profile_id == self.provider.id,
                self.owned,
            )
        )
        if self.category != "homestay" and not self.db.scalar(
            select(1).where(
                owned_refs.exists(),
            )
        ):
            return None
        count, mean = self.db.execute(
            select(
                func.count(Review.id),
                func.avg(Review.rating),
            ).where(
                Review.listing_reference_id.in_(owned_refs),
                Review.is_published == true(),
                *period_conditions(Review.created_at, period),
            )
        ).one()
        return PublishedReviews(
            count=count,
            average_rating=Decimal(str(mean)).quantize(Decimal("0.01"))
            if mean is not None
            else None,
        )

    def impressions(self, period, monthly=False):
        if self.category != "eco_tourism":
            return None
        metric, promo = EcoPromotionAnalytics, EcoSponsoredPromotion
        keys = month_key(metric.metric_date, monthly=monthly)
        rows = self.db.execute(
            select(*keys, func.coalesce(func.sum(metric.impressions_count), 0))
            .select_from(metric)
            .join(promo, promo.id == metric.promotion_id)
            .join(EcoExperience, EcoExperience.id == promo.eco_experience_id)
            .where(
                self.owned,
                promo.provider_profile_id == self.provider.id,
                metric.eco_experience_id == EcoExperience.id,
                *period_conditions(metric.metric_date, period, date_column=True),
            )
            .group_by(*keys)
        )
        if monthly:
            return {(year, month): total for year, month, total in rows}
        return rows.scalar_one()

    def unavailable(self, reviews_available):
        missing = {
            "profile_views": "No verified provider profile view counter exists.",
            "listing_views": "No reliable general listing view counter exists.",
        }
        if self.category != "homestay":
            missing["finance"] = "Verified online booking revenue is Homestay-only."
            missing["settlements"] = "Provider settlement integration is Homestay-only."
        if self.category not in ("homestay", "tour_guide", "artisan", "eco_tourism"):
            missing["cash_receipts"] = (
                "No verified cash receipt source exists for this category."
            )
        if self.category != "eco_tourism":
            missing["sponsored_impressions"] = (
                "No signed sponsored impression counter is available."
            )
        if not reviews_available:
            missing["reviews"] = (
                "No owned shared listing references are available for reviews."
            )
        return missing

    def cash_receipts(self, period, monthly=False):
        if self.category not in ("tour_guide", "artisan", "eco_tourism"):
            return None
        source = self._bookings()
        ref, tx = BookingReference, PaymentTransaction
        keys = month_key(tx.paid_at, monthly=monthly)
        rows = self.db.execute(
            select(*keys, tx.currency, func.sum(tx.amount))
            .select_from(tx)
            .join(ref, ref.id == tx.booking_reference_id)
            .join(
                source,
                (source.c.id == ref.target_booking_id)
                & (source.c.user_id == ref.user_id),
            )
            .where(
                ref.module_type == booking_reference_module(self.category),
                ref.provider_profile_id == self.provider.id,
                tx.user_id == ref.user_id,
                tx.currency == ref.currency,
                tx.payment_method == "cash",
                tx.payment_status.in_(CAPTURED),
                tx.paid_at.is_not(None),
                *period_conditions(tx.paid_at, period),
            )
            .group_by(*keys, tx.currency)
            .order_by(tx.currency)
        )
        totals = {}
        for *month, currency, amount in rows:
            totals.setdefault(tuple(month), []).append(
                CashCollection(currency=currency, amount=amount)
            )
        return totals

    def workspace(self):
        p, user = self.provider, self.provider.user
        navigation = (*COMMON_NAVIGATION, *MANAGEMENT[self.category])
        reviews = self.review_metrics(DashboardPeriod())
        return ProviderWorkspace(
            provider_id=p.id,
            category=self.category,
            personal=WorkspacePerson(
                user_id=user.id, name=user.full_name, email=user.email
            ),
            business=WorkspaceBusiness(
                name=p.business_name,
                description=p.description,
                phone=p.phone,
                province=p.business_province,
                district=p.business_district or p.district,
                city=p.business_city,
            ),
            account_active=user.is_active,
            approval_status=p.approval_status,
            verification_status="approved" if p.reviewed_at else "unrecorded",
            reviewed_at=utc_timestamp(p.reviewed_at) if p.reviewed_at else None,
            permissions=[key for key, _, _ in navigation],
            navigation=[
                WorkspaceNavigation(id=k, method=m, path=url)
                for k, m, url in navigation
            ],
            unavailable_metrics=self.unavailable(reviews is not None),
        )

    def recent_activity(self, period, page, page_size):
        source = self._bookings()
        model = self.model
        queries = [
            select(
                literal("listing_created").label("kind"),
                model.created_at.label("occurred_at"),
                model.id.label("listing_id"),
                cast(null(), Integer).label("booking_id"),
                cast(null(), Unicode(30)).label("status"),
                model.id.label("sequence"),
            ).where(self.owned, *period_conditions(model.created_at, period))
        ]
        if source is not None:
            c = source.c
            queries.append(
                select(
                    literal("booking_created"),
                    c.created_at,
                    c.listing_id,
                    c.id,
                    null(),
                    c.id,
                ).where(*period_conditions(c.created_at, period))
            )
            history = BookingStatusHistory
            queries.append(
                select(
                    literal("booking_status_changed"),
                    history.created_at,
                    c.listing_id,
                    c.id,
                    history.to_status,
                    history.id,
                )
                .select_from(history)
                .join(
                    BookingReference,
                    BookingReference.id == history.booking_reference_id,
                )
                .join(source, c.id == BookingReference.target_booking_id)
                .where(
                    BookingReference.module_type
                    == booking_reference_module(self.category),
                    or_(
                        and_(
                            history.from_status.is_(None),
                            history.to_status == "completed",
                        ),
                        history.from_status != history.to_status,
                    ),
                    BookingReference.provider_profile_id == self.provider.id,
                    BookingReference.user_id == c.user_id,
                    *period_conditions(history.created_at, period),
                )
            )
        events = (union_all(*queries) if len(queries) > 1 else queries[0]).subquery()
        e = events.c
        rows = (
            self.db.execute(
                select(events)
                .order_by(
                    e.occurred_at.desc(),
                    e.kind,
                    e.sequence.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size + 1)
            )
            .mappings()
            .all()
        )
        return ActivityPage(
            period=period,
            page=page,
            page_size=page_size,
            has_more=len(rows) > page_size,
            items=[
                ActivityItem(
                    **{**row, "occurred_at": utc_timestamp(row["occurred_at"])}
                )
                for row in rows[:page_size]
            ],
        )

    def dashboard(self, period, activity_limit, reservation_limit):
        reviews = self.review_metrics(period)
        homestay = self.category == "homestay"
        cash = self.cash_receipts(period)
        return ProviderDashboard(
            provider_id=self.provider.id,
            category=self.category,
            period=period,
            listings=self.listing_metrics(),
            bookings=self.booking_metrics(period),
            finance=finance_totals(self.db, self.provider.id, self.owned, period).get(
                (), []
            )
            if homestay
            else None,
            settlements=settlement_totals(self.db, self.provider.id)
            if homestay
            else None,
            reviews=reviews,
            unread_notifications=self.db.scalar(
                select(func.count(Notification.id)).where(
                    Notification.user_id == self.provider.user_id,
                    Notification.is_read == false(),
                )
            ),
            recent_activity=self.recent_activity(period, 1, activity_limit).items,
            upcoming_reservations=self.upcoming_reservations(period, reservation_limit),
            sponsored_impressions=self.impressions(period),
            cash_receipts=cash.get((), []) if cash is not None else None,
            unavailable_metrics=self.unavailable(reviews is not None),
        )

    def analytics(self, period):
        source = self._bookings()
        counts = {}
        if source is not None:
            created = source.c.created_at
            keys = month_key(created, monthly=True)
            counts = {
                (year, month): count
                for year, month, count in self.db.execute(
                    select(*keys, func.count(source.c.id))
                    .where(*period_conditions(created, period))
                    .group_by(*keys)
                )
            }
        homestay = self.category == "homestay"
        finance = (
            finance_totals(self.db, self.provider.id, self.owned, period, monthly=True)
            if homestay
            else {}
        )
        impressions = self.impressions(period, monthly=True)
        cash = self.cash_receipts(period, monthly=True)
        reviews = self.review_metrics(period)
        months = []
        month = period.start_date.replace(day=1)
        while month <= period.end_date:
            key = (month.year, month.month)
            months.append(
                MonthlyMetrics(
                    month=month,
                    bookings=counts.get(key, 0) if source is not None else None,
                    revenue=finance.get(key, []) if homestay else None,
                    sponsored_impressions=impressions.get(key, 0)
                    if impressions is not None
                    else None,
                    cash_receipts=cash.get(key, []) if cash is not None else None,
                )
            )
            month = next_month(month)
        return DashboardAnalytics(
            period=period,
            months=months,
            unavailable_metrics=self.unavailable(reviews is not None),
        )
