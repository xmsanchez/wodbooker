---
name: booking-behavior-change
description: Change or fix Booker thread behavior, waiters, retries, or scheduling. Use for booking bugs, wait/retry logic, weekly recurrence, or penalization handling.
---

# Booking behavior change

## Read first

1. [docs/booking-flow.md](../../../docs/booking-flow.md) — full loop, waiters, constants
2. [docs/wodbuster-integration.md](../../../docs/wodbuster-integration.md) — exception matrix
3. `wodbooker/booker.py` — `Booker.run`, `_get_datetime_to_book`, waiters

## Trace the path

1. Identify which **exception** or **wait state** is involved
2. Check if fix belongs in **booker** (retry/wait policy) or **scraper** (API parsing/detection)
3. Only change `scraper.py` if WodBuster response handling is wrong

## Side effects to check

| Change type | Also review |
|-------------|-------------|
| Success/failure messaging | `constants.EventMessage`, `Event` rows |
| User notifications | `mailer.py` (`SuccessEmail`, `ErrorEmail`, …) |
| Push on book result | `push_notifications.send_booking_status_notification` |
| Thread start/stop | `views.py` `BookingAdmin` CRUD and `/active` endpoint |
| Whitelist / priority | `BOOKING_WHITELIST_EMAILS`, `PRIORITY_USERS_EMAILS` |

## Testing

- Test with **active** and **inactive** booking (`is_active`)
- Test pause/resume via `/booking/active` POST
- Watch `logs/wodbooker.log` and `logs/wodbooker-high-level.log`
- Confirm `db.session.commit()` in `finally` blocks is not broken

## Tuning constants

Defined at top of `booker.py`:

- `_MAX_ERRORS`, `_MAX_BOOKING_ATTEMPTS`
- `GLOBAL_BOOKING_INTERVAL`, `BOOKING_RETRY_DELAY`, `BOOKING_LOCKED_DELAY`

Document changes in PR if behavior-visible to users.

## After merge

Update `docs/booking-flow.md` if wait/retry rules change.
