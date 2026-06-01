# WodBuster integration

WodBuster communication lives in `wodbooker/scraper.py`. Domain errors are defined in `wodbooker/exceptions.py` and handled in `wodbooker/booker.py`.

## HTTP client

- Library: **`cloudscraper`** (Cloudflare-aware session).
- Cookies: pickled bytes via `get_cookies()` / constructor; stored on `User.cookie` in the database.
- One cached `Scraper` per email via `get_scraper(email, cookie)`.
- Full re-login via `refresh_scraper(email, password)` (login form only).

## Login flow

1. If cookies provided: load into session → GET `roadtobox.aspx`.
2. If redirected to login → `_login_with_username_and_password()`.
3. ASP.NET form POST to `login.aspx` (viewstate, event validation).
4. Optional “trust this device” confirmation step.
5. Failure: `LoginError` (warning on form) or `PasswordRequired` (cookie stale, no password).
6. Multi-box users: `get_box_url()` follows redirect; single box expected for auto-book path.

## API handlers (athlete)

Base path pattern: `{box_url}/athlete/handlers/`

| Handler | Used by | Purpose |
|---------|---------|---------|
| `LoadClass.ashx` | `get_classes`, `book`, sync | Day schedule JSON (`ticks` query param) |
| `Calendario_Inscribir.ashx` | `book` | Enroll in class |
| `Calendario_Mover.ashx` | `book` | Move reservation (if already booked) |
| `Calendario_Borrar.ashx` | `cancel_booking` | Cancel reservation |

`book()` flow:

1. `LoadClass.ashx` for target date → find class matching `Hora` and `type_class`.
2. If no `Data` → `BookingNotAvailable` (may include `PrimeraHoraPublicacion` as `available_at`).
3. If `AtletasEntrenando >= Plazas` → `ClassIsFull`.
4. If no matching hour → `ClassNotFound`.
5. POST enroll/move → if `EsCorrecto` false → `BookingFailed`, `BookingPenalization`, or `BookingLockedException` based on message text.

## Server-Sent Events (SSE)

`wait_until_event(url, date, expected_events, max_datetime)`:

1. Load box homepage, extract SignalR connection info.
2. Connect to `bookinghub` SSE stream.
3. `JoinRoom` for the box.
4. Block until one of `expected_events` fires or timeout.

Common events:

| Event | Meaning |
|-------|---------|
| `changedBooking` | Booking slots changed (cancellation freed a spot) |
| `changedPizarra` | Schedule/board loaded or updated |

## Sync endpoints

| Method | API | DB target |
|--------|-----|-----------|
| `get_user_booked_classes` | `LoadClass.ashx?ticks=&idu=` | `WodBusterBooking` |
| `get_training_descriptions` | `LoadClass.ashx` (parses `ClasesDesc`) | `ClassTrainingDescription` |

Requires `user.athlete_id` (set at login from `preferences.aspx`).

## Exception → Booker action matrix

| Exception | Raised when | Booker action |
|-----------|-------------|---------------|
| `LoginError` | Login form warning; box redirect to login | Abort thread, `force_login=True`, email |
| `PasswordRequired` | Stale cookie, no password for re-login | Abort thread, `force_login=True`, email |
| `InvalidBox` | API 302 to login; SSE box name parse fails | Abort thread, email |
| `InvalidWodBusterResponse` | Bad HTTP status, JSON, or network in `_book_request` | Backoff wait, increment errors, email on first |
| `BookingNotAvailable` | No class data for day | `_TimeWaiter` or `_EventWaiter` (not loaded) |
| `ClassIsFull` | No free slots | `_EventWaiter` (`changedBooking`), email once |
| `ClassNotFound` | No matching time slot | Retry 20×, then skip week |
| `BookingFailed` | Book API rejected (not penalization/locked) | Skip week, email + push failure |
| `BookingPenalization` | Too soon after cancel / penalization message | Sleep parsed duration or SSE wait |
| `BookingLockedException` | User booking in another session | Retry every 0.2s in `_attempt_booking` |

`RequestException` from `requests` is handled like transient network errors (not defined in `exceptions.py`).

## Cookie refresh contract

After every **successful** `scraper.book()`, Booker sets:

```python
self._booking.user.cookie = scraper.get_cookies()
```

Keeps the WodBuster session valid for subsequent API calls and SSE.

## Logging safety

Use `_safe_log_response_content(response_text, max_length=2000)` for API responses. Never log passwords or full cookie jars.

## Fragility notes

- WodBuster HTML and JSON shapes change without notice. Preserve existing parsing patterns when extending.
- Penalization messages are parsed with regex for Spanish duration text (`minuto`, `segundo`).
- `type_class`: `0` = wod, `1` = openbox (see `Booking.type_class`).

## Related

- [booking-flow.md](booking-flow.md)
- `.cursor/rules/scraper.mdc`
- `.cursor/skills/wodbuster-sync/SKILL.md`
