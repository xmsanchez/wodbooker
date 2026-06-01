# Booking flow

This document describes how a single `Booking` record drives an automatic WodBuster reservation via the `Booker` thread in `wodbooker/booker.py`.

## Thread lifecycle

```
start_booking_loop(booking)
  → whitelist check (BOOKING_WHITELIST_EMAILS)
  → Booker(booking, app.app_context()).start()
  → register in __CURRENT_THREADS

stop_booking_loop(booking)
  → booker.stop(_StopThreadException)
  → remove from __CURRENT_THREADS
```

`views.py` must call start/stop when creating, editing, deleting, or toggling `is_active` on bookings.

## Main loop (`Booker.run`)

Each iteration:

1. Reload `Booking` from DB inside Flask `app_context`.
2. Compute target datetime with `_get_datetime_to_book`.
3. Wait for booking window via `_wait_for_booking_window` (`_TimeWaiter`).
4. Priority sleep (non-`PRIORITY_USERS_EMAILS` → 1 second).
5. Acquire `_GLOBAL_BOOKING_LOCK` (minimum `GLOBAL_BOOKING_INTERVAL` = 0.5s between any user's attempts).
6. `get_scraper(email, cookie)` → `_attempt_booking` → `scraper.book(...)`.
7. On success: `_handle_successful_booking` + push notification.
8. `db.session.commit()` in `finally`.

Loop exits on: `_StopThreadException`, `errors >= _MAX_ERRORS` (500), or `force_exit` (credential/box failures).

## Computing the next class (`_get_datetime_to_book`)

Timezone: **Europe/Madrid** (`_MADRID_TZ`).

Rules:

1. If no `last_book_date` or it is before today, and **today** matches `dow` and class time has not passed → book **today**.
2. Else find next occurrence of `dow` on or after `max(today, last_book_date + 1 day)`.
3. If that datetime is still in the past → add 7 days (next week same weekday).

**Weekly recurrence**: after a successful book, `last_book_date` is set to `day_to_book`. The next loop iteration targets the following week's same weekday.

**Skip week**: `skip_current_week` adds 7 days when:
- `ClassNotFound` after `_MAX_BOOKING_ATTEMPTS` (20) retries, or
- `BookingFailed` (non-recoverable book error for that week).

## Booking window

Opening time = `(day_to_book - offset days)` at `booking.available_at` (Madrid TZ).

Example: class on Friday, `offset=2`, `available_at=12:00` → booking attempts start Wednesday 12:00.

`_wait_for_booking_window` creates a `_TimeWaiter` if none exists and blocks until that datetime.

## Waiters

### `_TimeWaiter`

- `time.sleep` until `wait_datetime`.
- Used for: booking window, `BookingNotAvailable.available_at`, network/API backoff.

### `_EventWaiter`

- Calls `scraper.wait_until_event(url, date, expected_events, max_datetime)`.
- SSE events on WodBuster `bookinghub` hub.

| Situation | SSE events waited |
|-----------|-------------------|
| Class full | `changedBooking` |
| Classes not loaded | `changedPizarra`, `changedBooking` |
| Penalization (no parsed wait time) | `changedBooking` |

When a waiter finishes and `_datetime_to_book` changes, logs `EventMessage.CLASS_WAITING_OVER` and resets `class_is_full_notification_sent`.

## `_attempt_booking`

Calls `scraper.book(url, datetime_to_book, type_class)`.

- **`BookingLockedException`**: retry every `BOOKING_LOCKED_DELAY` (0.2s) until success (user booking elsewhere).
- Other exceptions propagate to the main loop handlers.

## Success path

1. `EventMessage.BOOKING_COMPLETED`
2. Email: `SuccessEmail`, or `SuccessAfterErrorEmail` if recovering from errors / full-class wait
3. Update `last_book_date`, `booked_at`
4. Persist fresh cookies: `user.cookie = scraper.get_cookies()`
5. `send_booking_status_notification(..., success=True)`

## Error handling summary

| Exception | Action |
|-----------|--------|
| `ClassNotFound` | Retry up to 20× (1s delay), then skip week |
| `BookingPenalization` | Parse wait from message, or sleep 10s + `_EventWaiter` |
| `BookingFailed` | Skip week, email + push failure |
| `ClassIsFull` | `_EventWaiter` on `changedBooking`, email once |
| `BookingNotAvailable` | `_TimeWaiter` or `_EventWaiter` (classes not loaded) |
| `RequestException` / `InvalidWodBusterResponse` | Backoff `(errors+1)*60` seconds, email on first error |
| `PasswordRequired` / `LoginError` | `force_exit`, `force_login=True`, email |
| `InvalidBox` | `force_exit`, email |

Full scraper-side causes: [wodbuster-integration.md](wodbuster-integration.md).

## Event deduplication (`_add_event`)

Before inserting an `Event`, skips if the last event for the same `booking_id` has the same message (avoids duplicate log noise during waits).

## Constants (tuning)

| Constant | Value | Purpose |
|----------|-------|---------|
| `_MAX_ERRORS` | 500 | Exit thread after repeated network/API failures |
| `_MAX_BOOKING_ATTEMPTS` | 20 | Retries for `ClassNotFound` |
| `GLOBAL_BOOKING_INTERVAL` | 0.5s | Min gap between any user's book attempts |
| `BOOKING_RETRY_DELAY` | 1s | Delay between `ClassNotFound` retries |
| `BOOKING_LOCKED_DELAY` | 0.2s | Retry interval for locked booking |

User-visible strings: `constants.EventMessage`.

## Sync functions (not Booker threads)

- **`sync_wodbuster_bookings(user)`** — Fetches booked classes from API into `WodBusterBooking`; marks missing rows `is_cancelled=True`. See skill `wodbuster-sync`.
- **`sync_training_descriptions_for_date(user, date)`** — Caches WOD text in `ClassTrainingDescription`.

## Related

- [wodbuster-integration.md](wodbuster-integration.md)
- `.cursor/rules/booker.mdc`
- `.cursor/skills/booking-behavior-change/SKILL.md`
