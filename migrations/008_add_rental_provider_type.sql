
-- Module 09: Add Rental Provider Type
-- Preserve all existing provider types.

IF OBJECT_ID(N'dbo.provider_profiles', N'U') IS NULL
BEGIN
    THROW 50001, 'provider_profiles table does not exist.', 1;
END;
GO

IF EXISTS (
    SELECT 1
    FROM sys.check_constraints
    WHERE name = N'CK_provider_profiles_type'
      AND parent_object_id = OBJECT_ID(N'dbo.provider_profiles')
)
BEGIN
    ALTER TABLE dbo.provider_profiles
    DROP CONSTRAINT CK_provider_profiles_type;
END;
GO

ALTER TABLE dbo.provider_profiles
WITH CHECK ADD CONSTRAINT CK_provider_profiles_type
CHECK (
    provider_type IN (
        'homestay',
        'tour_guide',
        'activity',
        'eco_tourism',
        'artisan',
        'rental'
    )
);
GO
