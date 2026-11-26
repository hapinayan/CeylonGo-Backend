CREATE TABLE dbo.traveler_settings (
    user_id BIGINT NOT NULL CONSTRAINT pk_traveler_settings PRIMARY KEY
        CONSTRAINT fk_traveler_settings_user REFERENCES dbo.users(id),
    country NVARCHAR(80) NULL,
    avatar NVARCHAR(40) NULL,
    email_verified BIT NOT NULL CONSTRAINT df_traveler_settings_email DEFAULT 0,
    google_only BIT NOT NULL CONSTRAINT df_traveler_settings_google DEFAULT 0,
    preferences NVARCHAR(MAX) NOT NULL CONSTRAINT df_traveler_settings_preferences DEFAULT N'{}',
    booking_updates BIT NOT NULL CONSTRAINT df_traveler_settings_booking DEFAULT 1,
    payment_updates BIT NOT NULL CONSTRAINT df_traveler_settings_payment DEFAULT 1,
    community_activity BIT NOT NULL CONSTRAINT df_traveler_settings_community DEFAULT 1,
    promotional_updates BIT NOT NULL CONSTRAINT df_traveler_settings_promotion DEFAULT 0,
    CONSTRAINT ck_traveler_settings_preferences CHECK (ISJSON(preferences) = 1)
);

CREATE TABLE dbo.traveler_notifications (
    id BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_traveler_notifications PRIMARY KEY,
    recipient_user_id BIGINT NOT NULL CONSTRAINT fk_traveler_notifications_recipient
        REFERENCES dbo.users(id),
    notification_type NVARCHAR(50) NOT NULL,
    category NVARCHAR(20) NOT NULL,
    title NVARCHAR(120) NOT NULL,
    message NVARCHAR(250) NOT NULL,
    related_entity_type NVARCHAR(20) NOT NULL,
    related_entity_id BIGINT NOT NULL,
    source_comment_id BIGINT NULL,
    is_read BIT NOT NULL CONSTRAINT df_traveler_notifications_read DEFAULT 0,
    created_at DATETIME2(3) NOT NULL,
    read_at DATETIME2(3) NULL,
    event_key NVARCHAR(120) NOT NULL CONSTRAINT uq_traveler_notifications_event UNIQUE,
    CONSTRAINT ck_traveler_notifications_category CHECK (
        category IN (N'booking', N'payment', N'community', N'account')
    ),
    CONSTRAINT ck_traveler_notifications_entity CHECK (
        related_entity_id > 0 AND
        ((category IN (N'booking', N'payment') AND related_entity_type = N'booking')
         OR (category = N'community' AND related_entity_type = N'community_post')
         OR (category = N'account' AND related_entity_type = N'account'))
    ),
    CONSTRAINT ck_traveler_notifications_read CHECK (
        (is_read = 0 AND read_at IS NULL) OR (is_read = 1 AND read_at IS NOT NULL)
    )
);
CREATE INDEX ix_traveler_notifications_feed ON dbo.traveler_notifications
    (recipient_user_id, created_at DESC, id DESC);
CREATE INDEX ix_traveler_notifications_unread ON dbo.traveler_notifications
    (recipient_user_id, is_read, created_at DESC, id DESC);

