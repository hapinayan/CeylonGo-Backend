"""Eco campaign checkout, verified capture and attribution."""

import os
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, update

from app.core.booking_transactions import CAPTURED, lock_booking_resource
from app.core.publication import public_eco
from app.core.security import verify_sponsored_tracking_token
from app.models.eco_experience import EcoExperience
from app.models.eco_promotion import (
    EcoPromotionAttribution, EcoPromotionEventReceipt, EcoSponsoredPromotion,
    EcoSponsorshipCapture,
)
from app.models.provider_profile import ProviderProfile
from app.models.shared import AdvertisingPayment, PaymentTransaction
from app.models.user import User
from app.services.payhere_bookings import capture_amount, checkout_response, lock_capture

ORDER_PREFIX = "AD-ECO-"
ORDER_FORMAT = re.compile(r"AD-ECO-([1-9][0-9]{0,9})-[0-9a-f]{32}")


def ledger_enabled():
    return os.getenv("ECO_SPONSORSHIP_LEDGER_ENABLED") == "1"


def now_utc():
    return datetime.now(UTC).replace(tzinfo=None)


def active_campaign(now):
    campaign = EcoSponsoredPromotion
    predicates = (
        campaign.campaign_status == "active", campaign.payment_status == "paid",
        campaign.starts_at <= now, campaign.ends_at > now,
    )
    if ledger_enabled():
        applied_capture = select(EcoSponsorshipCapture.gateway_id).where(
            EcoSponsorshipCapture.promotion_id == campaign.id,
            EcoSponsorshipCapture.applied.is_(True),
        ).exists()
        predicates += (campaign.reviewed_by.is_not(None), applied_capture)
    return predicates


def lock_experience(db, experience_id):
    lock_booking_resource(db, f"eco_sponsorship_{experience_id}")


def expire_campaigns(db, experience_id):
    db.execute(update(EcoSponsoredPromotion).where(
        EcoSponsoredPromotion.eco_experience_id == experience_id,
        EcoSponsoredPromotion.campaign_status == "active",
        EcoSponsoredPromotion.ends_at <= now_utc(),
    ).values(campaign_status="expired", updated_at=now_utc()))


def locked_campaign(db, query):
    campaign = db.scalar(query)
    if campaign is None:
        raise HTTPException(404, "Eco campaign not found")
    lock_experience(db, campaign.eco_experience_id)
    campaign = db.scalar(query.execution_options(populate_existing=True))
    if campaign is None:
        raise HTTPException(404, "Eco campaign not found")
    return campaign


def listing_is_eligible(db, campaign):
    return db.scalar(select(EcoExperience.id).where(
        EcoExperience.id == campaign.eco_experience_id,
        EcoExperience.provider_profile_id == campaign.provider_profile_id,
        public_eco(),
    )) is not None


def checkout(db, provider, campaign_id):
    from app.services.eco_native_payments import require_merchant

    if not ledger_enabled() or os.getenv("ECO_SPONSORSHIP_CHECKOUT_ENABLED") != "1":
        raise HTTPException(503, "Eco sponsorship checkout is unavailable")
    require_merchant()
    campaign = locked_campaign(db, select(EcoSponsoredPromotion).where(
        EcoSponsoredPromotion.id == campaign_id,
        EcoSponsoredPromotion.provider_profile_id == provider.id,
    ))
    if (
        campaign.campaign_status != "approved" or campaign.reviewed_by is None
        or campaign.payment_status not in ("unpaid", "pending_verification", "failed")
        or campaign.package_price_snapshot <= 0 or not listing_is_eligible(db, campaign)
    ):
        raise HTTPException(409, "Eco campaign is ineligible for checkout")
    if campaign.payment_reference is None:
        campaign.payment_reference = f"{ORDER_PREFIX}{campaign.id}-{uuid4().hex}"
    order_match = ORDER_FORMAT.fullmatch(campaign.payment_reference)
    if not order_match or int(order_match[1]) != campaign.id:
        raise HTTPException(409, "Eco campaign has an incompatible payment reference")
    campaign.payment_status = "pending_verification"
    result = checkout_response(
        campaign.id, campaign.payment_reference, "Eco sponsorship",
        campaign.package_price_snapshot, campaign.currency_snapshot,
        db.get(User, provider.user_id),
    )
    db.commit()
    return result


