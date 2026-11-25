"""
CeylonGo Academic MVP — Unified SQLAlchemy 2.0 Models
Maps the 17 Application Tables + 1 Schema Ledger
Reference: FRESH_DB_02_MVP_SCHEMA_DESIGN.md
"""

from datetime import date, datetime
from decimal import Decimal
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    FetchedValue,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Unicode,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class MVPBase(DeclarativeBase):
    pass


PK_TYPE = BigInteger().with_variant(Integer, "sqlite")


# ============================================================================
# 1. users
# ============================================================================
class MVPUser(MVPBase):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint("role IN ('traveler', 'provider', 'admin')", name="ck_users_role"),
        CheckConstraint("auth_version >= 0", name="ck_users_auth_version"),
        CheckConstraint("credential_version >= 0", name="ck_users_credential_version"),
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Unicode(254), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(Unicode(120), nullable=False)
    phone: Mapped[str | None] = mapped_column(Unicode(32), nullable=True)
    hashed_password: Mapped[str] = mapped_column(Unicode(512), nullable=False)
    role: Mapped[str] = mapped_column(String(12), nullable=False)  # traveler, provider, admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"), nullable=False)
    auth_version: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    credential_version: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    provider_profile: Mapped["MVPProviderProfile | None"] = relationship(
        "MVPProviderProfile", back_populates="user", foreign_keys="MVPProviderProfile.user_id", uselist=False
    )
    bookings: Mapped[list["MVPBooking"]] = relationship("MVPBooking", back_populates="traveler")
    community_posts: Mapped[list["MVPCommunityPost"]] = relationship("MVPCommunityPost", back_populates="author")


# ============================================================================
# 2. password_resets
# ============================================================================
class MVPPasswordReset(MVPBase):
    __tablename__ = "password_resets"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_password_resets_token_hash"),
        CheckConstraint("expires_at > created_at", name="ck_password_resets_expires"),
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped["MVPUser"] = relationship("MVPUser")


# ============================================================================
# 3. provider_profiles
# ============================================================================
class MVPProviderProfile(MVPBase):
    __tablename__ = "provider_profiles"
    __mapper_args__ = {"eager_defaults": False}
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_provider_profiles_user_id"),
        CheckConstraint(
            "verification_status IN ('draft', 'pending', 'approved', 'rejected', 'suspended')",
            name="ck_provider_profiles_verification_status",
        ),
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    business_name: Mapped[str] = mapped_column(Unicode(160), nullable=False)
    phone: Mapped[str] = mapped_column(Unicode(32), nullable=False)
    district: Mapped[str] = mapped_column(Unicode(80), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(12), nullable=False)  # draft, pending, approved, rejected, suspended
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    registration_details: Mapped[str | None] = mapped_column(
        Unicode(), deferred=True, server_default=FetchedValue(), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user: Mapped["MVPUser"] = relationship("MVPUser", foreign_keys=[user_id], back_populates="provider_profile")
    decided_by_user: Mapped["MVPUser | None"] = relationship("MVPUser", foreign_keys=[decided_by])
    listings: Mapped[list["MVPListing"]] = relationship("MVPListing", back_populates="provider")
    documents: Mapped[list["MVPProviderDocument"]] = relationship("MVPProviderDocument", back_populates="provider")


# ============================================================================
# 4. listings
# ============================================================================
class MVPListing(MVPBase):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("id", "provider_id", name="uq_listings_id_provider"),
        CheckConstraint("category IN ('homestay', 'bike', 'bicycle', 'adventure', 'guide')", name="ck_listings_category"),
        CheckConstraint("approval_status IN ('draft', 'pending', 'approved', 'rejected')", name="ck_listings_approval_status"),
        CheckConstraint("is_active = 0 OR approval_status = 'approved'", name="ck_listings_active_requires_approved"),
        CheckConstraint("price >= 0", name="ck_listings_price_nonnegative"),
        CheckConstraint("currency = 'LKR'", name="ck_listings_currency"),
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("provider_profiles.id"), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # homestay, bike, bicycle, adventure, guide
    title: Mapped[str] = mapped_column(Unicode(160), nullable=False)
    description: Mapped[str] = mapped_column(Unicode(2000), nullable=False)
    district: Mapped[str] = mapped_column(Unicode(80), nullable=False, index=True)
    locality: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="LKR", server_default=text("'LKR'"), nullable=False)
    approval_status: Mapped[str] = mapped_column(String(12), nullable=False, index=True)  # draft, pending, approved, rejected
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"), nullable=False, index=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    provider: Mapped["MVPProviderProfile"] = relationship("MVPProviderProfile", back_populates="listings")
    media: Mapped[list["MVPListingMedia"]] = relationship("MVPListingMedia", back_populates="listing")
    units: Mapped[list["MVPInventoryUnit"]] = relationship("MVPInventoryUnit", back_populates="listing")
    bookings: Mapped[list["MVPBooking"]] = relationship("MVPBooking", back_populates="listing")


