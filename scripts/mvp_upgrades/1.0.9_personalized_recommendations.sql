ALTER TABLE dbo.traveler_settings ADD behavioral_personalization BIT NOT NULL
    CONSTRAINT df_traveler_settings_behavioral_personalization DEFAULT (0);

CREATE TABLE dbo.traveler_activity_events (
    id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    traveler_id BIGINT NOT NULL REFERENCES dbo.users(id),
    event_type VARCHAR(6) NOT NULL,
    listing_id BIGINT NULL REFERENCES dbo.listings(id),
    category VARCHAR(16) NULL,
    search_query NVARCHAR(120) NULL,
    district NVARCHAR(80) NULL,
    destination NVARCHAR(80) NULL,
    filters NVARCHAR(MAX) NULL,
    fingerprint VARBINARY(32) NOT NULL,
    created_at DATETIME2 NOT NULL,
    CONSTRAINT ck_traveler_activity_event_shape CHECK (
        (event_type = 'view' AND listing_id IS NOT NULL AND category IS NOT NULL
            AND search_query IS NULL)
        OR (event_type = 'search' AND listing_id IS NULL)
    )
);

CREATE INDEX ix_traveler_activity_recent
    ON dbo.traveler_activity_events (traveler_id, created_at, id);
CREATE INDEX ix_traveler_activity_dedupe
    ON dbo.traveler_activity_events (traveler_id, fingerprint, created_at);
CREATE INDEX ix_traveler_activity_expiry ON dbo.traveler_activity_events (created_at);
