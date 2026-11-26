CREATE TABLE dbo.ai_conversations (
    id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    traveler_id BIGINT NOT NULL REFERENCES dbo.users(id),
    title NVARCHAR(120) NOT NULL,
    revision INT NOT NULL DEFAULT 0 CHECK (revision >= 0),
    created_at DATETIME2(3) NOT NULL,
    updated_at DATETIME2(3) NOT NULL,
    deleted_at DATETIME2(3) NULL
);
CREATE INDEX ix_ai_conversations_traveler_id ON dbo.ai_conversations(traveler_id, id);

CREATE TABLE dbo.ai_conversation_turns (
    id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    conversation_id BIGINT NOT NULL REFERENCES dbo.ai_conversations(id),
    user_message NVARCHAR(2500) NOT NULL,
    result_json NVARCHAR(MAX) NOT NULL CHECK (ISJSON(result_json) = 1),
    created_at DATETIME2(3) NOT NULL
);
CREATE INDEX ix_ai_conversation_turns_conversation_id
    ON dbo.ai_conversation_turns(conversation_id, id);

CREATE TABLE dbo.ai_saved_trips (
    id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    traveler_id BIGINT NOT NULL REFERENCES dbo.users(id),
    title NVARCHAR(120) NOT NULL,
    itinerary_json NVARCHAR(MAX) NOT NULL CHECK (ISJSON(itinerary_json) = 1),
    created_at DATETIME2(3) NOT NULL,
    updated_at DATETIME2(3) NOT NULL,
    deleted_at DATETIME2(3) NULL
);
CREATE INDEX ix_ai_saved_trips_traveler_id ON dbo.ai_saved_trips(traveler_id, id);
