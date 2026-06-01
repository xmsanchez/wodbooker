---
name: db-migration
description: Add or change SQLite schema for WodBooker. Use when adding columns, tables, indexes, or constraints on any SQLAlchemy model.
---

# DB migration (WodBooker)

## Steps

1. **Pick version folder**: `migrations/vX.Y.Z/` (next semver after latest in `migrations/`).

2. **Write SQL**: `migrations/vX.Y.Z/descriptive_name.sql`
   - One or more statements separated by `;`
   - SQLite syntax (`ALTER TABLE`, `CREATE TABLE`, indexes, etc.)

3. **Rollback (if destructive)**: `migrations/vX.Y.Z/rollback_descriptive_name.sql`
   - `migrate.py` does **not** run rollbacks automatically — manual only

4. **Update model**: `wodbooker/models.py` — match column types and constraints

5. **Apply**:
   ```bash
   python migrate.py vX.Y.Z
   ```

6. **Verify idempotency**: Re-run migrate; expect "duplicate column" / "already exists" warnings and skip (not fatal)

## DB file location

`migrate.py` resolves in order:

1. `instance/db.sqlite`
2. `db.sqlite`
3. `wodbooker/db.sqlite`

Runtime app uses `wodbooker/db.sqlite` per `__init__.py` config.

## Startup auto-migration

Only **v1.9.0** runs automatically on app start (push notification columns). **New versions are manual** unless you add similar logic to `__init__.py` (avoid unless necessary).

## Checklist

- [ ] SQL file in versioned folder
- [ ] `models.py` updated
- [ ] Rollback SQL if dropping columns/tables
- [ ] Migration tested locally
- [ ] PR notes mention `python migrate.py vX.Y.Z` for deployers

## Do not

- Use Alembic (project uses raw SQL + `migrate.py`)
- Forget unique constraints that mirror `__table_args__` on models
