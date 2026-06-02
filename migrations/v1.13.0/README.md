# Migration v1.13.0 — Athlete attendance stats

Adds `athlete_monthly_stats` cache table. Run before v1.13.1.

```bash
python migrate.py v1.13.0
python migrate.py v1.13.1
```

On app startup, missing v1.13.0 / v1.13.1 schema is applied automatically (same as v1.9.0 push columns). Restart the container after deploying new code if the DB predates this feature.

See parent plan for backup/restore (`rollback_athlete_monthly_stats.sql`).

## Manual QA

1. Sync WodBuster — home card shows **calendar month** class counts separate from **billing period credits**.
2. **Completar historial** fills older months; past months are not re-fetched on autosync.
3. **Regenerar historial** clears cache; run backfill again if needed.
