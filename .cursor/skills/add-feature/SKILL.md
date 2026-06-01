---
name: add-feature
description: End-to-end checklist for implementing a new WodBooker feature. Use when adding user-visible functionality, new booking fields, admin forms, or cross-cutting behavior.
---

# Add feature (WodBooker)

## Before coding

1. Read [AGENTS.md](../../../AGENTS.md) and `.cursor/rules/architecture.mdc`.
2. Classify which surfaces the feature touches:
   - **Model/DB** → run [db-migration](../db-migration/SKILL.md) first
   - **Booking scheduling** → `booker.py`, [booking-flow.md](../../../docs/booking-flow.md)
   - **WodBuster API** → `scraper.py`, [wodbuster-integration.md](../../../docs/wodbuster-integration.md)
   - **Admin UI** → `views.py`, `wodbooker/templates/`, `admin-ui.mdc`
   - **Notifications** → `mailer.py`, `push_notifications.py`, `notification_scheduler.py`
   - **Routes** → `__init__.py`

## Implementation checklist

- [ ] Minimal diff; match existing naming and patterns in touched files
- [ ] If new DB column: SQL in `migrations/vX.Y.Z/`, update `models.py`, run `python migrate.py vX.Y.Z`
- [ ] If `Booking` field affects scheduling: update `BookingForm`, booker logic, and start/stop thread callers in `views.py`
- [ ] If user-visible status text: add/use `constants.EventMessage`
- [ ] If admin field: `BookingForm` or `UserForm` + template if needed
- [ ] Do **not** change Docker, nginx, or README ops sections unless explicitly requested

## Booker thread rule

Any create/update/delete/toggle on `Booking` that changes `is_active` or scheduling fields must call:

- `start_booking_loop(booking)` when should run
- `stop_booking_loop(booking)` when should stop

See existing handlers in `BookingAdmin` (`views.py`).

## Manual test

1. Login with valid WodBuster credentials
2. Create or edit the affected booking/preference
3. Check `Event` log for expected messages (hidden `/event/` view)
4. If booking-related: verify thread starts/stops (logs show `Booker {id}`)

## After merge (context hygiene)

Update `docs/architecture.md` model table and/or relevant rule if invariants changed.
