IF OBJECT_ID(N'dbo.rental_age_rules', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.rental_age_rules (
        id INT IDENTITY(1,1) NOT NULL,
        vehicle_type NVARCHAR(20) NOT NULL,
        minimum_age INT NOT NULL,
        CONSTRAINT PK_rental_age_rules PRIMARY KEY (id),
        CONSTRAINT UQ_rental_age_rules_vehicle_type UNIQUE (vehicle_type),
        CONSTRAINT CK_rental_age_rules_vehicle_type
            CHECK (vehicle_type IN (N'bike', N'bicycle', N'tuk_tuk')),
        CONSTRAINT CK_rental_age_rules_minimum_age CHECK (minimum_age BETWEEN 0 AND 120)
    );
END;
GO