def capture(db, order_id, gateway_id, raw_amount, currency, code):
    """Apply callbacks after the shared merchant and signature checks."""
    from app.services.eco_native_payments import require_merchant

    if not ledger_enabled():
        raise HTTPException(503, "Eco sponsorship callback is unavailable")
    require_merchant()
    order_match = ORDER_FORMAT.fullmatch(order_id)
    if not order_match or int(order_match[1]) > 2147483647:
        raise HTTPException(400, "Invalid Eco sponsorship order")
    amount = capture_amount(raw_amount)
    if code == "2":
        if not isinstance(gateway_id, str) or gateway_id != gateway_id.strip():
            raise HTTPException(400, "Invalid payment identifier")
        lock_capture(db, gateway_id, -1)
        existing_ad = db.scalar(select(AdvertisingPayment.id).where(
            func.lower(AdvertisingPayment.gateway_transaction_id) == gateway_id.lower(),
            AdvertisingPayment.payment_status == "completed",
        ).limit(1))
        existing_booking = db.scalar(select(PaymentTransaction.id).where(
            PaymentTransaction.gateway_name == "payhere",
            func.lower(PaymentTransaction.gateway_transaction_id) == gateway_id.lower(),
            PaymentTransaction.payment_status.in_((*CAPTURED, "refunded")),
        ).limit(1))
        if existing_ad is not None or existing_booking is not None:
            raise HTTPException(409, "Payment identifier belongs to another order")
    campaign = locked_campaign(db, select(EcoSponsoredPromotion).where(
        EcoSponsoredPromotion.id == int(order_match[1]),
        EcoSponsoredPromotion.payment_reference == order_id,
    ))
    if amount != campaign.package_price_snapshot or currency != campaign.currency_snapshot:
        raise HTTPException(400, "Payment does not match the Eco campaign")
    if code != "2":
        if code in ("-1", "-2") and campaign.payment_status == "pending_verification":
            campaign.payment_status = "failed"
            db.commit()
        return {"status": "ignored"}
    key = gateway_id.casefold()
    prior = db.get(EcoSponsorshipCapture, key)
    if prior is not None:
        if prior.promotion_id != campaign.id:
            raise HTTPException(409, "Payment identifier belongs to another order")
        return {"status": "already_processed"}
    provider = db.get(ProviderProfile, campaign.provider_profile_id)
    applied = (
        campaign.campaign_status == "approved" and campaign.reviewed_by is not None
        and campaign.payment_status in ("pending_verification", "failed")
        and provider is not None and listing_is_eligible(db, campaign)
    )
    now = now_utc()
    db.add(EcoSponsorshipCapture(
        gateway_id=key, promotion_id=campaign.id, amount=amount, currency=currency,
        applied=applied, captured_at=now,
    ))
    if applied:
        campaign.payment_status = "paid"
        campaign.campaign_status = "active"
        campaign.starts_at = now
        campaign.ends_at = now + timedelta(days=campaign.duration_days_snapshot)
        campaign.updated_at = now
    db.commit()
    return {"status": "ok" if applied else "requires_reconciliation"}


def reject_claimed_capture(db, payment_id):
    """Keep non-Eco PayHere callbacks from reusing an Eco capture identity."""
    if not ledger_enabled() or not payment_id or len(payment_id) > 100:
        return
    candidate = payment_id.strip().casefold()
    lock_booking_resource(db, f"payhere_capture_{candidate}")
    if db.get(EcoSponsorshipCapture, candidate) is not None:
        raise HTTPException(409, "Payment identifier belongs to another order")


def attribute_booking(db, listing_id, reference_id, token):
    if not token or not ledger_enabled():
        return
    try:
        claims = verify_sponsored_tracking_token(token)
    except ValueError:
        return
    campaign_id = claims.get("campaign_id")
    nonce = claims.get("nonce")
    if claims.get("eco_experience_id") != listing_id or not campaign_id or not nonce:
        return
    lock_experience(db, listing_id)
    now = now_utc()
    eligible_click = db.scalar(select(EcoPromotionEventReceipt.nonce).join(
        EcoSponsoredPromotion, EcoSponsoredPromotion.id == EcoPromotionEventReceipt.promotion_id,
    ).join(
        EcoExperience, EcoExperience.id == EcoSponsoredPromotion.eco_experience_id,
    ).where(
        EcoPromotionEventReceipt.nonce == nonce, EcoPromotionEventReceipt.event_type == "click",
        EcoSponsoredPromotion.id == campaign_id, EcoSponsoredPromotion.eco_experience_id == listing_id,
        EcoSponsoredPromotion.provider_profile_id == EcoExperience.provider_profile_id,
        public_eco(),
        *active_campaign(now),
    ))
    if eligible_click and db.scalar(select(EcoPromotionAttribution.nonce).where(
        (EcoPromotionAttribution.nonce == nonce)
        | (EcoPromotionAttribution.booking_reference_id == reference_id),
    )) is None:
        db.add(EcoPromotionAttribution(
            booking_reference_id=reference_id, promotion_id=campaign_id,
            nonce=nonce, recorded_on=now.date(),
        ))
