# AGENTS.md — WodBooker

WodBooker is a Flask application that logs into WodBuster on behalf of users and automatically books recurring class slots (day-of-week + time + box URL), with optional email/push notifications and WodBuster booking sync.

## Where to start

| Task | Open first | Also read |
|------|------------|-----------|
| New feature (end-to-end) | This file + `.cursor/skills/add-feature/` | Relevant rule in `.cursor/rules/` |
| Booking / wait / retry bug | `wodbooker/booker.py`, `docs/booking-flow.md` | `scraper.mdc`, `docs/wodbuster-integration.md` |
| WodBuster API / login bug | `wodbooker/scraper.py`, `docs/wodbuster-integration.md` | `exceptions.py` |
| DB column / model | `wodbooker/models.py`, `.cursor/skills/db-migration/` | `migrations.mdc` |
| Admin / templates | `wodbooker/views.py`, `wodbooker/templates/` | `admin-ui.mdc` |
| Push / email | `push_notifications.py`, `notification_scheduler.py`, `mailer.py` | README (VAPID setup) |

## Critical invariants

- Datetimes for booking: **Europe/Madrid**.
- One **Booker** thread per active `Booking`; start/stop via `booker.start_booking_loop` / `stop_booking_loop`.
- WodBuster session: pickled cookies on `User`; Booker refreshes cookies after successful book.
- `BOOKING_WHITELIST_EMAILS` gates who may auto-book.
- Flask-Admin lives at **`/`** (not `/admin`).

## Run locally

```bash
pip install -r requirements.txt
python app.py   # http://0.0.0.0:5000
```

Production/Docker: see [README.md](README.md).

## Migrations

```bash
python migrate.py v1.12.0
```

See [docs/architecture.md](docs/architecture.md) for DB path and auto-migration note.

## Deep docs

- [docs/architecture.md](docs/architecture.md)
- [docs/booking-flow.md](docs/booking-flow.md)
- [docs/wodbuster-integration.md](docs/wodbuster-integration.md)

## Cursor rules

Always-on: `.cursor/rules/architecture.mdc`. File-scoped rules load when matching paths are edited.

## Context hygiene

After a PR, update docs/rules when you change: models (architecture doc), booking logic (`booking-flow.md`), WodBuster errors (`wodbuster-integration.md`), admin routes (`admin-ui.mdc`), or env vars (`architecture.mdc`).
