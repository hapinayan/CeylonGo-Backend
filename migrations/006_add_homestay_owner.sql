
-- M01.4: Add Homestay Ownership
-- Database: CeylonGoDB

IF COL_LENGTH('dbo.homestays', 'owner_id') IS NULL
BEGIN
    ALTER TABLE dbo.homestays
    ADD owner_id INT NULL;
END;

IF NOT EXISTS (
    SELECT 1
    FROM sys.foreign_keys
    WHERE name = 'FK_homestays_owner'
      AND parent_object_id = OBJECT_ID('dbo.homestays')
)
BEGIN
    ALTER TABLE dbo.homestays
    ADD CONSTRAINT FK_homestays_owner
        FOREIGN KEY (owner_id)
        REFERENCES dbo.users(id);
END;

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_homestays_owner_id'
      AND object_id = OBJECT_ID('dbo.homestays')
)
BEGIN
    CREATE INDEX IX_homestays_owner_id
    ON dbo.homestays(owner_id);
END;
