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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.admin_roles import MVP_ADMIN_ROLES
from app.core.mvp_security import (
    create_mvp_access_token, hash_binary_token, hash_password,
    require_approved_provider, verify_password,
)
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
    InventoryUnitCreate,
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
)
from app.schemas.provider_auth import validate_provider_password_strength
from app.services.mvp_provider_otp import PASSWORD_UNSET
from app.services.tour_guide_sync import acquire_guide_booking_lock
from app.services import mvp_activity, mvp_guide, mvp_homestay, mvp_rental
from app.services import mvp_listing_verification as verification
from app.services import mvp_account as account
from app.services import mvp_wallet as wallet_service


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
    def login_user(db: Session, req: UserLoginRequest, *,
                   provider_only: bool = False) -> tuple[str, MVPUser]:
        normalized_email = req.email.strip().lower()
        user = db.query(MVPUser).filter(func.lower(MVPUser.email) == normalized_email).first()
        if (
            not user or (provider_only and user.role != "provider")
            or user.hashed_password == PASSWORD_UNSET
            or not verify_password(req.password, user.hashed_password)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )
        if user.role == "provider":
            require_approved_provider(db, user)
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

        is_new = user is None
        if user:
            if user.role != "traveler":
                raise HTTPException(403, "Google login is available for traveler accounts.")
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
            db.flush()

        if account.available(db):
            settings = account.settings(db, user.id, write=True)
            settings.email_verified = True
            if is_new:
                settings.google_only = True
            db.commit()
        elif is_new:
            db.commit()

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
        if not user or (
            user.role == "provider" and (
                not user.is_active or user.hashed_password == PASSWORD_UNSET
            )
        ):
            # Return demo/dummy token to prevent timing side-channels and account enumeration
            return secrets.token_urlsafe(32)

        user = account.lock_user(db, user.id)
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

        user = account.lock_user(db, reset.user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
        db.refresh(reset)
        if reset.used_at is not None:
            raise HTTPException(400, "Invalid, expired, or previously used password reset token.")

        if user.role == "provider":
            if not user.is_active or user.hashed_password == PASSWORD_UNSET:
                raise HTTPException(400, "Password reset is unavailable for this account.")
            try:
                validate_provider_password_strength(req.new_password)
            except ValueError as error:
                raise HTTPException(422, str(error)) from None

        db.query(MVPPasswordReset).filter(
            MVPPasswordReset.user_id == user.id, MVPPasswordReset.used_at.is_(None),
        ).update({MVPPasswordReset.used_at: now}, synchronize_session=False)
        user.hashed_password = hash_password(req.new_password)
        user.credential_version += 1
        user.auth_version += 1  # Invalidate existing sessions
        user.updated_at = now
        if user.role == "traveler" and account.available(db):
            db.flush()
            account.settings(db, user.id, write=True).google_only = False
            account.notify(db, user.id, "account", "password_reset", user.id,
                           f"password:{user.id}:{user.credential_version}")
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

# ============================================================================
# 3. Listing & Inventory Service
# ============================================================================
class MVPListingService:
    @staticmethod
    def create_listing(db: Session, user: MVPUser, req: ListingCreateRequest) -> MVPListing:
        profile = db.query(MVPProviderProfile).filter(MVPProviderProfile.user_id == user.id).first()
        if user.role != "provider" or not profile or profile.verification_status != "approved":
            raise HTTPException(403, "Only approved providers can create listings")
        if req.category == "artisan":
            verification.require_schema(db)

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
            approval_status="draft",
            is_active=False,  # Active requires approved
            created_at=now,
            updated_at=now,
        )
        db.add(listing)
        db.commit()
        db.refresh(listing)
        return listing

    @staticmethod
    def add_media(db: Session, user: MVPUser, listing_id: int, req: ListingMediaCreate) -> MVPListingMedia:
        listing = verification.owned(db, user, listing_id, lock=True)
        verification.require_editable(listing)
        if listing.category == "adventure" or req.storage_key.startswith(verification.PHOTO_PREFIX):
            raise HTTPException(422, "Hosted photos must use the multipart upload endpoint")
        now = datetime.now(timezone.utc)
        media = MVPListingMedia(
            listing_id=listing.id,
            storage_key=req.storage_key,
            sort_order=req.sort_order,
            alt_text=req.alt_text,
            created_at=now,
        )
        db.add(media)
        verification.invalidate_review(listing)
        verification.commit(db)
        db.refresh(media)
        return media

    @staticmethod
    def add_inventory_unit(db: Session, user: MVPUser, listing_id: int, req: InventoryUnitCreate) -> MVPInventoryUnit:
        listing = verification.owned(db, user, listing_id, lock=True)
        verification.require_editable(listing)
        if listing.category == "adventure":
            raise HTTPException(422, "Configure capacity through the activity details")
        if listing.category == "guide":
            raise HTTPException(422, "Manage guide inventory through the packages endpoint")
        if listing.category == "artisan":
            raise HTTPException(422, "Manage artisan products through the products endpoint")
        if listing.category in mvp_rental.RENTAL_CATEGORIES and (
            req.capacity != 1 or len(listing.units) >= 100
        ):
            raise HTTPException(422, "Add each vehicle individually, up to 100 vehicles")
        if not req.label.strip() or (listing.category == "homestay" and req.capacity > 50):
            raise HTTPException(422, "Invalid room label or capacity")
        unit = MVPInventoryUnit(
            listing_id=listing.id,
            provider_id=listing.provider_id,
            label=req.label.strip(),
            capacity=req.capacity,
            is_active=True,
        )
        db.add(unit)
        verification.commit(db)
        db.refresh(unit)
        return unit

    @staticmethod
    def add_availability_slot(
        db: Session, listing_id: int, req: AvailabilitySlotCreate, actor: MVPUser | None = None,
    ) -> MVPAvailabilitySlot:
        listing = verification.owned(db, actor, listing_id, lock=True)
        verification.require_editable(listing)
        if listing.category == "artisan":
            raise HTTPException(422, "Artisan showcase listings do not accept bookings")
        unit = db.query(MVPInventoryUnit).filter(MVPInventoryUnit.id == req.unit_id, MVPInventoryUnit.listing_id == listing_id).first()
        if not unit:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inventory unit not found for this listing.")

        starts_at, ends_at = req.starts_at, req.ends_at
        if listing.category == "adventure":
            starts_at, ends_at = map(mvp_activity.slot_time, (starts_at, ends_at))
            mvp_activity.validate_calendar(db, listing, unit, req, starts_at, ends_at)
        if listing.category == "guide":
            verification.require_schema(db)
            starts_at, ends_at = map(mvp_activity.slot_time, (starts_at, ends_at))
            mvp_guide.validate_calendar(db, unit, req, starts_at, ends_at)
        if listing.category in mvp_rental.RENTAL_CATEGORIES:
            starts_at, ends_at = map(mvp_activity.slot_time, (starts_at, ends_at))
            mvp_rental.validate_calendar(db, unit, req, starts_at, ends_at)
        if listing.category == "homestay":
            if req.capacity > unit.capacity or not unit.is_active:
                raise HTTPException(422, "Slot capacity must fit an active room")
            starts_at, ends_at = map(mvp_homestay.utc_naive, (starts_at, ends_at))
            if not req.is_open and db.query(MVPBooking.id).join(MVPBookingAllocation).join(
                MVPAvailabilitySlot, MVPAvailabilitySlot.id == MVPBookingAllocation.slot_id
            ).filter(
                MVPAvailabilitySlot.unit_id == unit.id,
                MVPBookingAllocation.released_at.is_(None),
                MVPBooking.status.in_(("pending", "confirmed", "completed")),
                MVPBooking.starts_at < ends_at, MVPBooking.ends_at > starts_at,
            ).first():
                raise HTTPException(409, "Room already has a booking during these dates")
        if ends_at <= starts_at:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Slot ends_at must be strictly greater than starts_at.")

        slot = MVPAvailabilitySlot(
            unit_id=unit.id,
            listing_id=listing.id,
            starts_at=starts_at,
            ends_at=ends_at,
            capacity=req.capacity,
            is_open=req.is_open,
        )
        db.add(slot)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Availability conflicts with an existing room slot") from None
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
        q = db.query(MVPListing).filter(
            MVPListing.is_active == True, MVPListing.approval_status == "approved",
            MVPListing.provider.has(
                (MVPProviderProfile.verification_status == "approved")
                & MVPProviderProfile.user.has(
                    (MVPUser.role == "provider") & (MVPUser.is_active == True)
                ),
            ),
        )
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
        if db.get_bind().dialect.name == "mssql":
            # Serialize assignments across all guide listings belonging to one provider.
            provider_id = db.query(MVPListing.provider_id).filter_by(
                id=req.listing_id, category="guide",
            ).scalar()
            if provider_id is not None:
                acquire_guide_booking_lock(db, f"mvp_tour_guide_provider_{provider_id}")
        listing = verification.locked_listing(db, req.listing_id)
        if listing.category == "artisan":
            raise HTTPException(422, "Artisan showcase listings do not accept bookings")
        if listing.category == "homestay":
            return mvp_homestay.book(
                db, traveler, listing.id, req.starts_at, req.ends_at, req.quantity, req.request_key,
            )
        adventure = listing.category == "adventure"
        guide = listing.category == "guide"
        rental = listing.category in mvp_rental.RENTAL_CATEGORIES
        scheduled = adventure or guide or rental
        if rental and req.slot_id is not None:
            raise HTTPException(422, "Rental inventory is selected by the server")
        if scheduled:
            previous = db.query(MVPBooking).filter_by(
                traveler_id=traveler.id, request_key=req.request_key,
            ).first()
            if previous:
                if (previous.listing_id == listing.id and previous.quantity == req.quantity
                        and previous.slot_id == req.slot_id
                        and previous.total_amount == req.expected_total
                        and previous.starts_at == mvp_activity.slot_time(req.starts_at)
                        and previous.ends_at == mvp_activity.slot_time(req.ends_at)):
                    return previous
                raise HTTPException(409, "Request key was used for a different booking")
        if not listing.is_active or listing.approval_status != "approved" or not verification.provider_eligible(listing):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Listing is not available for booking.")

        if not scheduled and req.ends_at <= req.starts_at:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Booking ends_at must be later than starts_at.")

        # Find matching open slots for this listing that overlap the requested range
        total_amount = listing.price * Decimal(req.quantity)
        booking_start, booking_end = req.starts_at, req.ends_at
        if rental:
            slots, total_amount, booking_start, booking_end = mvp_rental.booking_selection(
                db, listing, req,
            )
        elif guide:
            slot, total_amount = mvp_guide.booking_selection(db, listing, req)
            slots = [slot]
            booking_start, booking_end = slot.starts_at, slot.ends_at
        elif adventure:
            slot = mvp_activity.booking_slot(db, listing, req)
            slots = [slot]
            booking_start, booking_end = slot.starts_at, slot.ends_at
        else:
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
        if adventure and db.query(MVPBooking.id).join(MVPBookingAllocation).filter(
            MVPBooking.traveler_id == traveler.id,
            MVPBookingAllocation.slot_id == slots[0].id,
            MVPBookingAllocation.released_at.is_(None),
        ).first():
            raise HTTPException(409, "You already have a reservation for this activity slot")

        # Capacity check across all overlapping slots
        if not slots:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No available availability slots for the requested dates.")

        for slot in ([] if scheduled else slots):
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
        request_hash = hashlib.sha256(f"{traveler.id}:{req.request_key}".encode("utf-8")).digest()

        booking = MVPBooking(
            listing_id=listing.id,
            traveler_id=traveler.id,
            request_key=req.request_key,
            request_hash=request_hash,
            starts_at=booking_start,
            ends_at=booking_end,
            quantity=req.quantity,
            total_amount=total_amount,
            currency="LKR",
            status="pending",
            created_at=now,
            updated_at=now,
        )
        db.add(booking)
        try:
            db.flush()
        except IntegrityError:
            if not scheduled:
                raise
            db.rollback()
            raise HTTPException(409, "Booking request conflicts with an existing booking") from None

        # Create allocations for each slot
        for slot in slots:
            alloc = MVPBookingAllocation(
                booking_id=booking.id,
                slot_id=slot.id,
                listing_id=listing.id,
                quantity=1 if rental else req.quantity,
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
        account.booking_event(db, booking)
        db.commit()
        db.refresh(booking)
        return booking

    @staticmethod
    def update_booking_status(db: Session, actor: MVPUser, booking_id: int, req: BookingStatusUpdateRequest) -> MVPBooking:
        locked_listing_id = db.query(MVPBooking.listing_id).join(MVPListing).filter(
            MVPBooking.id == booking_id,
            MVPListing.category.in_(wallet_service.BOOKABLE),
        ).scalar()
        if locked_listing_id is not None:
            verification.locked_listing(db, locked_listing_id)
        booking = _locked_booking(db, booking_id)
        if not booking:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found.")

        current = booking.status
        target = req.to_status
        owns_listing = (
            actor.role == "provider" and actor.provider_profile
            and actor.provider_profile.id == booking.listing.provider_id
        )
        rental = booking.listing.category in mvp_rental.RENTAL_CATEGORIES
        scheduled = rental or booking.listing.category in {"adventure", "guide"}
        if scheduled and target in {"confirmed", "rejected", "completed"} and not owns_listing:
            raise HTTPException(403, "Only the owning provider can decide this booking")
        if not (
            owns_listing or actor.role in MVP_ADMIN_ROLES or
            (actor.id == booking.traveler_id and target == "cancelled")
        ):
            raise HTTPException(403, "Only the host can decide this booking")
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

        wallet_event = None
        if booking.listing.category in wallet_service.BOOKABLE:
            if target == "confirmed":
                profile = wallet_service.lock_profile(db, booking.listing.provider_id)
                wallet_service.require_eligible(profile, actor)
                validate_booking_confirmation(db, booking)
                provider_wallet = wallet_service.initialize(db, profile.id)
                wallet_event = wallet_service.charge(db, booking, provider_wallet)
            elif current == "confirmed" and target == "cancelled":
                wallet_event = wallet_service.refund(db, booking)

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
        account.booking_event(db, booking, actor)
        db.commit()
        db.refresh(booking)
        booking.wallet_event = wallet_event
        return booking


def validate_booking_confirmation(db: Session, booking: MVPBooking, *, lock=True):
    if not any(payment.status in {"unpaid", "demo_succeeded"} for payment in booking.payments):
        raise HTTPException(409, "Traveler must select a payment method first")
    listing = booking.listing
    if listing.category == "homestay":
        mvp_homestay.validate_confirmation(db, booking, lock=lock)
        return
    if (not listing.is_active or listing.approval_status != "approved"
            or not verification.provider_eligible(listing)
            or booking.starts_at <= verification.now()):
        raise HTTPException(409, "The reservation cannot be confirmed")
    if listing.category in mvp_rental.RENTAL_CATEGORIES:
        mvp_rental.validate_confirmation(booking)
    elif (booking.slot_id is None or not booking.allocations[0].slot.is_open
          or booking.allocations[0].released_at is not None):
        raise HTTPException(409, "The reservation cannot be confirmed")


# ============================================================================
# 5. Payment Service
# ============================================================================
def _locked_booking(db: Session, booking_id: int) -> MVPBooking | None:
    if db.get_bind().dialect.name == "sqlite":
        db.query(MVPBooking).filter(MVPBooking.id == booking_id).update(
            {MVPBooking.updated_at: MVPBooking.updated_at}, synchronize_session=False,
        )
    return db.query(MVPBooking).with_hint(
        MVPBooking, "WITH (UPDLOCK, HOLDLOCK)", dialect_name="mssql"
    ).filter(MVPBooking.id == booking_id).populate_existing().first()


def _provider_confirmed_payment(db, booking, req, method, payment_status):
    attempt_no = getattr(req, "attempt_no", None)
    previous = sorted(booking.payments, key=lambda item: item.attempt_no)
    for payment in previous:
        if payment.request_key == req.request_key:
            if (payment.method != method or payment.status != payment_status
                    or (attempt_no is not None and payment.attempt_no != attempt_no)):
                raise HTTPException(409, "Request key is already used for a different payment")
            return payment
    if booking.status != "pending":
        raise HTTPException(409, "Payment can only be selected for a pending booking")
    if any(payment.status in {"unpaid", "demo_succeeded"} for payment in previous):
        raise HTTPException(409, "A payment method has already been selected")
    next_attempt = previous[-1].attempt_no + 1 if previous else 1
    if attempt_no is not None and attempt_no != next_attempt:
        raise HTTPException(409, f"Expected payment attempt number {next_attempt}")
    now = datetime.now(timezone.utc)
    payment = MVPBookingPayment(
        booking_id=booking.id, request_key=req.request_key, attempt_no=next_attempt,
        request_hash=hashlib.sha256(
            f"{booking.id}:{req.request_key}:{next_attempt}".encode()
        ).digest(),
        method=method, status=payment_status, display_amount=booking.total_amount,
        currency=booking.currency, created_at=now,
        resolved_at=now if method == "simulated" else None,
    )
    db.add(payment)
    account.payment_event(db, booking, payment)
    db.commit()
    db.refresh(payment)
    return payment


class MVPPaymentService:
    @staticmethod
    def process_simulated(db: Session, req: SimulatedPaymentRequest, traveler: MVPUser) -> MVPBookingPayment:
        booking = _locked_booking(db, req.booking_id)
        if not booking:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found.")
        if booking.traveler_id != traveler.id:
            raise HTTPException(403, "Only the booking traveler can select payment")
        if booking.listing.category in {"homestay", "adventure", "guide", "bike", "bicycle"}:
            if "simulate_outcome" not in req.model_fields_set:
                raise HTTPException(422, "Specify the simulated payment outcome")
            outcome = "demo_succeeded" if req.simulate_outcome == "success" else "demo_failed"
            return _provider_confirmed_payment(db, booking, req, "simulated", outcome)

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

        account.payment_event(db, booking, payment)
        db.commit()
        db.refresh(payment)
        return payment

    @staticmethod
    def process_at_location(db: Session, req: AtLocationPaymentRequest, traveler: MVPUser) -> MVPBookingPayment:
        booking = _locked_booking(db, req.booking_id)
        if not booking:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found.")
        if booking.traveler_id != traveler.id:
            raise HTTPException(403, "Only the booking traveler can select payment")
        if booking.listing.category in {"homestay", "adventure", "guide", "bike", "bicycle"}:
            return _provider_confirmed_payment(db, booking, req, "at_location", "unpaid")

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

        account.payment_event(db, booking, payment)
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
        profile = wallet_service.lock_profile(db, provider_id)
        if not profile:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider profile not found.")
        if profile.verification_status == "draft":
            raise HTTPException(409, "The provider must submit the application before review.")

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
        if (profile.verification_status == "approved" and profile.user.is_active
                and profile.user.role == "provider"):
            db.flush()
            wallet_service.initialize(db, profile.id)
        db.commit()
        db.refresh(profile)
        return profile

    @staticmethod
    def decide_listing(db: Session, admin: MVPUser, listing_id: int, req: AdminListingDecision) -> MVPListing:
        if admin.role not in MVP_ADMIN_ROLES:
            raise HTTPException(403, "Administrator required")
        verification.require_schema(db)
        listing = verification.locked_listing(db, listing_id)
        decision = req.approval_status
        allowed = {
            "pending": {"approved", "rejected", "more_evidence_required"},
            "approved": {"suspended"},
            "suspended": {"approved", "more_evidence_required"},
        }
        if decision not in allowed.get(listing.approval_status, set()):
            raise HTTPException(409, "Invalid listing verification transition")
        reason = (req.decision_reason or "").strip()
        if decision != "approved" and not reason:
            raise HTTPException(422, "Enter a reason or evidence instructions")
        if decision == "approved":
            if not verification.provider_eligible(listing):
                raise HTTPException(409, "Listing provider is no longer eligible")
            verification.validate_submission(db, listing)

        now = datetime.now(timezone.utc)
        listing.approval_status = "evidence" if decision == "more_evidence_required" else decision
        listing.decided_by = admin.id
        listing.decided_at = now
        listing.decision_reason = reason or None
        listing.updated_at = now
        listing.is_active = (
            decision == "approved" and listing.category not in {"adventure", "bike", "bicycle"}
        )

        # Audit log (single subject: subject_listing_id)
        audit = MVPAdminAuditLog(
            actor_id=admin.id,
            action="listing_decision",
            subject_user_id=None,
            subject_provider_id=None,
            subject_listing_id=listing.id,
            subject_post_id=None,
            reason=f"{decision}: {reason or 'Listing approved'}"[:500],
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
