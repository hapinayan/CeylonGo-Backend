
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.models.promotion import HomestayPromotion


def calculate_booking_price(
    db: Session,
    homestay_id: int,
    check_in: date,
    check_out: date,
    price_per_night: Decimal,
) -> Decimal:
    """Calculate booking total using the highest eligible discount."""

    nights = (check_out - check_in).days

    if nights <= 0:
        raise ValueError(
            "Check-out date must be after check-in date"
        )

    base_price = Decimal(str(price_per_night)) * nights

    # A promotion must cover every booked night.
    # Check-out is exclusive, so the final booked night
    # is the day before check-out.
    last_booked_night = date.fromordinal(
        check_out.toordinal() - 1
    )

    promotion = (
        db.query(HomestayPromotion)
        .filter(
            HomestayPromotion.homestay_id == homestay_id,
            HomestayPromotion.is_active == True,
            HomestayPromotion.start_date <= check_in,
            HomestayPromotion.end_date >= last_booked_night,
        )
        .order_by(
            HomestayPromotion.discount_percent.desc(),
            HomestayPromotion.id.asc(),
        )
        .first()
    )

    if promotion is None:
        return base_price.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

    discount = Decimal(str(promotion.discount_percent))

    final_price = (
        base_price
        * (Decimal("100") - discount)
        / Decimal("100")
    )

    return final_price.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
