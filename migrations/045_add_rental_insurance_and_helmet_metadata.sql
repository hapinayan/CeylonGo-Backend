IF COL_LENGTH(N'dbo.rental_vehicles', N'helmet_condition') IS NULL
    ALTER TABLE dbo.rental_vehicles ADD helmet_condition NVARCHAR(100) NULL;
GO

IF COL_LENGTH(N'dbo.rental_vehicle_documents', N'insurance_effective_date') IS NULL
    ALTER TABLE dbo.rental_vehicle_documents ADD insurance_effective_date DATE NULL;
GO

IF COL_LENGTH(N'dbo.rental_vehicle_documents', N'insurance_exclusions') IS NULL
    ALTER TABLE dbo.rental_vehicle_documents ADD insurance_exclusions NVARCHAR(1000) NULL;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = N'CK_rental_vehicle_docs_insurance_dates'
      AND parent_object_id = OBJECT_ID(N'dbo.rental_vehicle_documents')
)
    ALTER TABLE dbo.rental_vehicle_documents WITH CHECK
        ADD CONSTRAINT CK_rental_vehicle_docs_insurance_dates
        CHECK (insurance_effective_date IS NULL OR
               (expiry_date IS NOT NULL AND insurance_effective_date <= expiry_date));
GO
