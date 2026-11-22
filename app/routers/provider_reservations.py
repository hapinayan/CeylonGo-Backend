from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import or_, select, true
from sqlalchemy.orm import Session

from app.core.dependencies import require_traveler
from app.core.publication import approved_provider
from app.database.connection import get_db
from app.models.shared import BookingReference, BookingStatusHistory
from app.models.tour_guide import TourGuide, TourGuideAvailability, TourGuidePackage
from app.models.user import User
from app.routers.artisan_reservations import create_artisan_reservation
from app.routers.provider_workspace import Period, Reader
from app.schemas.artisan_reservation import ArtisanReservationCreate, ArtisanReservationResponse
from app.schemas.eco_reservation import EcoReservationResponse
from app.schemas.payment import GuidePaymentStatusResponse, PaymentInitiateResponse
from app.services import eco_reservations
from app.services.guide_payments import guide_checkout, guide_payment_status
from app.services.provider_dashboard_finance import period_conditions
from app.services.provider_reservations import (
    ARTISAN,
    GUIDE,
    LOCAL,
    MODELS,
    change_activity_booking,
    change_reservation,
    effective_artisan_expiry,
    reservation_payload,
    reservations,
    reserve_artisan,
    reserve_guide,
)
from app.services.provider_workspace import booking_reference_module, utc_timestamp

router = APIRouter(prefix="/api/v1", tags=["Reservations"])
DB = Annotated[Session, Depends(get_db)]
Traveler = Annotated[User, Depends(require_traveler)]
BookingId = Annotated[int, Path(gt=0, le=2147483647)]
Page = Annotated[int, Query(ge=1, le=1000)]
PageSize = Annotated[int, Query(ge=1, le=50)]
Module = Literal["tour_guide", "artisan_reservation"]


class GuideReservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guide_id: int = Field(gt=0, le=2147483647)
    package_id: int = Field(gt=0, le=2147483647)
    people: int = Field(gt=0, le=1000)
    starts_at: AwareDatetime
    payment_method: Literal["pay_at_location", "online"] = "pay_at_location"


class ArtisanReservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: int = Field(gt=0, le=2147483647)
    quantity: int = Field(gt=0, le=1000)
    pickup_date: date


class CancelReservation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str | None = Field(default=None, max_length=500)


class ReservationAction(CancelReservation):
    action: Literal["accept", "reject", "cancel", "ready", "cash", "complete"]


class ReservationResponse(BaseModel):
    id: int
    module: Module
    reference: str
    status: str
    payment_status: str
    total: Decimal
    currency: str
    starts_at: datetime | None
    ends_at: datetime | None
    pickup_date: date | None
    meeting_point: str | None
    payment_method: Literal["pay_at_location", "online"] | None = None
    payment_expires_at: datetime | None = None


class GuidePackage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    duration_hours: Decimal
    pricing_type: str
    price: Decimal
    currency: str
    max_people: int
    meeting_point: str


class GuideWindow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    available_date: date
    start_time: time | None
    end_time: time | None
    is_available: bool


class BookingHistory(BaseModel):
    previous_status: str | None
    status: str
    occurred_at: datetime


class ProviderBooking(BaseModel):
    id: int
    listing_id: int
    title: str
    status: str
    payment_status: str | None
    total_price: Decimal
    currency: str
    starts: datetime | date | None
    ends: datetime | date | None
    created_at: datetime
    actions: list[str]
    payment_method: str | None = None
    payment_expires_at: datetime | None = None


class BookingDetail(ProviderBooking):
    history: list[BookingHistory]
    has_more_history: bool


class BookingPage(BaseModel):
    items: list[ProviderBooking]
    page: int
    page_size: int
    has_more: bool