# ============================================================================
# 5. listing_media
# ============================================================================
class MVPListingMedia(MVPBase):
    __tablename__ = "listing_media"
    __table_args__ = (
        UniqueConstraint("storage_key", name="uq_listing_media_storage_key"),
        UniqueConstraint("listing_id", "sort_order", name="uq_listing_media_listing_sort"),
        CheckConstraint("sort_order >= 0", name="ck_listing_media_sort_order"),
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("listings.id"), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(Unicode(450), nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    alt_text: Mapped[str | None] = mapped_column(Unicode(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    listing: Mapped["MVPListing"] = relationship("MVPListing", back_populates="media")


# ============================================================================
# 6. inventory_units
# ============================================================================
class MVPInventoryUnit(MVPBase):
    __tablename__ = "inventory_units"
    __table_args__ = (
        UniqueConstraint("listing_id", "label", name="uq_inventory_units_listing_label"),
        UniqueConstraint("id", "listing_id", name="uq_inventory_units_id_listing"),
        UniqueConstraint("id", "provider_id", name="uq_inventory_units_id_provider"),
        CheckConstraint("capacity > 0", name="ck_inventory_units_capacity"),
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("listings.id"), nullable=False, index=True)
    provider_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    label: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"), nullable=False)

    listing: Mapped["MVPListing"] = relationship("MVPListing", back_populates="units")
    slots: Mapped[list["MVPAvailabilitySlot"]] = relationship("MVPAvailabilitySlot", back_populates="unit")


# ============================================================================
# 7. provider_documents
# ============================================================================
class MVPProviderDocument(MVPBase):
    __tablename__ = "provider_documents"
    __table_args__ = (
        UniqueConstraint("storage_key", name="uq_provider_documents_storage_key"),
        CheckConstraint("kind IN ('identity', 'business', 'revenue_license', 'insurance')", name="ck_provider_documents_kind"),
        CheckConstraint("status IN ('pending', 'verified', 'rejected')", name="ck_provider_documents_status"),
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("provider_profiles.id"), nullable=False, index=True)
    unit_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # identity, business, revenue_license, insurance
    storage_key: Mapped[str] = mapped_column(Unicode(450), nullable=False)
    sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)  # pending, verified, rejected
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    provider: Mapped["MVPProviderProfile"] = relationship("MVPProviderProfile", back_populates="documents")


# ============================================================================
# 8. provider_email_challenges
# ============================================================================
class MVPProviderEmailChallenge(MVPBase):
    __tablename__ = "provider_email_challenges"
    __table_args__ = (
        CheckConstraint("attempts BETWEEN 0 AND 10", name="ck_challenges_attempts"),
        CheckConstraint("expires_at > created_at", name="ck_challenges_expires"),
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Unicode(254), nullable=False, index=True)
    challenge_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(SmallInteger, default=0, server_default=text("0"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


# ============================================================================
# 9. availability_slots
# ============================================================================
class MVPAvailabilitySlot(MVPBase):
    __tablename__ = "availability_slots"
    __table_args__ = (
        UniqueConstraint("unit_id", "starts_at", name="uq_availability_slots_unit_starts"),
        UniqueConstraint("id", "listing_id", name="uq_availability_slots_id_listing"),
        CheckConstraint("ends_at > starts_at", name="ck_availability_slots_dates"),
        CheckConstraint("capacity > 0", name="ck_availability_slots_capacity"),
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    unit_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("inventory_units.id"), nullable=False, index=True)
    listing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("listings.id"), nullable=False, index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"), nullable=False)

    unit: Mapped["MVPInventoryUnit"] = relationship("MVPInventoryUnit", back_populates="slots")
    allocations: Mapped[list["MVPBookingAllocation"]] = relationship("MVPBookingAllocation", back_populates="slot")


# ============================================================================
# 10. bookings
# ============================================================================
class MVPBooking(MVPBase):
    __tablename__ = "bookings"
    __table_args__ = (
        UniqueConstraint("traveler_id", "request_key", name="uq_bookings_traveler_request"),
        UniqueConstraint("id", "listing_id", name="uq_bookings_id_listing"),
        CheckConstraint("ends_at > starts_at", name="ck_bookings_dates"),
        CheckConstraint("quantity > 0", name="ck_bookings_quantity"),
        CheckConstraint("total_amount >= 0", name="ck_bookings_amount"),
        CheckConstraint("currency = 'LKR'", name="ck_bookings_currency"),
        CheckConstraint("status IN ('pending', 'confirmed', 'completed', 'rejected', 'cancelled')", name="ck_bookings_status"),
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("listings.id"), nullable=False, index=True)
    traveler_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    request_key: Mapped[str] = mapped_column(String(36), nullable=False)
    request_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="LKR", server_default=text("'LKR'"), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, index=True)  # pending, confirmed, completed, rejected, cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    listing: Mapped["MVPListing"] = relationship("MVPListing", back_populates="bookings")
    traveler: Mapped["MVPUser"] = relationship("MVPUser", back_populates="bookings")
    allocations: Mapped[list["MVPBookingAllocation"]] = relationship("MVPBookingAllocation", back_populates="booking")
    status_history: Mapped[list["MVPBookingStatusHistory"]] = relationship("MVPBookingStatusHistory", back_populates="booking")
    payments: Mapped[list["MVPBookingPayment"]] = relationship("MVPBookingPayment", back_populates="booking")


# ============================================================================
# 11. booking_allocations
# ============================================================================
class MVPBookingAllocation(MVPBase):
    __tablename__ = "booking_allocations"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_booking_allocations_quantity"),
    )

    booking_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("bookings.id"), primary_key=True)
    slot_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("availability_slots.id"), primary_key=True)
    listing_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    booking: Mapped["MVPBooking"] = relationship("MVPBooking", back_populates="allocations")
    slot: Mapped["MVPAvailabilitySlot"] = relationship("MVPAvailabilitySlot", back_populates="allocations")


# ============================================================================
# 12. booking_status_history
# ============================================================================
class MVPBookingStatusHistory(MVPBase):
    __tablename__ = "booking_status_history"
    __table_args__ = ()

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("bookings.id"), nullable=False, index=True)
    actor_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(12), nullable=True)
    to_status: Mapped[str] = mapped_column(String(12), nullable=False)
    reason: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    booking: Mapped["MVPBooking"] = relationship("MVPBooking", back_populates="status_history")
    actor: Mapped["MVPUser"] = relationship("MVPUser")


