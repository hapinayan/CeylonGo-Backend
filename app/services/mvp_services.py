"""
CeylonGo Academic MVP — Unified Core Business Logic Services
Encapsulates domain logic, cross-row invariants, state transitions, and audit logging.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
import hashlib
import secrets
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.mvp_security import hash_binary_token, hash_password, verify_password, create_mvp_access_token
from app.models.mvp import (
    MVPAdminAuditLog,
    MVPAvailabilitySlot,
    MVPBooking,
    MVPBookingAllocation,
    MVPBookingPayment,
    MVPBookingStatusHistory,
    MVPCommunityComment,
    MVPCommunityPost,
    MVPCommunityPostLike,
    MVPInventoryUnit,
    MVPListing,
    MVPListingMedia,
    MVPPasswordReset,
    MVPProviderDocument,
    MVPProviderEmailChallenge,
    MVPProviderProfile,
    MVPUser,
)
from app.schemas.mvp import (
    AdminListingDecision,
    AdminModerationRequest,
    AdminProviderDecision,
    AtLocationPaymentRequest,
    AvailabilitySlotCreate,
    BookingCreateRequest,
    BookingStatusUpdateRequest,
    CommentCreateRequest,
    EmailChallengeRequest,
    EmailChallengeVerify,
    InventoryUnitCreate,
    ItineraryDayPlan,
    ItineraryRequest,
    ItineraryResponse,
    ListingCreateRequest,
    ListingMediaCreate,
    ListingUpdateRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    PostCreateRequest,
    ProviderDocumentUpload,
    ProviderRegisterRequest,
    SimulatedPaymentRequest,
    UserLoginRequest,
    UserRegisterRequest,
)


# ============================================================================
# 1. Authentication Service
# ============================================================================
class MVPAuthService:
    @staticmethod
    def check_email(db: Session, email: str) -> bool:
        normalized_email = email.strip().lower()
        if not normalized_email or "@" not in normalized_email or len(normalized_email) > 254:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid email address format.",
            )
        user = db.query(MVPUser).filter(func.lower(MVPUser.email) == normalized_email).first()
        return user is not None

    @staticmethod
    def register_user(db: Session, req: UserRegisterRequest) -> MVPUser:
        normalized_email = req.email.strip().lower()
        existing = db.query(MVPUser).filter(func.lower(MVPUser.email) == normalized_email).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this email address already exists.",
            )

        now = datetime.now(timezone.utc)
        user = MVPUser(
            email=normalized_email,
            full_name=req.full_name.strip(),
            phone=req.phone.strip() if req.phone else None,
            hashed_password=hash_password(req.password),
            role=req.role,
            is_active=True,
            auth_version=0,
            credential_version=0,
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def login_user(db: Session, req: UserLoginRequest) -> tuple[str, MVPUser]:
        normalized_email = req.email.strip().lower()
        user = db.query(MVPUser).filter(func.lower(MVPUser.email) == normalized_email).first()
        if not user or not verify_password(req.password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is suspended or inactive.",
            )

        token = create_mvp_access_token(
            user_id=user.id,
            email=user.email,
            role=user.role,
            auth_version=user.auth_version,
            credential_version=user.credential_version,
        )
        return token, user

    @staticmethod
    def login_with_google(db: Session, id_token: str) -> tuple[str, MVPUser]:
        from app.core.google_auth import verify_google_id_token
        google_payload = verify_google_id_token(id_token)
        google_email = google_payload["email"]
        google_name = google_payload["full_name"]

        user = db.query(MVPUser).filter(func.lower(MVPUser.email) == google_email).first()
        now = datetime.now(timezone.utc)

        if user:
            if not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User account is suspended or inactive.",
                )
        else:
            # Create new traveler account with secure unguessable random password
            user = MVPUser(
                email=google_email,
                full_name=google_name,
                phone=None,
                hashed_password=hash_password(secrets.token_urlsafe(32)),
                role="traveler",
                is_active=True,
                auth_version=0,
                credential_version=0,
                created_at=now,
                updated_at=now,
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        token = create_mvp_access_token(
            user_id=user.id,
            email=user.email,
            role=user.role,
            auth_version=user.auth_version,
            credential_version=user.credential_version,
        )
        return token, user

    @staticmethod
    def invalidate_sessions(db: Session, user: MVPUser) -> None:
        """Increments auth_version to invalidate all currently issued JWTs."""
        user.auth_version += 1
        user.updated_at = datetime.now(timezone.utc)
        db.commit()

    @staticmethod
    def request_password_reset(db: Session, req: PasswordResetRequest, send_email: bool = True) -> str:
        normalized_email = req.email.strip().lower()
        user = db.query(MVPUser).filter(func.lower(MVPUser.email) == normalized_email).first()
        if not user:
            # Return demo/dummy token to prevent timing side-channels and account enumeration
            return secrets.token_urlsafe(32)

        raw_token = secrets.token_urlsafe(32)
        token_hash = hash_binary_token(raw_token)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=30)

        reset = MVPPasswordReset(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
            created_at=now,
        )
        db.add(reset)
        db.commit()

        if send_email:
            try:
                from app.core.email import (
                    build_password_reset_url,
                    is_smtp_configured,
                    send_password_reset_email,
                )
                if is_smtp_configured():
                    reset_url = build_password_reset_url(raw_token)
                    send_password_reset_email(
                        to_email=user.email,
                        reset_url=reset_url,
                        user_name=user.full_name,
                    )
            except Exception:
                pass

        return raw_token

    @staticmethod
    def verify_reset_token(db: Session, token: str) -> MVPUser:
        token_hash = hash_binary_token(token)
        now = datetime.now(timezone.utc)
        reset = (
            db.query(MVPPasswordReset)
            .filter(
                MVPPasswordReset.token_hash == token_hash,
                MVPPasswordReset.used_at.is_(None),
                MVPPasswordReset.expires_at > now,
            )
            .first()
        )
        if not reset:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid, expired, or previously used password reset token.",
            )

        user = db.query(MVPUser).filter(MVPUser.id == reset.user_id).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User associated with token not found.",
            )
        return user

    @staticmethod
    def confirm_password_reset(db: Session, req: PasswordResetConfirm) -> None:
        token_hash = hash_binary_token(req.token)
        now = datetime.now(timezone.utc)
        reset = (
            db.query(MVPPasswordReset)
            .filter(
                MVPPasswordReset.token_hash == token_hash,
                MVPPasswordReset.used_at.is_(None),
                MVPPasswordReset.expires_at > now,
            )
            .first()
        )
        if not reset:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid, expired, or previously used password reset token.",
            )

        user = db.query(MVPUser).filter(MVPUser.id == reset.user_id).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

        reset.used_at = now
        user.hashed_password = hash_password(req.new_password)
        user.credential_version += 1
        user.auth_version += 1  # Invalidate existing sessions
        user.updated_at = now
        db.commit()


# ============================================================================
# 2. Provider Service
# ============================================================================
class MVPProviderService:
    @staticmethod
    def register_profile(db: Session, user: MVPUser, req: ProviderRegisterRequest) -> MVPProviderProfile:
        existing = db.query(MVPProviderProfile).filter(MVPProviderProfile.user_id == user.id).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Provider profile already exists for this account.",
            )

        now = datetime.now(timezone.utc)
        profile = MVPProviderProfile(
            user_id=user.id,
            business_name=req.business_name.strip(),
            phone=req.phone.strip(),
            district=req.district.strip(),
            verification_status="pending",
            submitted_at=now,
            created_at=now,
            updated_at=now,
        )
        # Update user role to provider if not already
        if user.role != "provider" and user.role != "admin":
            user.role = "provider"
            user.updated_at = now

        db.add(profile)
        db.commit()
        db.refresh(profile)
        return profile

    @staticmethod
    def upload_document(db: Session, user: MVPUser, req: ProviderDocumentUpload) -> MVPProviderDocument:
        profile = db.query(MVPProviderProfile).filter(MVPProviderProfile.user_id == user.id).first()
        if not profile:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Create provider profile first.")

        # Cross-row constraint check:
        # vehicle evidence (revenue_license, insurance) requires unit_id; identity/business requires unit_id IS NULL
        if req.kind in ("revenue_license", "insurance") and not req.unit_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Document kind '{req.kind}' requires an associated unit_id.",
            )
        if req.kind in ("identity", "business") and req.unit_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Document kind '{req.kind}' must not be attached to a specific inventory unit.",
            )

        # Validate unit belongs to provider if supplied
        if req.unit_id:
            unit = db.query(MVPInventoryUnit).filter(
                MVPInventoryUnit.id == req.unit_id,
                MVPInventoryUnit.provider_id == profile.id
            ).first()
            if not unit:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inventory unit not found.")

        now = datetime.now(timezone.utc)
        doc = MVPProviderDocument(
            provider_id=profile.id,
            unit_id=req.unit_id,
            kind=req.kind,
            storage_key=req.storage_key,
            sha256=bytes.fromhex(req.sha256_hex),
            status="pending",
            expires_on=req.expires_on,
            created_at=now,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        return doc

    @staticmethod
    def create_email_challenge(db: Session, req: EmailChallengeRequest) -> str:
        code = f"{secrets.randbelow(900000) + 100000}"
        challenge_hash = hashlib.sha256(f"{req.email.lower()}:{code}".encode("utf-8")).digest()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=15)

        challenge = MVPProviderEmailChallenge(
            email=req.email.lower(),
            challenge_hash=challenge_hash,
            expires_at=expires_at,
            attempts=0,
            created_at=now,
        )
        db.add(challenge)
        db.commit()
        return code

    @staticmethod
    def verify_email_challenge(db: Session, req: EmailChallengeVerify) -> bool:
        now = datetime.now(timezone.utc)
        challenge = (
            db.query(MVPProviderEmailChallenge)
            .filter(
                MVPProviderEmailChallenge.email == req.email.lower(),
                MVPProviderEmailChallenge.consumed_at.is_(None),
                MVPProviderEmailChallenge.expires_at > now,
            )
            .order_by(MVPProviderEmailChallenge.id.desc())
            .first()
        )
        if not challenge:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active email challenge found.")

        if challenge.attempts >= 10:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts. Request a new challenge.")

        challenge.attempts += 1
        expected_hash = hashlib.sha256(f"{req.email.lower()}:{req.code}".encode("utf-8")).digest()
        if challenge.challenge_hash != expected_hash:
            db.commit()
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect verification code.")

        challenge.verified_at = now
        challenge.consumed_at = now
        db.commit()
        return True


# ============================================================================
# 3. Listing & Inventory Service
# ============================================================================
class MVPListingService:
    @staticmethod
    def create_listing(db: Session, user: MVPUser, req: ListingCreateRequest) -> MVPListing:
        profile = db.query(MVPProviderProfile).filter(MVPProviderProfile.user_id == user.id).first()
        if not profile:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only registered providers can create listings.")

        # Coordinate bounds check
        if (req.latitude is not None and req.longitude is None) or (req.latitude is None and req.longitude is not None):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Coordinates must both be present or both absent.")
        if req.latitude is not None and not (-90.0 <= req.latitude <= 90.0 and -180.0 <= req.longitude <= 180.0):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Coordinates out of valid legal bounds.")

        now = datetime.now(timezone.utc)
        listing = MVPListing(
            provider_id=profile.id,
            category=req.category,
            title=req.title.strip(),
            description=req.description.strip(),
            district=req.district.strip(),
            locality=req.locality.strip(),
            latitude=req.latitude,
            longitude=req.longitude,
            price=req.price,
            currency="LKR",
            approval_status="pending",
            is_active=False,  # Active requires approved
            submitted_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(listing)
        db.commit()
        db.refresh(listing)
        return listing

    @staticmethod
    def add_media(db: Session, user: MVPUser, listing_id: int, req: ListingMediaCreate) -> MVPListingMedia:
        listing = db.query(MVPListing).filter(MVPListing.id == listing_id).first()
        if not listing:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found.")
        now = datetime.now(timezone.utc)
        media = MVPListingMedia(
            listing_id=listing.id,
            storage_key=req.storage_key,
            sort_order=req.sort_order,
            alt_text=req.alt_text,
            created_at=now,
        )
        db.add(media)
        db.commit()
        db.refresh(media)
        return media

    @staticmethod
    def add_inventory_unit(db: Session, user: MVPUser, listing_id: int, req: InventoryUnitCreate) -> MVPInventoryUnit:
        listing = db.query(MVPListing).filter(MVPListing.id == listing_id).first()
        if not listing:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found.")
        unit = MVPInventoryUnit(
            listing_id=listing.id,
            provider_id=listing.provider_id,
            label=req.label.strip(),
            capacity=req.capacity,
            is_active=True,
        )
        db.add(unit)
        db.commit()
        db.refresh(unit)
        return unit

    @staticmethod
    def add_availability_slot(db: Session, listing_id: int, req: AvailabilitySlotCreate) -> MVPAvailabilitySlot:
        listing = db.query(MVPListing).filter(MVPListing.id == listing_id).first()
        if not listing:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found.")
        unit = db.query(MVPInventoryUnit).filter(MVPInventoryUnit.id == req.unit_id, MVPInventoryUnit.listing_id == listing_id).first()
        if not unit:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inventory unit not found for this listing.")

        if req.ends_at <= req.starts_at:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Slot ends_at must be strictly greater than starts_at.")

        slot = MVPAvailabilitySlot(
            unit_id=unit.id,
            listing_id=listing.id,
            starts_at=req.starts_at,
            ends_at=req.ends_at,
            capacity=req.capacity,
            is_open=req.is_open,
        )
        db.add(slot)
        db.commit()
        db.refresh(slot)
        return slot

    @staticmethod
    def search_listings(
        db: Session,
        category: str | None = None,
        district: str | None = None,
        max_price: Decimal | None = None,
        limit: int = 50,
    ) -> list[MVPListing]:
        q = db.query(MVPListing).filter(MVPListing.is_active == True, MVPListing.approval_status == "approved")
        if category:
            q = q.filter(MVPListing.category == category)
        if district:
            q = q.filter(func.lower(MVPListing.district) == district.strip().lower())
        if max_price is not None:
            q = q.filter(MVPListing.price <= max_price)
        return q.order_by(MVPListing.id.desc()).limit(limit).all()


# ============================================================================
# 4. Booking & Allocation Service
# ============================================================================
class MVPBookingService:
    @staticmethod
    def create_booking(db: Session, traveler: MVPUser, req: BookingCreateRequest) -> MVPBooking:
        listing = db.query(MVPListing).filter(MVPListing.id == req.listing_id).first()
        if not listing:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found.")
        if not listing.is_active or listing.approval_status != "approved":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Listing is not available for booking.")

        if req.ends_at <= req.starts_at:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Booking ends_at must be later than starts_at.")

        # Find matching open slots for this listing that overlap the requested range
        slots = (
            db.query(MVPAvailabilitySlot)
            .filter(
                MVPAvailabilitySlot.listing_id == listing.id,
                MVPAvailabilitySlot.is_open == True,
                MVPAvailabilitySlot.starts_at < req.ends_at,
                MVPAvailabilitySlot.ends_at > req.starts_at,
            )
            .all()
        )

        # Capacity check across all overlapping slots
        if not slots:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No available availability slots for the requested dates.")

        for slot in slots:
            # Sum unreleased allocations on this slot
            allocated = (
                db.query(func.coalesce(func.sum(MVPBookingAllocation.quantity), 0))
                .filter(
                    MVPBookingAllocation.slot_id == slot.id,
                    MVPBookingAllocation.released_at.is_(None),
                )
                .scalar()
            )
            if (allocated + req.quantity) > slot.capacity:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Requested quantity ({req.quantity}) exceeds remaining slot capacity ({slot.capacity - allocated}).",
                )

        now = datetime.now(timezone.utc)
        total_amount = listing.price * Decimal(req.quantity)
        request_hash = hashlib.sha256(f"{traveler.id}:{req.request_key}".encode("utf-8")).digest()

        booking = MVPBooking(
            listing_id=listing.id,
            traveler_id=traveler.id,
            request_key=req.request_key,
            request_hash=request_hash,
            starts_at=req.starts_at,
            ends_at=req.ends_at,
            quantity=req.quantity,
            total_amount=total_amount,
            currency="LKR",
            status="pending",
            created_at=now,
            updated_at=now,
        )
        db.add(booking)
        db.flush()  # Obtain booking.id

        # Create allocations for each slot
        for slot in slots:
            alloc = MVPBookingAllocation(
                booking_id=booking.id,
                slot_id=slot.id,
                listing_id=listing.id,
                quantity=req.quantity,
                reserved_at=now,
                released_at=None,
            )
            db.add(alloc)

        # Status history entry
        hist = MVPBookingStatusHistory(
            booking_id=booking.id,
            actor_user_id=traveler.id,
            from_status=None,
            to_status="pending",
            reason="Initial booking request created",
            occurred_at=now,
        )
        db.add(hist)
        db.commit()
        db.refresh(booking)
        return booking

    @staticmethod
    def update_booking_status(db: Session, actor: MVPUser, booking_id: int, req: BookingStatusUpdateRequest) -> MVPBooking:
        booking = db.query(MVPBooking).filter(MVPBooking.id == booking_id).first()
        if not booking:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found.")

        current = booking.status
        target = req.to_status

        # Transition validation per ck_booking_status_history_transitions
        allowed_transitions = {
            "pending": ["confirmed", "rejected", "cancelled"],
            "confirmed": ["completed", "cancelled"],
        }
        if current not in allowed_transitions or target not in allowed_transitions[current]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Illegal status transition from '{current}' to '{target}'.",
            )

        now = datetime.now(timezone.utc)
        booking.status = target
        booking.updated_at = now

        # If rejected or cancelled, release slot allocations
        if target in ("rejected", "cancelled"):
            db.query(MVPBookingAllocation).filter(
                MVPBookingAllocation.booking_id == booking.id,
                MVPBookingAllocation.released_at.is_(None),
            ).update({"released_at": now})

        # Insert history record
        hist = MVPBookingStatusHistory(
            booking_id=booking.id,
            actor_user_id=actor.id,
            from_status=current,
            to_status=target,
            reason=req.reason or f"Status transitioned to {target}",
            occurred_at=now,
        )
        db.add(hist)
        db.commit()
        db.refresh(booking)
        return booking


# ============================================================================
# 5. Payment Service
# ============================================================================
class MVPPaymentService:
    @staticmethod
    def process_simulated(db: Session, req: SimulatedPaymentRequest) -> MVPBookingPayment:
        booking = db.query(MVPBooking).filter(MVPBooking.id == req.booking_id).first()
        if not booking:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found.")

        # Check single succeeded demo payment constraint
        existing_succeeded = db.query(MVPBookingPayment).filter(
            MVPBookingPayment.booking_id == booking.id,
            MVPBookingPayment.status == "demo_succeeded"
        ).first()
        if existing_succeeded:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Booking payment has already demo_succeeded.",
            )

        now = datetime.now(timezone.utc)
        payment_status = "demo_succeeded" if req.simulate_outcome == "success" else "demo_failed"
        request_hash = hashlib.sha256(f"{booking.id}:{req.request_key}:{req.attempt_no}".encode("utf-8")).digest()

        payment = MVPBookingPayment(
            booking_id=booking.id,
            request_key=req.request_key,
            request_hash=request_hash,
            attempt_no=req.attempt_no,
            method="simulated",
            status=payment_status,
            display_amount=booking.total_amount,
            currency="LKR",
            created_at=now,
            resolved_at=now,
        )
        db.add(payment)

        # On demo_succeeded, transition booking to confirmed if still pending
        if payment_status == "demo_succeeded" and booking.status == "pending":
            booking.status = "confirmed"
            booking.updated_at = now
            hist = MVPBookingStatusHistory(
                booking_id=booking.id,
                actor_user_id=booking.traveler_id,
                from_status="pending",
                to_status="confirmed",
                reason="Simulated payment succeeded",
                occurred_at=now,
            )
            db.add(hist)

        db.commit()
        db.refresh(payment)
        return payment

    @staticmethod
    def process_at_location(db: Session, req: AtLocationPaymentRequest) -> MVPBookingPayment:
        booking = db.query(MVPBooking).filter(MVPBooking.id == req.booking_id).first()
        if not booking:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found.")

        # Check single unpaid payment constraint
        existing_unpaid = db.query(MVPBookingPayment).filter(
            MVPBookingPayment.booking_id == booking.id,
            MVPBookingPayment.status == "unpaid"
        ).first()
        if existing_unpaid:
            return existing_unpaid

        now = datetime.now(timezone.utc)
        request_hash = hashlib.sha256(f"{booking.id}:{req.request_key}:1".encode("utf-8")).digest()

        payment = MVPBookingPayment(
            booking_id=booking.id,
            request_key=req.request_key,
            request_hash=request_hash,
            attempt_no=1,
            method="at_location",
            status="unpaid",
            display_amount=booking.total_amount,
            currency="LKR",
            created_at=now,
            resolved_at=None,
        )
        db.add(payment)

        if booking.status == "pending":
            booking.status = "confirmed"
            booking.updated_at = now
            hist = MVPBookingStatusHistory(
                booking_id=booking.id,
                actor_user_id=booking.traveler_id,
                from_status="pending",
                to_status="confirmed",
                reason="Pay at location selected — confirmed",
                occurred_at=now,
            )
            db.add(hist)

        db.commit()
        db.refresh(payment)
        return payment


# ============================================================================
# 6. Community Service
# ============================================================================
class MVPCommunityService:
    @staticmethod
    def create_post(db: Session, author: MVPUser, req: PostCreateRequest) -> MVPCommunityPost:
        now = datetime.now(timezone.utc)
        post = MVPCommunityPost(
            author_id=author.id,
            body=req.body.strip(),
            visibility="visible",
            created_at=now,
            updated_at=now,
        )
        db.add(post)
        db.commit()
        db.refresh(post)
        return post

    @staticmethod
    def list_posts(db: Session, limit: int = 30) -> list[dict]:
        posts = (
            db.query(MVPCommunityPost)
            .filter(MVPCommunityPost.visibility == "visible")
            .order_by(MVPCommunityPost.id.desc())
            .limit(limit)
            .all()
        )
        results = []
        for p in posts:
            likes = db.query(func.count(MVPCommunityPostLike.post_id)).filter(MVPCommunityPostLike.post_id == p.id).scalar()
            comments = db.query(func.count(MVPCommunityComment.id)).filter(MVPCommunityComment.post_id == p.id, MVPCommunityComment.visibility == "visible").scalar()
            results.append({
                "id": p.id,
                "author_id": p.author_id,
                "author_name": p.author.full_name if p.author else "Traveler",
                "body": p.body,
                "visibility": p.visibility,
                "created_at": p.created_at,
                "likes_count": likes or 0,
                "comments_count": comments or 0,
            })
        return results

    @staticmethod
    def create_comment(db: Session, author: MVPUser, post_id: int, req: CommentCreateRequest) -> MVPCommunityComment:
        post = db.query(MVPCommunityPost).filter(MVPCommunityPost.id == post_id, MVPCommunityPost.visibility == "visible").first()
        if not post:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found or unavailable.")
        now = datetime.now(timezone.utc)
        comment = MVPCommunityComment(
            post_id=post.id,
            author_id=author.id,
            body=req.body.strip(),
            visibility="visible",
            created_at=now,
            updated_at=now,
        )
        db.add(comment)
        db.commit()
        db.refresh(comment)
        return comment

    @staticmethod
    def toggle_like(db: Session, user: MVPUser, post_id: int) -> dict[str, Any]:
        post = db.query(MVPCommunityPost).filter(MVPCommunityPost.id == post_id).first()
        if not post:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found.")

        existing = db.query(MVPCommunityPostLike).filter(
            MVPCommunityPostLike.post_id == post_id,
            MVPCommunityPostLike.user_id == user.id
        ).first()

        if existing:
            db.delete(existing)
            db.commit()
            return {"post_id": post_id, "liked": False}
        else:
            now = datetime.now(timezone.utc)
            like = MVPCommunityPostLike(post_id=post_id, user_id=user.id, created_at=now)
            db.add(like)
            db.commit()
            return {"post_id": post_id, "liked": True}


# ============================================================================
# 7. Admin Service
# ============================================================================
class MVPAdminService:
    @staticmethod
    def decide_provider(db: Session, admin: MVPUser, provider_id: int, req: AdminProviderDecision) -> MVPProviderProfile:
        profile = db.query(MVPProviderProfile).filter(MVPProviderProfile.id == provider_id).first()
        if not profile:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider profile not found.")

        now = datetime.now(timezone.utc)
        profile.verification_status = req.verification_status
        profile.decided_by = admin.id
        profile.decided_at = now
        profile.decision_reason = req.decision_reason
        profile.updated_at = now

        # Audit log (single subject: subject_provider_id)
        audit = MVPAdminAuditLog(
            actor_id=admin.id,
            action="provider_decision",
            subject_user_id=None,
            subject_provider_id=profile.id,
            subject_listing_id=None,
            subject_post_id=None,
            reason=req.decision_reason or f"Provider set to {req.verification_status}",
            occurred_at=now,
        )
        db.add(audit)
        db.commit()
        db.refresh(profile)
        return profile

    @staticmethod
    def decide_listing(db: Session, admin: MVPUser, listing_id: int, req: AdminListingDecision) -> MVPListing:
        listing = db.query(MVPListing).filter(MVPListing.id == listing_id).first()
        if not listing:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found.")

        now = datetime.now(timezone.utc)
        listing.approval_status = req.approval_status
        listing.decided_by = admin.id
        listing.decided_at = now
        listing.decision_reason = req.decision_reason
        listing.updated_at = now
        if req.approval_status == "approved":
            listing.is_active = True
        else:
            listing.is_active = False

        # Audit log (single subject: subject_listing_id)
        audit = MVPAdminAuditLog(
            actor_id=admin.id,
            action="listing_decision",
            subject_user_id=None,
            subject_provider_id=None,
            subject_listing_id=listing.id,
            subject_post_id=None,
            reason=req.decision_reason or f"Listing set to {req.approval_status}",
            occurred_at=now,
        )
        db.add(audit)
        db.commit()
        db.refresh(listing)
        return listing

    @staticmethod
    def moderate_community_post(db: Session, admin: MVPUser, post_id: int, req: AdminModerationRequest) -> MVPCommunityPost:
        post = db.query(MVPCommunityPost).filter(MVPCommunityPost.id == post_id).first()
        if not post:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community post not found.")

        now = datetime.now(timezone.utc)
        post.visibility = req.visibility
        post.updated_at = now

        # Audit log (single subject: subject_post_id)
        audit = MVPAdminAuditLog(
            actor_id=admin.id,
            action="post_moderation",
            subject_user_id=None,
            subject_provider_id=None,
            subject_listing_id=None,
            subject_post_id=post.id,
            reason=req.reason,
            occurred_at=now,
        )
        db.add(audit)
        db.commit()
        db.refresh(post)
        return post


# ============================================================================
# 8. AI Itinerary Service
# ============================================================================
class MVPAIItineraryService:
    @staticmethod
    def generate_itinerary(req: ItineraryRequest) -> ItineraryResponse:
        dest = req.destination.strip().title()
        days = req.duration_days
        budget = req.budget_level

        cost_multiplier = {"budget": Decimal("3500.00"), "moderate": Decimal("8500.00"), "luxury": Decimal("22000.00")}.get(budget, Decimal("8500.00"))

        day_plans: list[ItineraryDayPlan] = []
        templates = [
            ("Arrival & Local Orientation", "Check in to approved local homestay; enjoy Ceylon tea", "Explore town center and local craft shops", "Traditional Sri Lankan rice and curry dinner", ["homestay", "guide"]),
            ("Nature & Scenic Exploration", "Early morning sunrise hike or nature walk", "Scenic cycle ride through countryside or tea estates", "Stargazing and relaxation by the garden", ["bike", "adventure"]),
            ("Heritage & Cultural Immersion", "Visit historic temple or colonial fortress", "Participate in local village cooking demonstration", "Evening cultural performance or lakeside sunset", ["guide", "homestay"]),
            ("Adventure & Active Discovery", "Whitewater rafting, zip-lining, or canyoning", "Picnic lunch by waterfall pool", "Casual dinner at eco-cafe", ["adventure", "bike"]),
            ("Wildlife & Coastal Relaxation", "Morning wildlife safari or coastal beach stroll", "Snorkeling, lagoon boat safari, or surfing lesson", "Fresh seafood beachside dinner", ["adventure", "guide"]),
        ]

        for d in range(1, days + 1):
            tmpl_idx = (d - 1) % len(templates)
            title, morning, afternoon, evening, categories = templates[tmpl_idx]
            day_cost = cost_multiplier + Decimal(d * 250)
            day_plans.append(
                ItineraryDayPlan(
                    day=d,
                    title=f"{dest}: {title}",
                    morning=f"Morning: {morning} in {dest}.",
                    afternoon=f"Afternoon: {afternoon}.",
                    evening=f"Evening: {evening}.",
                    recommended_categories=categories,
                    estimated_day_cost_lkr=day_cost,
                )
            )

        total_cost = sum(p.estimated_day_cost_lkr for p in day_plans)
        tips = [
            f"Always verify provider ratings and licensed guides for activities in {dest}.",
            "Carry cash in LKR for local roadside stalls and tuk-tuks.",
            "Stay hydrated and respect local dress codes at cultural sites.",
        ]

        return ItineraryResponse(
            destination=dest,
            duration_days=days,
            budget_level=budget,
            currency="LKR",
            total_estimated_cost_lkr=total_cost,
            days=day_plans,
            tips=tips,
            generated_at=datetime.now(timezone.utc),
        )
