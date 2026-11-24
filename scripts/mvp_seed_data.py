"""Synthetic Academic MVP scenarios. Dates are naive UTC for SQL Server DATETIME2."""

from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from app.models import mvp

PREFIX = "academic-mvp-v1-1"
MARK = "[CeylonGo Academic MVP V1.1]"
VENUES = (
    ("homestay", "Ella", "Badulla", "6.866700", "81.046600", 8500, 1),
    ("bike", "Kandy", "Kandy", "7.290600", "80.633700", 4500, 1),
    ("bicycle", "Galle", "Galle", "6.053500", "80.221000", 1500, 1),
    ("adventure", "Kitulgala", "Kegalle", "6.993600", "80.412600", 6000, 6),
    ("guide", "Sigiriya", "Matale", "7.957000", "80.760300", 5000, 1),
)
# Module references for offline demonstrations. AI uses the existing template endpoint.
MOCK_INTEGRATIONS = {
    "TR-06": {"destination": "Ella", "duration_days": 2, "budget_level": "moderate"},
    "TR-08": {"label": "Ella", "latitude": 6.8667, "longitude": 81.0466},
    "TR-09": {"locality": "Ella", "temperature_c": 22, "condition": "partly cloudy"},
}


def request_key(label):
    return str(uuid5(NAMESPACE_URL, f"https://ceylongo.invalid/{PREFIX}/{label}"))


def digest(value):
    return sha256(value.encode("utf-8")).digest()


