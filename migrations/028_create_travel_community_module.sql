-- Migration: 028_create_travel_community_module.sql
-- Description: Centralized Travel Community Database Tables
-- Module: Community Posts, Media, Likes, Comments, Saved Posts, Follows, Reports & Hashtags
-- Target Database: Microsoft SQL Server (CeylonGoDB)
-- Safe & Idempotent: Can be executed multiple times safely without data loss.

-- =============================================================================
-- Batch 1: Create dbo.community_posts & dbo.community_post_media
-- =============================================================================
IF OBJECT_ID('dbo.community_posts', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.community_posts (
        id INT IDENTITY(1,1) NOT NULL,
        author_id INT NOT NULL,
        content NVARCHAR(MAX) NOT NULL,
        location_tag NVARCHAR(150) NULL,
        district NVARCHAR(100) NULL,
        latitude DECIMAL(9,6) NULL,
        longitude DECIMAL(9,6) NULL,
        likes_count INT NOT NULL CONSTRAINT DF_community_posts_likes DEFAULT 0,
        comments_count INT NOT NULL CONSTRAINT DF_community_posts_comments DEFAULT 0,
        is_published BIT NOT NULL CONSTRAINT DF_community_posts_published DEFAULT 1,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_community_posts_created DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NULL,

        CONSTRAINT PK_community_posts PRIMARY KEY (id),

        CONSTRAINT FK_community_posts_author
            FOREIGN KEY (author_id)
            REFERENCES dbo.users(id)
            ON DELETE CASCADE,

        CONSTRAINT CK_community_posts_likes
            CHECK (likes_count >= 0),

        CONSTRAINT CK_community_posts_comments
            CHECK (comments_count >= 0)
    );

    CREATE NONCLUSTERED INDEX IX_community_posts_author
        ON dbo.community_posts(author_id);

    CREATE NONCLUSTERED INDEX IX_community_posts_created
        ON dbo.community_posts(created_at DESC);

    CREATE NONCLUSTERED INDEX IX_community_posts_district
        ON dbo.community_posts(district);

    PRINT 'community_posts table created.';
END;

IF OBJECT_ID('dbo.community_post_media', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.community_post_media (
        id INT IDENTITY(1,1) NOT NULL,
        post_id INT NOT NULL,
        media_type NVARCHAR(20) NOT NULL CONSTRAINT DF_community_post_media_type DEFAULT 'image',
        media_url NVARCHAR(500) NOT NULL,
        display_order INT NOT NULL CONSTRAINT DF_community_post_media_order DEFAULT 0,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_community_post_media_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_community_post_media PRIMARY KEY (id),

        CONSTRAINT FK_community_post_media_post
            FOREIGN KEY (post_id)
            REFERENCES dbo.community_posts(id)
            ON DELETE CASCADE,

        CONSTRAINT CK_community_post_media_type
            CHECK (media_type IN ('image', 'video')),

        CONSTRAINT CK_community_post_media_order
            CHECK (display_order >= 0)
    );

    CREATE NONCLUSTERED INDEX IX_community_post_media_post
        ON dbo.community_post_media(post_id, display_order);

    PRINT 'community_post_media table created.';
END;
GO

-- =============================================================================
-- Batch 2: Create dbo.community_post_likes & dbo.community_comments
-- =============================================================================
IF OBJECT_ID('dbo.community_post_likes', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.community_post_likes (
        id INT IDENTITY(1,1) NOT NULL,
        post_id INT NOT NULL,
        user_id INT NOT NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_community_post_likes_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_community_post_likes PRIMARY KEY (id),

        CONSTRAINT FK_community_post_likes_post
            FOREIGN KEY (post_id)
            REFERENCES dbo.community_posts(id)
            ON DELETE CASCADE,

        -- ON DELETE NO ACTION prevents multiple cascade path / cycle (Error 1785) with posts.author_id
        CONSTRAINT FK_community_post_likes_user
            FOREIGN KEY (user_id)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT UQ_community_post_likes
            UNIQUE (post_id, user_id)
    );

    CREATE NONCLUSTERED INDEX IX_community_post_likes_user
        ON dbo.community_post_likes(user_id, post_id);

    PRINT 'community_post_likes table created.';
END;

IF OBJECT_ID('dbo.community_comments', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.community_comments (
        id INT IDENTITY(1,1) NOT NULL,
        post_id INT NOT NULL,
        author_id INT NOT NULL,
        parent_comment_id INT NULL,
        content NVARCHAR(1000) NOT NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_community_comments_created DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NULL,

        CONSTRAINT PK_community_comments PRIMARY KEY (id),

        CONSTRAINT FK_community_comments_post
            FOREIGN KEY (post_id)
            REFERENCES dbo.community_posts(id)
            ON DELETE CASCADE,

        -- ON DELETE NO ACTION prevents cascade cycle with users table
        CONSTRAINT FK_community_comments_author
            FOREIGN KEY (author_id)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT FK_community_comments_parent
            FOREIGN KEY (parent_comment_id)
            REFERENCES dbo.community_comments(id)
            ON DELETE NO ACTION
    );

    CREATE NONCLUSTERED INDEX IX_community_comments_post
        ON dbo.community_comments(post_id, created_at);

    CREATE NONCLUSTERED INDEX IX_community_comments_author
        ON dbo.community_comments(author_id);

    CREATE NONCLUSTERED INDEX IX_community_comments_parent
        ON dbo.community_comments(parent_comment_id);

    PRINT 'community_comments table created.';
END;
GO

-- =============================================================================
-- Batch 3: Create dbo.community_saved_posts & dbo.community_follows
-- =============================================================================
IF OBJECT_ID('dbo.community_saved_posts', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.community_saved_posts (
        id INT IDENTITY(1,1) NOT NULL,
        user_id INT NOT NULL,
        post_id INT NOT NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_community_saved_posts_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_community_saved_posts PRIMARY KEY (id),

        CONSTRAINT FK_community_saved_posts_user
            FOREIGN KEY (user_id)
            REFERENCES dbo.users(id)
            ON DELETE CASCADE,

        CONSTRAINT FK_community_saved_posts_post
            FOREIGN KEY (post_id)
            REFERENCES dbo.community_posts(id)
            ON DELETE NO ACTION,

        CONSTRAINT UQ_community_saved_posts
            UNIQUE (user_id, post_id)
    );

    CREATE NONCLUSTERED INDEX IX_community_saved_posts_post
        ON dbo.community_saved_posts(post_id);

    PRINT 'community_saved_posts table created.';
END;

IF OBJECT_ID('dbo.community_follows', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.community_follows (
        id INT IDENTITY(1,1) NOT NULL,
        follower_id INT NOT NULL,
        following_id INT NOT NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_community_follows_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_community_follows PRIMARY KEY (id),

        CONSTRAINT FK_community_follows_follower
            FOREIGN KEY (follower_id)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT FK_community_follows_following
            FOREIGN KEY (following_id)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT UQ_community_follows
            UNIQUE (follower_id, following_id),

        CONSTRAINT CK_community_follows_distinct
            CHECK (follower_id <> following_id)
    );

    CREATE NONCLUSTERED INDEX IX_community_follows_follower
        ON dbo.community_follows(follower_id);

    CREATE NONCLUSTERED INDEX IX_community_follows_following
        ON dbo.community_follows(following_id);

    PRINT 'community_follows table created.';
END;
GO

-- =============================================================================
-- Batch 4: Create dbo.community_reports
-- =============================================================================
IF OBJECT_ID('dbo.community_reports', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.community_reports (
        id INT IDENTITY(1,1) NOT NULL,
        reporter_id INT NOT NULL,
        entity_type NVARCHAR(30) NOT NULL,
        entity_id INT NOT NULL,
        report_reason NVARCHAR(100) NOT NULL,
        description NVARCHAR(1000) NULL,
        report_status NVARCHAR(20) NOT NULL CONSTRAINT DF_community_reports_status DEFAULT 'pending',
        reviewed_by INT NULL,
        reviewed_at DATETIME2 NULL,
        review_notes NVARCHAR(500) NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_community_reports_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_community_reports PRIMARY KEY (id),

        CONSTRAINT FK_community_reports_reporter
            FOREIGN KEY (reporter_id)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT FK_community_reports_reviewer
            FOREIGN KEY (reviewed_by)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_community_reports_type
            CHECK (entity_type IN ('post', 'comment', 'user')),

        CONSTRAINT CK_community_reports_status
            CHECK (report_status IN ('pending', 'investigating', 'resolved', 'dismissed'))
    );

    CREATE NONCLUSTERED INDEX IX_community_reports_entity
        ON dbo.community_reports(entity_type, entity_id);

    CREATE NONCLUSTERED INDEX IX_community_reports_status
        ON dbo.community_reports(report_status);

    CREATE NONCLUSTERED INDEX IX_community_reports_reporter
        ON dbo.community_reports(reporter_id);

    PRINT 'community_reports table created.';
END;
GO

-- =============================================================================
-- Batch 5: Create dbo.community_hashtags & dbo.community_post_hashtags
-- =============================================================================
IF OBJECT_ID('dbo.community_hashtags', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.community_hashtags (
        id INT IDENTITY(1,1) NOT NULL,
        tag NVARCHAR(100) NOT NULL,
        usage_count INT NOT NULL CONSTRAINT DF_community_hashtags_usage DEFAULT 1,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_community_hashtags_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_community_hashtags PRIMARY KEY (id),

        CONSTRAINT UQ_community_hashtags_tag
            UNIQUE (tag),

        CONSTRAINT CK_community_hashtags_usage
            CHECK (usage_count >= 0)
    );

    CREATE NONCLUSTERED INDEX IX_community_hashtags_usage
        ON dbo.community_hashtags(usage_count DESC);

    PRINT 'community_hashtags table created.';
END;

IF OBJECT_ID('dbo.community_post_hashtags', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.community_post_hashtags (
        id INT IDENTITY(1,1) NOT NULL,
        post_id INT NOT NULL,
        hashtag_id INT NOT NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_community_post_hashtags_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_community_post_hashtags PRIMARY KEY (id),

        CONSTRAINT FK_community_post_hashtags_post
            FOREIGN KEY (post_id)
            REFERENCES dbo.community_posts(id)
            ON DELETE CASCADE,

        CONSTRAINT FK_community_post_hashtags_hashtag
            FOREIGN KEY (hashtag_id)
            REFERENCES dbo.community_hashtags(id)
            ON DELETE CASCADE,

        CONSTRAINT UQ_community_post_hashtags
            UNIQUE (post_id, hashtag_id)
    );

    CREATE NONCLUSTERED INDEX IX_community_post_hashtags_hashtag
        ON dbo.community_post_hashtags(hashtag_id, post_id);

    PRINT 'community_post_hashtags table created.';
END;
GO