def project_booking(reader, row):
    category = reader.category
    status = row.status
    actions = []
    if category == "artisan":
        expiry = effective_artisan_expiry(row.starts, row.expires_at)
        active_hold = expiry is not None and expiry > reader.now
        unpaid = row.payment_status == "unpaid"
        pickup_states = ("confirmed", "ready_for_pickup")
        if status == "pending":
            if active_hold and row.starts is not None and row.starts >= reader.today:
                actions.append("accept")
            if unpaid:
                actions.append("reject")
        if active_hold and unpaid and status in ("pending", *pickup_states):
            actions.append("cancel")
        if active_hold and status == "confirmed":
            actions.append("ready")
        if active_hold and unpaid and status in pickup_states and row.total_price > 0:
            actions.append("cash")
        if (
            row.starts == reader.today
            and status in pickup_states
            and (row.payment_status == "paid" or (active_hold and unpaid and row.total_price == 0))
        ):
            actions.append("complete")
    elif category in ("homestay", "tour_guide"):
        if status == "pending":
            cutoff = reader.today if category == "homestay" else reader.now
            if row.starts is not None and row.starts >= cutoff:
                actions.append("accept")
            actions.append("reject")
        if (
            status
            in (
                "pending",
                "confirmed",
                "ready_for_pickup",
                "awaiting_payment",
            )
            and (
                category == "homestay"
                or row.payment_status == "unpaid"
                or (category == "tour_guide" and status == "awaiting_payment")
            )
            and (category != "tour_guide" or row.starts > reader.now)
        ):
            actions.append("cancel")
        if (
            category == "tour_guide"
            and status in ("confirmed", "ready_for_pickup")
            and row.payment_status == "unpaid"
            and row.total_price > 0
            and (category != "tour_guide" or row.payment_method == "pay_at_location")
        ):
            actions.append("cash")
        cutoff = reader.today if category == "homestay" else reader.now
        if (
            status in ("confirmed", "ready_for_pickup", "in_progress")
            and row.ends is not None
            and row.ends <= cutoff
            and (
                category == "homestay"
                or row.payment_status == "paid"
                or row.total_price == 0
            )
        ):
            actions.append("complete")
    elif category == "rental":
        if status == "pending":
            actions.append("confirm")
        elif status in ("confirmed", "active") and row.ends <= reader.now:
            actions.append("complete")
    elif category == "activity":
        if status == "confirmed" and row.ends <= reader.now:
            actions.append("complete")
        if (
            status in ("pending", "confirmed")
            and row.starts > reader.now
            and row.payment_status == "unpaid"
        ):
            actions.append("cancel")
    elif category == "eco_tourism":
        if status == "pending":
            actions.append("reject")
            if row.starts > reader.now:
                actions.append("accept")
        if (
            status in ("pending", "confirmed")
            and row.starts > reader.now
            and row.payment_status == "unpaid"
        ):
            actions.append("cancel")
        if status == "confirmed":
            if row.payment_status == "unpaid" and row.total_price > 0:
                actions.append("cash")
            if row.ends <= reader.now and (
                row.payment_status == "paid" or row.total_price == 0
            ):
                actions.append("complete")
    data = dict(row._mapping)
    data.pop("expires_at", None)
    for key in ("starts", "ends", "created_at", "payment_expires_at"):
        if isinstance(data.get(key), datetime):
            data[key] = utc_timestamp(data[key])
    return ProviderBooking(**data, actions=actions)


@router.post(
    "/tour-guide-reservations", response_model=ReservationResponse, status_code=201
)
def request_guide_reservation(
    payload: GuideReservationRequest, buyer: Traveler, db: DB
):
    return reserve_guide(db, buyer, payload)


@router.post(
    "/tour-guide-reservations/{booking_id}/payments/initiate",
    response_model=PaymentInitiateResponse,
)
def initiate_guide_payment(booking_id: BookingId, buyer: Traveler, db: DB):
    return guide_checkout(db, booking_id, buyer)


@router.get(
    "/tour-guide-reservations/{booking_id}/payments/status",
    response_model=GuidePaymentStatusResponse,
)
def get_guide_payment_status(booking_id: BookingId, buyer: Traveler, db: DB):
    return guide_payment_status(db, booking_id, buyer)


def ensure_public_guide(db, guide_id):
    if (
        db.scalar(
            select(TourGuide.id).where(
                TourGuide.id == guide_id,
                TourGuide.is_active == true(),
                TourGuide.is_available == true(),
                TourGuide.approval_status == "approved",
                approved_provider(TourGuide.provider_profile_id),
            )
        )
        is None
    ):
        raise HTTPException(404, "Public guide not found")


