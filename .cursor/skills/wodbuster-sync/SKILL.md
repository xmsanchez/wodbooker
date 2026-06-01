---
name: wodbuster-sync
description: Work on WodBuster booking sync or training description cache. Use when fixing sync accuracy, cancelled classes, autosync, or WodBusterBooking/ClassTrainingDescription data.
---

# WodBuster sync

## Entry points

| Function | File | Purpose |
|----------|------|---------|
| `sync_wodbuster_bookings(user)` | `booker.py` | Mirror API bookings → `WodBusterBooking` |
| `sync_training_descriptions_for_date(user, date, box_url?)` | `booker.py` | Cache WOD text → `ClassTrainingDescription` |

## Prerequisites

- `user.athlete_id` must be set (fetched at login from `preferences.aspx`)
- Box URL from latest `Booking.url` or `scraper.get_box_url()`

## `sync_wodbuster_bookings` behavior

1. Date range: current week Mon–Sun (Madrid TZ)
2. May extend to **next week** if any user `Booking` opening window is already open (`offset`, `available_at`)
3. Per day: `scraper.get_user_booked_classes` → upsert `WodBusterBooking`
4. Rows in DB but missing from API → `is_cancelled=True`
5. May inline training description sync for same days
6. Returns `{success, new, updated, cancelled, errors}`

## UI triggers

- `BookingAdmin` list render (badges, autosync if `wodbuster_autosync_enabled`)
- `POST /booking/sync-wodbuster-bookings` (form redirect)
- `POST /api/wodbuster/sync` (AJAX, `__init__.py`)

## Models

- **`WodBusterBooking`**: unique `(user_id, class_id, class_date)`; used by push reminder scheduler
- **`ClassTrainingDescription`**: unique `(user_id, class_date, id_pizarra)`

## Cancel flow

- `POST /booking/cancel-wodbuster-booking` → `scraper.cancel_booking` + update local row

## Debugging

1. Confirm `athlete_id` and valid `User.cookie`
2. Compare API response parsing in `scraper.get_user_booked_classes`
3. Check week boundary logic vs `Booking` offset/opening times
4. Read `training_descriptions` logger for description sync issues

## Related

- [wodbuster-integration.md](../../../docs/wodbuster-integration.md)
- `notification_scheduler.py` reads `WodBusterBooking` for class reminders

## After merge

Update `docs/wodbuster-integration.md` if API handlers or sync rules change.