# ============================================================================
# 13. booking_payments
# ============================================================================
class MVPBookingPayment(MVPBase):
    __tablename__ = "booking_payments"
    __table_args__ = (
        UniqueConstraint("booking_id", "request_key", name="uq_booking_payments_request_key"),
        UniqueConstraint("booking_id", "attempt_no", name="uq_booking_payments_attempt"),
        CheckConstraint("attempt_no > 0", name="ck_booking_payments_attempt"),
        CheckConstraint("display_amount >= 0", name="ck_booking_payments_amount"),
        CheckConstraint("currency = 'LKR'", name="ck_booking_payments_currency"),
        CheckConstraint(
            "((method = 'simulated' AND status IN ('demo_pending', 'demo_failed', 'demo_succeeded')) OR "
            "(method = 'at_location' AND status = 'unpaid'))",
            name="ck_booking_payments_method_status",
        ),
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("bookings.id"), nullable=False, index=True)
    request_key: Mapped[str] = mapped_column(String(36), nullable=False)
    request_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)  # simulated, at_location
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # demo_pending, demo_failed, demo_succeeded, unpaid
    display_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="LKR", server_default=text("'LKR'"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    booking: Mapped["MVPBooking"] = relationship("MVPBooking", back_populates="payments")


# ============================================================================
# 14. community_posts
# ============================================================================
class MVPCommunityPost(MVPBase):
    __tablename__ = "community_posts"
    __table_args__ = (
        CheckConstraint("visibility IN ('visible', 'hidden', 'deleted')", name="ck_community_posts_visibility"),
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    author_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    body: Mapped[str] = mapped_column(Unicode(2000), nullable=False)
    visibility: Mapped[str] = mapped_column(String(10), default="visible", nullable=False, index=True)  # visible, hidden, deleted
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    author: Mapped["MVPUser"] = relationship("MVPUser", back_populates="community_posts")
    comments: Mapped[list["MVPCommunityComment"]] = relationship("MVPCommunityComment", back_populates="post")
    likes: Mapped[list["MVPCommunityPostLike"]] = relationship("MVPCommunityPostLike", back_populates="post")


# ============================================================================
# 15. community_comments
# ============================================================================
class MVPCommunityComment(MVPBase):
    __tablename__ = "community_comments"
    __table_args__ = (
        CheckConstraint("visibility IN ('visible', 'hidden', 'deleted')", name="ck_community_comments_visibility"),
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("community_posts.id"), nullable=False, index=True)
    author_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    body: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    visibility: Mapped[str] = mapped_column(String(10), default="visible", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    post: Mapped["MVPCommunityPost"] = relationship("MVPCommunityPost", back_populates="comments")
    author: Mapped["MVPUser"] = relationship("MVPUser")


# ============================================================================
# 16. community_post_likes
# ============================================================================
class MVPCommunityPostLike(MVPBase):
    __tablename__ = "community_post_likes"

    post_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("community_posts.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    post: Mapped["MVPCommunityPost"] = relationship("MVPCommunityPost", back_populates="likes")
    user: Mapped["MVPUser"] = relationship("MVPUser")


# ============================================================================
# 17. admin_audit_logs
# ============================================================================
class MVPAdminAuditLog(MVPBase):
    __tablename__ = "admin_audit_logs"
    __table_args__ = (
        CheckConstraint(
            "action IN ('user_status', 'provider_decision', 'listing_decision', 'post_moderation')",
            name="ck_admin_audit_logs_action",
        ),
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    actor_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    subject_provider_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("provider_profiles.id"), nullable=True)
    subject_listing_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("listings.id"), nullable=True)
    subject_post_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("community_posts.id"), nullable=True)
    reason: Mapped[str] = mapped_column(Unicode(500), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    actor: Mapped["MVPUser"] = relationship("MVPUser", foreign_keys=[actor_id])


# ============================================================================
# 18. mvp_schema_versions
# ============================================================================
class MVPSchemaVersion(MVPBase):
    __tablename__ = "mvp_schema_versions"
    __table_args__ = (
        UniqueConstraint("artifact_name", name="uq_mvp_schema_versions_artifact"),
    )

    version: Mapped[str] = mapped_column(String(20), primary_key=True)
    artifact_name: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    applied_by: Mapped[str] = mapped_column(Unicode(128), nullable=False)
