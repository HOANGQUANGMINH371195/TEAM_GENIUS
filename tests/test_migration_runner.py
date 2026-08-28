from pathlib import Path

from database.postgres.migrations.runner import migration_files, migration_sql, sync_database_url


def test_migration_runner_normalizes_asyncpg_url_and_strips_transaction_wrappers(tmp_path: Path):
    path = tmp_path / "20260101_example.sql"
    path.write_text("BEGIN;\nALTER TABLE example ADD COLUMN value text;\nCOMMIT;\n", encoding="utf-8")

    assert sync_database_url("postgresql+asyncpg://user:pass@host/db") == "postgresql://user:pass@host/db"
    assert migration_sql(path).strip() == "ALTER TABLE example ADD COLUMN value text;"
    files = migration_files(tmp_path)
    assert len(files) == 1
    assert files[0].version == "20260101_example"
    assert len(files[0].checksum) == 64
