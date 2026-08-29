from pathlib import Path


ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "database" / "postgres" / "migrations" / "20260835_vbpl_jobs_cache.sql"
ROLLBACK = ROOT / "database" / "postgres" / "migrations" / "20260835_vbpl_jobs_cache.rollback.sql"


def test_vbpl_job_migration_defines_durable_state_and_rollback() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    rollback = ROLLBACK.read_text(encoding="utf-8").lower()
    for table in ("vbpl_ingest_jobs", "vbpl_ingest_items", "vbpl_ingest_stages"):
        assert f"create table if not exists public.{table}" in sql
        assert f"drop table if exists public.{table}" in rollback
    assert "for update skip locked" not in sql
    assert "vbpl_ingest_jobs_claim_idx" in sql
    assert "vbpl_ingest_stages_claim_idx" in sql
    assert "create trigger vbpl_ingest_jobs_updated_at" in sql
    assert "drop function if exists public.update_vbpl_job_updated_at" in rollback
