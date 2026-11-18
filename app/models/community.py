from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Unicode,
    UnicodeText,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.models.user import Base, User


class CommunityPost(Base):
    __tablename__ = "community_posts"

    __table_args__ = (
        CheckConstraint("likes_count >= 0", name="CK_community_posts_likes"),
        CheckConstraint("comments_count >= 0", name="CK_community_posts_comments"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    author_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    location_tag: Mapped[str | None] = mapped_column(Unicode(150), nullable=True)
    district: Mapped[str | None] = mapped_column(Unicode(100), nullable=True, index=True)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    likes_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    comments_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False, index=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    author: Mapped["User"] = relationship("User", foreign_keys=[author_id])


class CommunityPostMedia(Base):
    __tablename__ = "community_post_media"

    __table_args__ = (
        CheckConstraint("media_type IN ('image', 'video')", name="CK_community_post_media_type"),
        CheckConstraint("display_order >= 0", name="CK_community_post_media_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("community_posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    media_type: Mapped[str] = mapped_column(Unicode(20), default="image", nullable=False)
    media_url: Mapped[str] = mapped_column(Unicode(500), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    post: Mapped["CommunityPost"] = relationship(
        "CommunityPost",
        backref=backref("media", cascade="all, delete-orphan", lazy="select"),
    )


class CommunityPostLike(Base):
    __tablename__ = "community_post_likes"

    __table_args__ = (
        UniqueConstraint("post_id", "user_id", name="UQ_community_post_likes"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("community_posts.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    post: Mapped["CommunityPost"] = relationship("CommunityPost")
    user: Mapped["User"] = relationship("User")


class CommunityComment(Base):
    __tablename__ = "community_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("community_posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    parent_comment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("community_comments.id"), nullable=True, index=True
    )
    content: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    post: Mapped["CommunityPost"] = relationship(
        "CommunityPost",
        backref=backref("comments", cascade="all, delete-orphan", lazy="select"),
    )
    author: Mapped["User"] = relationship("User")


class CommunitySavedPost(Base):
    __tablename__ = "community_saved_posts"

    __table_args__ = (
        UniqueConstraint("user_id", "post_id", name="UQ_community_saved_posts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("community_posts.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    user: Mapped["User"] = relationship("User")
    post: Mapped["CommunityPost"] = relationship("CommunityPost")


class CommunityFollow(Base):
    __tablename__ = "community_follows"

    __table_args__ = (
        UniqueConstraint("follower_id", "following_id", name="UQ_community_follows"),
        CheckConstraint("follower_id <> following_id", name="CK_community_follows_distinct"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    follower_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    following_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    follower: Mapped["User"] = relationship("User", foreign_keys=[follower_id])
    following: Mapped["User"] = relationship("User", foreign_keys=[following_id])


class CommunityReport(Base):
    __tablename__ = "community_reports"

    __table_args__ = (
        CheckConstraint("entity_type IN ('post', 'comment', 'user')", name="CK_community_reports_type"),
        CheckConstraint("report_status IN ('pending', 'investigating', 'resolved', 'dismissed')", name="CK_community_reports_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reporter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(Unicode(30), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    report_reason: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    report_status: Mapped[str] = mapped_column(
        Unicode(20), default="pending", nullable=False, index=True
    )
    reviewed_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class CommunityHashtag(Base):
    __tablename__ = "community_hashtags"

    __table_args__ = (
        CheckConstraint("usage_count >= 0", name="CK_community_hashtags_usage"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tag: Mapped[str] = mapped_column(Unicode(100), unique=True, nullable=False)
    usage_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class CommunityPostHashtag(Base):
    __tablename__ = "community_post_hashtags"

    __table_args__ = (
        UniqueConstraint("post_id", "hashtag_id", name="UQ_community_post_hashtags"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("community_posts.id", ondelete="CASCADE"), nullable=False
    )
    hashtag_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("community_hashtags.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
