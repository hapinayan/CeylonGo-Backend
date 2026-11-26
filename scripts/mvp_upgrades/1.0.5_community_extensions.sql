IF OBJECT_ID(N'dbo.mvp_schema_versions', N'U') IS NULL
    THROW 51046, 'The Academic MVP ledger is required.', 1;

IF OBJECT_ID(N'dbo.community_post_media', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.community_post_media (
        id BIGINT IDENTITY(1,1) CONSTRAINT pk_community_post_media PRIMARY KEY CLUSTERED,
        post_id BIGINT NOT NULL,
        storage_key NVARCHAR(450) NOT NULL,
        media_type VARCHAR(20) NOT NULL CONSTRAINT df_community_post_media_type DEFAULT 'image',
        content_type VARCHAR(32) NOT NULL CONSTRAINT df_community_post_media_ct DEFAULT 'image/jpeg',
        alt_text NVARCHAR(160) NULL,
        display_order SMALLINT NOT NULL CONSTRAINT df_community_post_media_order DEFAULT 0,
        is_visible BIT NOT NULL CONSTRAINT df_community_post_media_visible DEFAULT 1,
        created_at DATETIME2(3) NOT NULL CONSTRAINT df_community_post_media_created DEFAULT SYSUTCDATETIME(),
        CONSTRAINT fk_community_post_media_post FOREIGN KEY (post_id) REFERENCES dbo.community_posts(id),
        CONSTRAINT uq_community_post_media_storage_key UNIQUE NONCLUSTERED (storage_key),
        CONSTRAINT ck_community_post_media_order CHECK (display_order >= 0),
        CONSTRAINT ck_community_post_media_type CHECK (media_type IN ('image'))
    );

    CREATE NONCLUSTERED INDEX ix_community_post_media_post ON dbo.community_post_media (post_id, is_visible, display_order, id);
END;

IF OBJECT_ID(N'dbo.community_saved_posts', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.community_saved_posts (
        user_id BIGINT NOT NULL,
        post_id BIGINT NOT NULL,
        created_at DATETIME2(3) NOT NULL CONSTRAINT df_community_saved_posts_created DEFAULT SYSUTCDATETIME(),
        CONSTRAINT pk_community_saved_posts PRIMARY KEY CLUSTERED (user_id, post_id),
        CONSTRAINT fk_community_saved_posts_user FOREIGN KEY (user_id) REFERENCES dbo.users(id),
        CONSTRAINT fk_community_saved_posts_post FOREIGN KEY (post_id) REFERENCES dbo.community_posts(id)
    );

    CREATE NONCLUSTERED INDEX ix_community_saved_posts_post ON dbo.community_saved_posts (post_id);
END;

IF OBJECT_ID(N'dbo.community_reports', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.community_reports (
        id BIGINT IDENTITY(1,1) CONSTRAINT pk_community_reports PRIMARY KEY CLUSTERED,
        reporter_id BIGINT NOT NULL,
        entity_type VARCHAR(16) NOT NULL,
        entity_id BIGINT NOT NULL,
        reason NVARCHAR(100) NOT NULL,
        description NVARCHAR(1000) NULL,
        status VARCHAR(20) NOT NULL CONSTRAINT df_community_reports_status DEFAULT 'pending',
        reviewed_by BIGINT NULL,
        review_notes NVARCHAR(500) NULL,
        reviewed_at DATETIME2(3) NULL,
        created_at DATETIME2(3) NOT NULL CONSTRAINT df_community_reports_created DEFAULT SYSUTCDATETIME(),
        CONSTRAINT fk_community_reports_reporter FOREIGN KEY (reporter_id) REFERENCES dbo.users(id),
        CONSTRAINT fk_community_reports_reviewer FOREIGN KEY (reviewed_by) REFERENCES dbo.users(id),
        CONSTRAINT ck_community_reports_entity_type CHECK (entity_type IN ('post', 'comment')),
        CONSTRAINT ck_community_reports_status CHECK (status IN ('pending', 'reviewed', 'resolved', 'dismissed'))
    );

    CREATE NONCLUSTERED INDEX ix_community_reports_status ON dbo.community_reports (status, entity_type, id);
    CREATE NONCLUSTERED INDEX ix_community_reports_entity ON dbo.community_reports (entity_type, entity_id);
END;
GO
