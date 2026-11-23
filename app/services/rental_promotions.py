from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.booking_transactions import lock_booking_resource
from app.core.publication import public_rental
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import AdvertisingPayment, ListingReference, PromotionCampaign
from app.services.payhere_bookings import capture_amount

RENTAL_CAMPAIGN_MODULES = ("rental", "rental_vehicle")


def verified_rental_promotion_payment():
    return select(AdvertisingPayment.id).where(
        AdvertisingPayment.campaign_id == PromotionCampaign.id,
        AdvertisingPayment.provider_profile_id == PromotionCampaign.provider_profile_id,
        AdvertisingPayment.amount == PromotionCampaign.budget,
        AdvertisingPayment.currency == PromotionCampaign.currency,
        AdvertisingPayment.gateway_name == "payhere",
        AdvertisingPayment.payment_status == "completed",
        AdvertisingPayment.verified_at.is_not(None),
        AdvertisingPayment.gateway_transaction_id.is_not(None),
    )


def has_eligible_rental_inventory(db, provider_id, listing_reference_id=None):
    eligible = select(RentalVehicle.id).where(
        RentalVehicle.provider_id == provider_id, public_rental(),
    )
    if listing_reference_id is not None:
        eligible = eligible.join(ListingReference, ListingReference.target_id == RentalVehicle.id).where(
            ListingReference.id == listing_reference_id,
            ListingReference.provider_profile_id == provider_id,
            ListingReference.module_type.in_(RENTAL_CAMPAIGN_MODULES),
        )
    return db.scalar(eligible.limit(1)) is not None


def capture_rental_promotion(db, payment, campaign, payment_id, raw_amount, currency, code):
    if code == "2":
        if not payment_id.strip() or len(payment_id) > 100:
            raise HTTPException(400, "Verified payment must include a transaction identifier")
        lock_booking_resource(db, f"payhere_ad_capture_{payment_id.casefold()}")
    lock_booking_resource(db, f"rental_promotion_{campaign.id}")
    db.refresh(payment)
    db.refresh(campaign)
    amount = capture_amount(raw_amount)
    if (
        payment.provider_profile_id != campaign.provider_profile_id
        or payment.gateway_name != "payhere"
        or amount != payment.amount or amount != campaign.budget
        or currency != payment.currency or currency != campaign.currency
    ):
        raise HTTPException(400, "Payment does not match rental campaign")
    if payment.payment_status == "completed":
        if code == "2" and payment_id.casefold() != (payment.gateway_transaction_id or "").casefold():
            raise HTTPException(409, "Rental campaign checkout already has a verified payment")
        return {"status": "success", "message": "Advertising payment callback already processed"}
    if code == "2":
        if db.scalar(select(AdvertisingPayment.id).where(
            AdvertisingPayment.id != payment.id,
            func.lower(AdvertisingPayment.gateway_transaction_id) == payment_id.lower(),
            AdvertisingPayment.payment_status == "completed",
        ).limit(1)) is not None:
            raise HTTPException(409, "Payment identifier belongs to another order")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        payment.gateway_transaction_id = payment_id
        payment.payment_status = "completed"
        payment.verified_at = payment.updated_at = now
    elif code in ("-1", "-2"):
        payment.payment_status = "failed"
    else:
        return {"status": "ignored"}
    db.commit()
    return {"status": "success", "message": "Advertising payment callback processed"}
