-- DBA review: apply through scripts/migrate.py in its per-migration transaction.
-- Run the two EXISTS queries as read-only preflight checks; both must be false.
-- If invalid rows are found, agree on remediation separately and rerun preflight.
-- WITH CHECK revalidates every row under the DDL lock. Schedule for a maintenance
-- window because validation and schema modification can block concurrent writes.
-- A failure rolls back both constraint changes and the runner's history write.
-- After commit, use a separately approved forward migration to restore the old
-- predicates if needed. Keep migration history intact. This migration changes no rows.

IF OBJECT_ID(N'dbo.tour_guides', N'U') IS NULL
   OR OBJECT_ID(N'dbo.tour_guide_packages', N'U') IS NULL
    THROW 50300, 'Migration 032 requires the Tour Guide tables from migration 022.', 1;

IF EXISTS (
    SELECT 1
    FROM dbo.tour_guides
    WHERE (base_latitude IS NULL AND base_longitude IS NOT NULL)
       OR (base_latitude IS NOT NULL AND base_longitude IS NULL)
       OR base_latitude NOT BETWEEN -90.0 AND 90.0
       OR base_longitude NOT BETWEEN -180.0 AND 180.0
)
    THROW 50301, 'Invalid base coordinates in dbo.tour_guides. Review existing rows before retrying.', 1;

IF EXISTS (
    SELECT 1
    FROM dbo.tour_guide_packages
    WHERE (meeting_latitude IS NULL AND meeting_longitude IS NOT NULL)
       OR (meeting_latitude IS NOT NULL AND meeting_longitude IS NULL)
       OR meeting_latitude NOT BETWEEN -90.0 AND 90.0
       OR meeting_longitude NOT BETWEEN -180.0 AND 180.0
)
    THROW 50302, 'Invalid meeting coordinates in dbo.tour_guide_packages. Review existing rows before retrying.', 1;

ALTER TABLE dbo.tour_guides DROP CONSTRAINT CK_tour_guides_coords;

ALTER TABLE dbo.tour_guides WITH CHECK ADD CONSTRAINT CK_tour_guides_coords
    CHECK (
        (base_latitude IS NULL AND base_longitude IS NULL)
        OR (base_latitude IS NOT NULL AND base_longitude IS NOT NULL
            AND base_latitude BETWEEN -90.0 AND 90.0
            AND base_longitude BETWEEN -180.0 AND 180.0)
    );

ALTER TABLE dbo.tour_guide_packages DROP CONSTRAINT CK_tour_guide_packages_coords;

ALTER TABLE dbo.tour_guide_packages WITH CHECK ADD CONSTRAINT CK_tour_guide_packages_coords
    CHECK (
        (meeting_latitude IS NULL AND meeting_longitude IS NULL)
        OR (meeting_latitude IS NOT NULL AND meeting_longitude IS NOT NULL
            AND meeting_latitude BETWEEN -90.0 AND 90.0
            AND meeting_longitude BETWEEN -180.0 AND 180.0)
    );
