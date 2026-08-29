-- Retired VBPL bootstrap migration.
-- Active datasets are provisioned by the release pipeline. Do not infer
-- application configuration from pg_settings or create a dataset here.
-- Kept as a recorded no-op so installations that already discovered this
-- migration remain checksum-stable and safe to upgrade.
SELECT 1;
