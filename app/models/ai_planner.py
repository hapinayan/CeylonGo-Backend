from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Time,
    Unicode,
    UnicodeText,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.models.user import Base, User


class AIChatSession(Base):
    __tablename__ = "ai_chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_title: Mapped[str] = mapped_column(Unicode(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User")


class AIChatMessage(Base):
    __tablename__ = "ai_chat_messages"

    __table_args__ = (
        CheckConstraint("sender_role IN ('user', 'assistant', 'system')", name="CK_ai_chat_messages_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ai_chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_role: Mapped[str] = mapped_column(Unicode(20), nullable=False)
    message_content: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    structured_payload: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    session: Mapped["AIChatSession"] = relationship(
        "AIChatSession",
        backref=backref("messages", cascade="all, delete-orphan", lazy="select"),
    )


class TravelPreference(Base):
    __tablename__ = "travel_preferences"

    __table_args__ = (
        UniqueConstraint("user_id", name="UQ_travel_preferences_user"),
        CheckConstraint("preferred_budget_level IN ('budget', 'medium', 'luxury')", name="CK_travel_pref_budget"),
        CheckConstraint("pace IN ('relaxed', 'moderate', 'fast_paced')", name="CK_travel_pref_pace"),
        CheckConstraint("travel_party_type IN ('solo', 'couple', 'family', 'group')", name="CK_travel_pref_party"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    preferred_budget_level: Mapped[str] = mapped_column(Unicode(30), default="medium", nullable=False)
    pace: Mapped[str] = mapped_column(Unicode(30), default="moderate", nullable=False)
    dietary_preferences: Mapped[str | None] = mapped_column(Unicode(255), nullable=True)
    travel_party_type: Mapped[str] = mapped_column(Unicode(50), default="solo", nullable=False)
    interests: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    accessibility_needs: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User")


class TravelPlan(Base):
    __tablename__ = "travel_plans"

    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="CK_travel_plans_dates"),
        CheckConstraint("total_days > 0", name="CK_travel_plans_days"),
        CheckConstraint("estimated_budget IS NULL OR estimated_budget >= 0", name="CK_travel_plans_budget"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    ai_session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ai_chat_sessions.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(Unicode(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_days: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    share_token: Mapped[str | None] = mapped_column(Unicode(100), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User")
    session: Mapped["AIChatSession | None"] = relationship("AIChatSession")


class TravelPlanDay(Base):
    __tablename__ = "travel_plan_days"

    __table_args__ = (
        UniqueConstraint("travel_plan_id", "day_number", name="UQ_travel_plan_days"),
        CheckConstraint("day_number > 0", name="CK_travel_plan_days_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    travel_plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("travel_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_number: Mapped[int] = mapped_column(Integer, nullable=False)
    date_value: Mapped[date] = mapped_column(Date, nullable=False)
    day_title: Mapped[str] = mapped_column(Unicode(200), nullable=False)
    day_summary: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    overnight_destination: Mapped[str | None] = mapped_column(Unicode(150), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    travel_plan: Mapped["TravelPlan"] = relationship(
        "TravelPlan",
        backref=backref("days", cascade="all, delete-orphan", lazy="select"),
    )


class TravelPlanItem(Base):
    __tablename__ = "travel_plan_items"

    __table_args__ = (
        CheckConstraint(
            "item_type IN ('attraction', 'activity', 'homestay', 'restaurant', 'transit', 'guide')",
            name="CK_travel_plan_items_type",
        ),
        CheckConstraint("item_order >= 0", name="CK_travel_plan_items_order"),
        CheckConstraint("estimated_cost IS NULL OR estimated_cost >= 0", name="CK_travel_plan_items_cost"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_day_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("travel_plan_days.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_order: Mapped[int] = mapped_column(Integer, nullable=False)
    item_type: Mapped[str] = mapped_column(Unicode(30), nullable=False)
    title: Mapped[str] = mapped_column(Unicode(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    destination_name: Mapped[str | None] = mapped_column(Unicode(150), nullable=True)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    listing_reference_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("listing_references.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    plan_day: Mapped["TravelPlanDay"] = relationship(
        "TravelPlanDay",
        backref=backref("items", cascade="all, delete-orphan", lazy="select"),
    )


class AIPendingAction(Base):
    __tablename__ = "ai_pending_actions"

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'executed', 'expired')",
            name="CK_ai_pending_actions_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ai_chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    action_type: Mapped[str] = mapped_column(Unicode(50), nullable=False)
    payload_json: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    status: Mapped[str] = mapped_column(Unicode(20), default="pending", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    session: Mapped["AIChatSession"] = relationship("AIChatSession")
    user: Mapped["User"] = relationship("User")


class AIActionAudit(Base):
    __tablename__ = "ai_action_audit"

    __table_args__ = (
        CheckConstraint("outcome IN ('success', 'failed', 'cancelled')", name="CK_ai_action_audit_outcome"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pending_action_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ai_pending_actions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(Unicode(30), nullable=False)
    execution_log: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    pending_action: Mapped["AIPendingAction"] = relationship(
        "AIPendingAction",
        backref=backref("audit_logs", cascade="all, delete-orphan", lazy="select"),
    )
    user: Mapped["User"] = relationship("User")
