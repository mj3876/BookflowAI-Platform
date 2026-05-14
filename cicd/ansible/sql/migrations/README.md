# RDS Schema Migrations

Numbered migrations applied **after** the baseline (`../001_tables.sql` + `../002_indexes.sql` + `../003_grants.sql`).

## File naming
`<3-digit version>_<short description>.sql` — applied in lexicographic order.

## Tracking
`schema_versions` table records each applied version (created by `001_init.sql`).
Each migration MUST end with:
```sql
INSERT INTO schema_versions (version, description, applied_at)
VALUES ('NNN', 'short description', NOW())
ON CONFLICT (version) DO NOTHING;
```

## Idempotency
All DDL must be idempotent (`ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, etc.).
Re-running migrations on already-migrated DB must be a no-op.

## Application
- **Initial deploy**: `rds_seed.py` task runs all migrations in order after baseline.
- **Drift fix on existing DB**: SSM directly applies the new migration file via `psql -f`.

## Current migrations
| # | file | description |
|---|---|---|
| 001 | 001_init.sql | baseline marker + schema_versions table creation |
| 002 | 002_returns_reject_columns.sql | A4 FR-A6.8: returns.rejected_at + reject_reason |
| 005 | 005_wh_to_store_order_type.sql | Stage 0 WH_TO_STORE order_type 정합 코멘트 |