@router.get(
    "/tour-guide-reservations/guides/{guide_id}/packages",
    response_model=list[GuidePackage],
)
def guide_packages(
    guide_id: Annotated[int, Path(gt=0, le=2147483647)],
    db: DB,
    page: Page = 1,
    page_size: PageSize = 20,
):
    ensure_public_guide(db, guide_id)
    return db.scalars(
        select(TourGuidePackage)
        .where(
            TourGuidePackage.tour_guide_id == guide_id,
            TourGuidePackage.is_active == true(),
        )
        .order_by(TourGuidePackage.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()


@router.get(
    "/tour-guide-reservations/guides/{guide_id}/availability",
    response_model=list[GuideWindow],
)
def guide_availability(
    guide_id: Annotated[int, Path(gt=0, le=2147483647)],
    db: DB,
    day: date,
    page: Page = 1,
    page_size: PageSize = 20,
):
    today = datetime.now(LOCAL).date()
    if not today <= day <= today + timedelta(days=365):
        raise HTTPException(422, "Use a date within the next 365 days")
    ensure_public_guide(db, guide_id)
    model = TourGuideAvailability
    return db.scalars(
        select(model)
        .where(
            model.tour_guide_id == guide_id,
            model.available_date == day,
        )
        .order_by(model.start_time, model.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()


@router.post(
    "/artisan-reservations",
    response_model=ReservationResponse | ArtisanReservationResponse,
    status_code=201,
)
def request_artisan_reservation(
    payload: ArtisanReservationRequest | ArtisanReservationCreate, buyer: Traveler, db: DB
):
    if isinstance(payload, ArtisanReservationCreate):
        return create_artisan_reservation(payload, buyer, db)
    return reserve_artisan(db, buyer, payload)


@router.get("/reservations/{module}/me", response_model=list[ReservationResponse])
def my_reservations(
    module: Module, buyer: Traveler, db: DB, page: Page = 1, page_size: PageSize = 20
):
    query = reservations(db, module, user_id=buyer.id)
    return [
        reservation_payload(row, ref)
        for row, ref in db.execute(
            query.order_by(MODELS[module].id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ]


@router.post(
    "/reservations/{module}/{booking_id}/cancel", response_model=ReservationResponse
)
def cancel_reservation(
    module: Module,
    booking_id: BookingId,
    payload: CancelReservation,
    buyer: Traveler,
    db: DB,
):
    return change_reservation(db, module, booking_id, buyer, "cancel", payload.reason)


@router.post(
    "/provider/bookings/{booking_id}/action",
    response_model=ReservationResponse | EcoReservationResponse | BookingDetail,
)
def provider_reservation_action(
    booking_id: BookingId, payload: ReservationAction, reader: Reader
):
    if reader.category == "eco_tourism":
        return eco_reservations.change_reservation(
            reader.db,
            reader.provider.user,
            booking_id,
            payload.action,
            payload.reason,
            provider=reader.provider,
        )
    if reader.category == "activity":
        change_activity_booking(
            reader.db, reader.provider, booking_id, payload.action, payload.reason
        )
        return provider_booking_detail(booking_id, reader)
    module = {"tour_guide": GUIDE, "artisan": ARTISAN}.get(reader.category)
    if module is None:
        raise HTTPException(409, "Use the existing category booking actions")
    return change_reservation(
        reader.db,
        module,
        booking_id,
        reader.provider.user,
        payload.action,
        payload.reason,
        provider=reader.provider,
    )


@router.get("/provider/bookings", response_model=BookingPage)
def provider_bookings(
    reader: Reader,
    period: Period,
    page: Page = 1,
    page_size: PageSize = 20,
    state: Literal[
        "pending",
        "confirmed",
        "upcoming",
        "active",
        "completed",
        "cancelled",
        "rejected",
        "expired",
        "awaiting_payment",
        "ready_for_pickup",
    ]
    | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
):
    source = reader._bookings()
    if source is None:
        raise HTTPException(409, "This category has no native reservation workflow")
    cols = source.c
    query = select(source).where(*period_conditions(cols.created_at, period))
    if state == "upcoming":
        query = query.where(reader._upcoming(cols))
    elif state == "active":
        query = query.where(reader.active_booking(cols))
    elif state:
        query = query.where(cols.status == state)
    if q and (term := q.strip()):
        search = cols.title.icontains(term, autoescape=True)
        if (
            term.isascii()
            and term.isdecimal()
            and len(term) <= 10
            and int(term) <= 2147483647
        ):
            search = or_(search, cols.id == int(term))
        query = query.where(search)
    rows = reader.db.execute(
        query.order_by(cols.created_at.desc(), cols.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size + 1)
    ).all()
    return BookingPage(
        items=[project_booking(reader, row) for row in rows[:page_size]],
        page=page,
        page_size=page_size,
        has_more=len(rows) > page_size,
    )


@router.get("/provider/bookings/{booking_id}", response_model=BookingDetail)
def provider_booking_detail(
    booking_id: BookingId,
    reader: Reader,
    history_page: Page = 1,
    history_size: PageSize = 20,
):
    source = reader._bookings()
    row = (
        reader.db.execute(select(source).where(source.c.id == booking_id)).first()
        if source is not None
        else None
    )
    if row is None:
        raise HTTPException(404, "Owned booking not found")
    history = BookingStatusHistory
    ref = BookingReference
    events = reader.db.scalars(
        select(history)
        .join(ref, ref.id == history.booking_reference_id)
        .where(
            ref.module_type == booking_reference_module(reader.category),
            ref.target_booking_id == booking_id,
            ref.user_id == row.user_id,
            ref.provider_profile_id == reader.provider.id,
        )
        .order_by(history.created_at.desc(), history.id.desc())
        .offset((history_page - 1) * history_size)
        .limit(history_size + 1)
    ).all()
    return BookingDetail(
        **project_booking(reader, row).model_dump(),
        history=[
            BookingHistory(
                previous_status=event.from_status,
                status=event.to_status,
                occurred_at=utc_timestamp(event.created_at),
            )
            for event in events[:history_size]
        ],
        has_more_history=len(events) > history_size,
    )
