-- Migration: 029_create_ai_travel_planner_module.sql
-- Description: Centralized AI Smart Travel Planner & Itinerary Database Tables
-- Module: AI Chat Sessions, Messages, Travel Preferences, Plans, Days, Items, Actions & Audit
-- Target Database: Microsoft SQL Server (CeylonGoDB)
-- Safe & Idempotent: Can be executed multiple times safely without data loss.

-- =============================================================================
-- Batch 1: Create dbo.ai_chat_sessions & dbo.ai_chat_messages
-- =============================================================================
IF OBJECT_ID('dbo.ai_chat_sessions', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ai_chat_sessions (
        id INT IDENTITY(1,1) NOT NULL,
        user_id INT NOT NULL,
        session_title NVARCHAR(200) NOT NULL,
        is_active BIT NOT NULL CONSTRAINT DF_ai_chat_sessions_active DEFAULT 1,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_ai_chat_sessions_created DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NULL,

        CONSTRAINT PK_ai_chat_sessions PRIMARY KEY (id),

        CONSTRAINT FK_ai_chat_sessions_user
            FOREIGN KEY (user_id)
            REFERENCES dbo.users(id)
            ON DELETE CASCADE
    );

    CREATE NONCLUSTERED INDEX IX_ai_chat_sessions_user
        ON dbo.ai_chat_sessions(user_id);

    PRINT 'ai_chat_sessions table created.';
END;

IF OBJECT_ID('dbo.ai_chat_messages', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ai_chat_messages (
        id INT IDENTITY(1,1) NOT NULL,
        session_id INT NOT NULL,
        sender_role NVARCHAR(20) NOT NULL,
        message_content NVARCHAR(MAX) NOT NULL,
        structured_payload NVARCHAR(MAX) NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_ai_chat_messages_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_ai_chat_messages PRIMARY KEY (id),

        CONSTRAINT FK_ai_chat_messages_session
            FOREIGN KEY (session_id)
            REFERENCES dbo.ai_chat_sessions(id)
            ON DELETE CASCADE,

        CONSTRAINT CK_ai_chat_messages_role
            CHECK (sender_role IN ('user', 'assistant', 'system'))
    );

    CREATE NONCLUSTERED INDEX IX_ai_chat_messages_session
        ON dbo.ai_chat_messages(session_id, created_at);

    PRINT 'ai_chat_messages table created.';
END;
GO

-- =============================================================================
-- Batch 2: Create dbo.travel_preferences
-- =============================================================================
IF OBJECT_ID('dbo.travel_preferences', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.travel_preferences (
        id INT IDENTITY(1,1) NOT NULL,
        user_id INT NOT NULL,
        preferred_budget_level NVARCHAR(30) NOT NULL CONSTRAINT DF_travel_pref_budget DEFAULT 'medium',
        pace NVARCHAR(30) NOT NULL CONSTRAINT DF_travel_pref_pace DEFAULT 'moderate',
        dietary_preferences NVARCHAR(255) NULL,
        travel_party_type NVARCHAR(50) NOT NULL CONSTRAINT DF_travel_pref_party DEFAULT 'solo',
        interests NVARCHAR(500) NULL,
        accessibility_needs NVARCHAR(500) NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_travel_preferences_created DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NULL,

        CONSTRAINT PK_travel_preferences PRIMARY KEY (id),

        CONSTRAINT FK_travel_preferences_user
            FOREIGN KEY (user_id)
            REFERENCES dbo.users(id)
            ON DELETE CASCADE,

        CONSTRAINT UQ_travel_preferences_user
            UNIQUE (user_id),

        CONSTRAINT CK_travel_pref_budget
            CHECK (preferred_budget_level IN ('budget', 'medium', 'luxury')),

        CONSTRAINT CK_travel_pref_pace
            CHECK (pace IN ('relaxed', 'moderate', 'fast_paced')),

        CONSTRAINT CK_travel_pref_party
            CHECK (travel_party_type IN ('solo', 'couple', 'family', 'group'))
    );

    PRINT 'travel_preferences table created.';
END;
GO

-- =============================================================================
-- Batch 3: Create dbo.travel_plans & dbo.travel_plan_days
-- =============================================================================
IF OBJECT_ID('dbo.travel_plans', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.travel_plans (
        id INT IDENTITY(1,1) NOT NULL,
        user_id INT NOT NULL,
        ai_session_id INT NULL,
        title NVARCHAR(200) NOT NULL,
        summary NVARCHAR(1000) NULL,
        start_date DATE NOT NULL,
        end_date DATE NOT NULL,
        total_days INT NOT NULL,
        estimated_budget DECIMAL(12,2) NULL,
        currency NVARCHAR(3) NOT NULL CONSTRAINT DF_travel_plans_currency DEFAULT 'LKR',
        is_shared BIT NOT NULL CONSTRAINT DF_travel_plans_shared DEFAULT 0,
        share_token NVARCHAR(100) NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_travel_plans_created DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NULL,

        CONSTRAINT PK_travel_plans PRIMARY KEY (id),

        -- ON DELETE NO ACTION prevents cycle with ai_chat_sessions cascade
        CONSTRAINT FK_travel_plans_user
            FOREIGN KEY (user_id)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT FK_travel_plans_session
            FOREIGN KEY (ai_session_id)
            REFERENCES dbo.ai_chat_sessions(id)
            ON DELETE SET NULL,

        CONSTRAINT UQ_travel_plans_share_token
            UNIQUE (share_token),

        CONSTRAINT CK_travel_plans_dates
            CHECK (end_date >= start_date),

        CONSTRAINT CK_travel_plans_days
            CHECK (total_days > 0),

        CONSTRAINT CK_travel_plans_budget
            CHECK (estimated_budget IS NULL OR estimated_budget >= 0)
    );

    CREATE NONCLUSTERED INDEX IX_travel_plans_user
        ON dbo.travel_plans(user_id);

    CREATE NONCLUSTERED INDEX IX_travel_plans_dates
        ON dbo.travel_plans(start_date, end_date);

    PRINT 'travel_plans table created.';
END;

IF OBJECT_ID('dbo.travel_plan_days', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.travel_plan_days (
        id INT IDENTITY(1,1) NOT NULL,
        travel_plan_id INT NOT NULL,
        day_number INT NOT NULL,
        date_value DATE NOT NULL,
        day_title NVARCHAR(200) NOT NULL,
        day_summary NVARCHAR(1000) NULL,
        overnight_destination NVARCHAR(150) NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_travel_plan_days_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_travel_plan_days PRIMARY KEY (id),

        CONSTRAINT FK_travel_plan_days_plan
            FOREIGN KEY (travel_plan_id)
            REFERENCES dbo.travel_plans(id)
            ON DELETE CASCADE,

        CONSTRAINT UQ_travel_plan_days
            UNIQUE (travel_plan_id, day_number),

        CONSTRAINT CK_travel_plan_days_number
            CHECK (day_number > 0)
    );

    CREATE NONCLUSTERED INDEX IX_travel_plan_days_plan
        ON dbo.travel_plan_days(travel_plan_id);

    PRINT 'travel_plan_days table created.';
END;
GO

-- =============================================================================
-- Batch 4: Create dbo.travel_plan_items
-- =============================================================================
IF OBJECT_ID('dbo.travel_plan_items', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.travel_plan_items (
        id INT IDENTITY(1,1) NOT NULL,
        plan_day_id INT NOT NULL,
        item_order INT NOT NULL,
        item_type NVARCHAR(30) NOT NULL,
        title NVARCHAR(200) NOT NULL,
        description NVARCHAR(1000) NULL,
        destination_name NVARCHAR(150) NULL,
        latitude DECIMAL(9,6) NULL,
        longitude DECIMAL(9,6) NULL,
        start_time TIME NULL,
        end_time TIME NULL,
        estimated_cost DECIMAL(10,2) NULL,
        listing_reference_id INT NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_travel_plan_items_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_travel_plan_items PRIMARY KEY (id),

        CONSTRAINT FK_travel_plan_items_day
            FOREIGN KEY (plan_day_id)
            REFERENCES dbo.travel_plan_days(id)
            ON DELETE CASCADE,

        CONSTRAINT FK_travel_plan_items_listing
            FOREIGN KEY (listing_reference_id)
            REFERENCES dbo.listing_references(id)
            ON DELETE SET NULL,

        CONSTRAINT CK_travel_plan_items_type
            CHECK (item_type IN ('attraction', 'activity', 'homestay', 'restaurant', 'transit', 'guide')),

        CONSTRAINT CK_travel_plan_items_order
            CHECK (item_order >= 0),

        CONSTRAINT CK_travel_plan_items_cost
            CHECK (estimated_cost IS NULL OR estimated_cost >= 0)
    );

    CREATE NONCLUSTERED INDEX IX_travel_plan_items_day
        ON dbo.travel_plan_items(plan_day_id, item_order);

    CREATE NONCLUSTERED INDEX IX_travel_plan_items_listing
        ON dbo.travel_plan_items(listing_reference_id);

    PRINT 'travel_plan_items table created.';
END;
GO

-- =============================================================================
-- Batch 5: Create dbo.ai_pending_actions & dbo.ai_action_audit
-- =============================================================================
IF OBJECT_ID('dbo.ai_pending_actions', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ai_pending_actions (
        id INT IDENTITY(1,1) NOT NULL,
        session_id INT NOT NULL,
        user_id INT NOT NULL,
        action_type NVARCHAR(50) NOT NULL,
        payload_json NVARCHAR(MAX) NOT NULL,
        status NVARCHAR(20) NOT NULL CONSTRAINT DF_ai_pending_actions_status DEFAULT 'pending',
        expires_at DATETIME2 NOT NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_ai_pending_actions_created DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NULL,

        CONSTRAINT PK_ai_pending_actions PRIMARY KEY (id),

        CONSTRAINT FK_ai_pending_actions_session
            FOREIGN KEY (session_id)
            REFERENCES dbo.ai_chat_sessions(id)
            ON DELETE CASCADE,

        -- ON DELETE NO ACTION prevents cycle with users table
        CONSTRAINT FK_ai_pending_actions_user
            FOREIGN KEY (user_id)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_ai_pending_actions_status
            CHECK (status IN ('pending', 'approved', 'rejected', 'executed', 'expired'))
    );

    CREATE NONCLUSTERED INDEX IX_ai_pending_actions_session
        ON dbo.ai_pending_actions(session_id);

    CREATE NONCLUSTERED INDEX IX_ai_pending_actions_user_status
        ON dbo.ai_pending_actions(user_id, status);

    PRINT 'ai_pending_actions table created.';
END;

IF OBJECT_ID('dbo.ai_action_audit', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ai_action_audit (
        id INT IDENTITY(1,1) NOT NULL,
        pending_action_id INT NOT NULL,
        user_id INT NOT NULL,
        outcome NVARCHAR(30) NOT NULL,
        execution_log NVARCHAR(MAX) NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_ai_action_audit_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_ai_action_audit PRIMARY KEY (id),

        CONSTRAINT FK_ai_action_audit_action
            FOREIGN KEY (pending_action_id)
            REFERENCES dbo.ai_pending_actions(id)
            ON DELETE CASCADE,

        CONSTRAINT FK_ai_action_audit_user
            FOREIGN KEY (user_id)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_ai_action_audit_outcome
            CHECK (outcome IN ('success', 'failed', 'cancelled'))
    );

    CREATE NONCLUSTERED INDEX IX_ai_action_audit_action
        ON dbo.ai_action_audit(pending_action_id);

    PRINT 'ai_action_audit table created.';
END;
GO