def populate(put, anchor, password_factory):
    """Write the manifest in FK order using the caller's transactional insert helper."""
    today = datetime.combine(anchor, datetime.min.time())
    created = today - timedelta(days=30)

    def account(key, role):
        return put(
            mvp.MVPUser, {"email": f"{key}@{PREFIX}.invalid"},
            full_name=f"{MARK} {key.title()}", role=role,
            created_at=created, updated_at=created,
            on_insert=lambda: {"hashed_password": password_factory(role)},
        )

    admin = account("admin", "admin")
    traveler = account("traveler-one", "traveler")
    other = account("traveler-two", "traveler")
    audit = []

    def profile(key, district, status):
        owner = account(key, "provider")
        decided = status != "pending"
        provider = put(
            mvp.MVPProviderProfile, {"user_id": owner},
            business_name=f"{MARK} {key.title()}", phone="0000000000", district=district,
            verification_status=status, created_at=created, submitted_at=created,
            updated_at=created, decided_at=created if decided else None,
            decided_by=admin if decided else None,
            decision_reason=f"{MARK} Synthetic verification" if decided else None,
        )
        if decided:
            audit.append(("provider_decision", "subject_provider_id", provider, key))
        return owner, provider

    def listing(key, category, locality, district, coords, price, provider, status):
        approved = status == "approved"
        decided = status != "pending"
        listing_id = put(
            mvp.MVPListing, {"title": f"{MARK} {key.title()}"},
            category=category, provider_id=provider, locality=locality, district=district,
            latitude=coords[0], longitude=coords[1], price=Decimal(price), currency="LKR",
            description=f"Synthetic demonstration listing in {locality}.",
            approval_status=status, is_active=approved, submitted_at=created,
            created_at=created, updated_at=created, decided_at=created if decided else None,
            decided_by=admin if decided else None,
            decision_reason=f"{MARK} Synthetic listing review" if decided else None,
        )
        if decided:
            audit.append(("listing_decision", "subject_listing_id", listing_id, key))
        return listing_id

    def document(provider, key, kind, unit=None, verified=True):
        storage = f"mock/{PREFIX}/documents/{key}-{kind}.txt"
        put(
            mvp.MVPProviderDocument, {"storage_key": storage},
            provider_id=provider, unit_id=unit, kind=kind,
            sha256=digest(f"Synthetic document: {storage}"),
            status="verified" if verified else "pending", created_at=created,
            reviewed_by=admin if verified else None, reviewed_at=created if verified else None,
            expires_on=anchor + timedelta(days=365) if unit else None,
        )

    for status in ("pending", "rejected"):
        key = f"provider-{status}"
        _, provider = profile(key, "Badulla", status)
        document(provider, key, "identity", verified=False)
        listing(f"homestay-{status}", "homestay", "Ella", "Badulla", (None, None), 8500,
                provider, status)

    journeys = {
        "homestay": ("confirmed", 7, 1),
        "bike": ("pending", 8, 1),
        "bicycle": ("rejected", 9, 1),
        "adventure": ("completed", -7, 2),
        "guide": ("cancelled", 10, 1),
    }
    booked_at = today - timedelta(days=10)
    for category, locality, district, lat, lon, price, capacity in VENUES:
        owner, provider = profile(category, district, "approved")
        listing_id = listing(category, category, locality, district, (Decimal(lat), Decimal(lon)),
                             price, provider, "approved")
        put(
            mvp.MVPListingMedia, {"storage_key": f"mock/{PREFIX}/media/{category}.jpg"},
            listing_id=listing_id, sort_order=0, alt_text=f"Demo {category} in {locality}",
            created_at=created,
        )
        unit = put(
            mvp.MVPInventoryUnit, {"listing_id": listing_id, "label": f"{MARK} {category} unit"},
            provider_id=provider, capacity=capacity, is_active=True,
        )
        for kind in ("identity", "business"):
            document(provider, category, kind)
        if category == "bike":
            for kind in ("revenue_license", "insurance"):
                document(provider, category, kind, unit)

        duration = timedelta(days=1) if category == "homestay" else timedelta(hours=2)

        def slot(offset, is_open):
            start = today + timedelta(days=offset, hours=4)
            end = start + duration
            slot_id = put(
                mvp.MVPAvailabilitySlot, {"unit_id": unit, "starts_at": start},
                listing_id=listing_id, ends_at=end, capacity=capacity, is_open=is_open,
            )
            return slot_id, start, end

        slot(14, True)
        slot(16, False)
        status, offset, quantity = journeys[category]
        slot_id, starts, ends = slot(offset, offset > 0)
        steps = ["pending"]
        if status == "completed":
            steps.append("confirmed")
        if status != "pending":
            steps.append(status)
        event_times = [booked_at + timedelta(hours=i) for i in range(len(steps))]
        if status == "completed":
            event_times[-1] = ends
        key = request_key(f"booking/{category}")
        customer = other if category in {"bicycle", "guide"} else traveler
        total = Decimal(price) * quantity
        booking = put(
            mvp.MVPBooking, {"traveler_id": customer, "request_key": key},
            listing_id=listing_id, request_hash=digest(f"{customer}:{key}"),
            starts_at=starts, ends_at=ends, quantity=quantity, total_amount=total,
            currency="LKR", status=status, created_at=booked_at, updated_at=event_times[-1],
        )
        put(
            mvp.MVPBookingAllocation, {"booking_id": booking, "slot_id": slot_id},
            listing_id=listing_id, quantity=quantity, reserved_at=booked_at,
            released_at=event_times[-1] if status in {"rejected", "cancelled"} else None,
        )
        for i, (state, timestamp) in enumerate(zip(steps, event_times)):
            put(
                mvp.MVPBookingStatusHistory, {"booking_id": booking, "to_status": state},
                from_status=steps[i-1] if i else None,
                actor_user_id=customer if state in {"pending", "cancelled"} else owner,
                occurred_at=timestamp, reason=f"{MARK} {state}",
            )
        payments = {
            "homestay": [("at_location", "unpaid")],
            "adventure": [("simulated", "demo_failed"), ("simulated", "demo_succeeded")],
        }
        for attempt, (method, result) in enumerate(payments.get(category, ()), 1):
            pay_key = request_key(f"payment/{category}/{attempt}")
            paid_at = booked_at + timedelta(minutes=attempt * 15)
            put(
                mvp.MVPBookingPayment, {"booking_id": booking, "request_key": pay_key},
                request_hash=digest(f"{booking}:{pay_key}:{attempt}"),
                attempt_no=attempt, method=method, status=result, display_amount=total,
                currency="LKR", created_at=paid_at,
                resolved_at=paid_at if method == "simulated" else None,
            )

    for visibility, body in (("visible", "The Ella trail was quiet this morning."),
                             ("hidden", "Synthetic post for the moderation queue.")):
        post = put(
            mvp.MVPCommunityPost, {"author_id": traveler, "body": f"{MARK} {body}"},
            visibility=visibility, created_at=created, updated_at=created,
        )
        put(
            mvp.MVPCommunityComment,
            {"post_id": post, "author_id": other, "body": f"{MARK} Thanks for sharing."},
            visibility=visibility, created_at=created, updated_at=created,
        )
        if visibility == "visible":
            put(mvp.MVPCommunityPostLike, {"post_id": post, "user_id": other}, created_at=created)
        else:
            audit.append(("post_moderation", "subject_post_id", post, "hidden post"))

    for action, subject_column, subject_id, label in audit:
        put(
            mvp.MVPAdminAuditLog, {"actor_id": admin, "action": action, subject_column: subject_id},
            reason=f"{MARK} {label}", occurred_at=created,
        )
