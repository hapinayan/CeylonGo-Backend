from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Unicode, UnicodeText
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mvp import MVPBase, PK_TYPE


class Conversation(MVPBase):
    __tablename__ = "ai_conversations"

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    traveler_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(Unicode(120))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class ConversationTurn(MVPBase):
    __tablename__ = "ai_conversation_turns"

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ai_conversations.id"), index=True,
    )
    user_message: Mapped[str] = mapped_column(Unicode(2500))
    result_json: Mapped[str] = mapped_column(UnicodeText)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Trip(MVPBase):
    __tablename__ = "ai_saved_trips"

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    traveler_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(Unicode(120))
    itinerary_json: Mapped[str] = mapped_column(UnicodeText)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
