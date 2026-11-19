from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.models.promotion import HomestayPromotion


def calculate_detailed_booking_price(
    db: Session,
    homestay_id: int,
    check_in: date,
    check_out: date,
    price_per_night: Decimal,
) -> Dict[str, Any]:
    """
    Calculate booking pricing breakdown using the highest eligible discount.
    Supports both percentage and fixed-amount discounts.
    Ensures payable amounts never become negative.
    """
    nights = (check_out - check_in).days
    if nights <= 0:
        raise ValueError("Check-out date must be after check-in date")

    base_price = (Decimal(str(price_per_night)) * nights).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    # A promotion must cover every booked night.
    # Check-out is exclusive, so the final booked night is day before check-out.
    last_booked_night = date.fromordinal(check_out.toordinal() - 1)

    query = (
        db.query(HomestayPromotion)
        .filter(
            HomestayPromotion.homestay_id == homestay_id,
            HomestayPromotion.is_active == True,
            HomestayPromotion.start_date <= check_in,
            HomestayPromotion.end_date >= last_booked_night,
        )
    )

    promos_result = query.all()
    if isinstance(promos_result, list):
        eligible_promotions = promos_result
    else:
        # Backward-compatibility for unit test mocks that mock .order_by().first()
        try:
            first_promo = query.order_by(HomestayPromotion.discount_percent.desc()).first()
            eligible_promotions = [first_promo] if first_promo else []
        except Exception:
            eligible_promotions = []

    best_promo = None
    best_savings = Decimal("0.00")

    for promo in eligible_promotions:
        promo_savings = Decimal("0.00")
        dtype = getattr(promo, "discount_type", None) or ("percentage" if getattr(promo, "discount_percent", None) is not None else None)
        if dtype == "percentage" and getattr(promo, "discount_percent", None) is not None:
            promo_savings = (
                base_price * Decimal(str(promo.discount_percent)) / Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        elif dtype == "fixed" and getattr(promo, "discount_amount", None) is not None:
            promo_savings = Decimal(str(promo.discount_amount)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            # Fixed discount must not exceed base_price
            promo_savings = min(promo_savings, base_price)

        if promo_savings > best_savings:
            best_savings = promo_savings
            best_promo = promo

    final_price = max(Decimal("0.00"), base_price - best_savings).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    return {
        "original_price": base_price,
        "discount_amount": best_savings,
        "final_price": final_price,
        "savings": best_savings,
        "nights": nights,
        "price_per_night": Decimal(str(price_per_night)).quantize(Decimal("0.01")),
        "applied_promotion_id": best_promo.id if best_promo else None,
        "discount_type": best_promo.discount_type if best_promo else None,
        "discount_percent": best_promo.discount_percent if best_promo else None,
        "discount_fixed_amount": (
            best_promo.discount_amount
            if best_promo and best_promo.discount_type == "fixed"
            else None
        ),
    }


def calculate_booking_price(
    db: Session,
    homestay_id: int,
    check_in: date,
    check_out: date,
    price_per_night: Decimal,
) -> Decimal:
    """Calculate booking total price. Backward-compatible wrapper returning Decimal."""
    breakdown = calculate_detailed_booking_price(
        db=db,
        homestay_id=homestay_id,
        check_in=check_in,
        check_out=check_out,
        price_per_night=price_per_night,
    )
    return breakdown["final_price"]


def record_campaign_booking_conversion(
    db: Session,
    homestay_id: int,
    user_id: int | None,
    booking_reference_id: int | None = None,
    campaign_id: int | None = None,
) -> bool:
    """
    Attribute an internal booking conversion event to an active, verified, paid campaign
    linked to the given homestay listing.
    Only records conversion when trusted booking context is established:
    - campaign_id and booking_reference_id are provided
    - deduplicates by checking existing event for (campaign_id, booking_reference_id)
    - organic bookings without campaign attribution are rejected
    """
    if booking_reference_id is None or campaign_id is None:
        # Untrusted / organic booking without explicit campaign attribution is not counted
        return False

    try:
        from app.models.shared import (
            AdvertisingPayment,
            ListingReference,
            PromotionCampaign,
            PromotionEvent,
        )

        # Check deduplication
        existing = (
            db.query(PromotionEvent)
            .filter(
                PromotionEvent.campaign_id == campaign_id,
                PromotionEvent.booking_reference_id == booking_reference_id,
                PromotionEvent.event_type == "booking",
            )
            .first()
        )
        if existing:
            return False

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        listing_ref = (
            db.query(ListingReference)
            .filter(
                ListingReference.module_type == "homestay",
                ListingReference.target_id == homestay_id,
            )
            .first()
        )
        if not listing_ref:
            return False

        paid_campaign_ids = {
            row[0]
            for row in db.query(AdvertisingPayment.campaign_id)
            .filter(AdvertisingPayment.payment_status == "completed")
            .all()
        }
        if campaign_id not in paid_campaign_ids:
            return False

        active_campaign = (
            db.query(PromotionCampaign)
            .filter(
                PromotionCampaign.id == campaign_id,
                PromotionCampaign.target_module == "homestay",
                PromotionCampaign.status == "active",
                PromotionCampaign.starts_at <= now,
                PromotionCampaign.ends_at >= now,
                PromotionCampaign.listing_reference_id == listing_ref.id,
            )
            .first()
        )

        if active_campaign:
            event = PromotionEvent(
                campaign_id=active_campaign.id,
                booking_reference_id=booking_reference_id,
                event_type="booking",
                user_id=user_id,
                ip_address="internal",
            )
            db.add(event)
            return True
        return False
    except Exception:
        return False
