# Attendance history chart and tooltip

Standalone page at `/attendance-history` (linked from the booking list). Chart.js 4.4.1 renders monthly or yearly attendance from cached WodBuster stats.

## Data flow (server → chart)

| Layer | File | Role |
|-------|------|------|
| API / page | `wodbooker/__init__.py` | `GET /attendance-history`, `GET /api/attendance/history` |
| Aggregation | `wodbooker/attendance_stats.py` | `get_attendance_history()` — builds `months[]` |
| Template | `wodbooker/templates/attendance_history.html` | Injects `window.ATTENDANCE_HISTORY_DATA`; loads Chart.js CDN |
| Chart UI | `wodbooker/static/attendance-history.js` | All Chart.js config, toggles, tooltip |
| Backfill UI | `wodbooker/static/attendance-backfill.js` | `POST /api/attendance/backfill`, regenerate — **not** chart logic |

### `ATTENDANCE_HISTORY_DATA` shape

From `get_attendance_history()` (page and API share the same payload):

- `months[]` — each row: `label` (`YYYY-MM`), `year`, `month`, `attended`, `booked`, `no_show`, `cancelled` (number or `null`), `prev_year_attended`, `fetched_at`
- `incomplete`, `months_remaining`, `stale` — banners on the page
- `chart` — legacy parallel arrays (page uses `months` in JS)

**Previous year semantics:**

- **Monthly view:** `prev_year_attended` = same calendar month one year earlier.
- **Yearly view:** `aggregateYearly()` sums months per year; “año anterior” = previous **calendar year** total.

Backend cache: `AthleteMonthlyStats` + `User.attendance_history_from` (see `docs/architecture.md`).

## Chart datasets (4 visible series)

All configuration lives in `attendance-history.js`. Combo chart: `type: 'bar'` with line datasets.

| Index | Legend | Type | Data source |
|-------|--------|------|-------------|
| 0 | Asistidas | bar (blue) | `currentSeries.attended` |
| 1 | Reservadas | bar (grey) | `currentSeries.booked` |
| 2 | Asistidas año anterior | line (yellow) | `currentSeries.prevYearAttended` |
| 3 | Tendencia 6 meses | line (green, dashed) | `currentSeries.attendedTrend` |

**Not drawn on chart:** `no_show`, `cancelled` — shown in the HTML table and tooltip only.

### Client-side transforms

```
allMonths (from window)
  → monthlySeries() OR aggregateYearly()
  → applyRangeLimit()   # last 13 periods unless “Mostrar todos los años”
  → movingAverage(attended, TREND_WINDOW)  # TREND_WINDOW = 6
  → currentSeries
```

- **Trailing moving average:** for index `i`, average of `attended[i-window+1..i]` (partial window at start of series).
- **Range toggle:** `#historyRangeToggleBtn` — `showFullHistory` flips 13-month cap.
- **Granularity toggle:** `#historyGranularityToggleBtn` — `showYearly` switches monthly/yearly aggregation.
- **`rerenderChart()`:** updates `labels` and `datasets[0..3].data` only; calls `chart.update()`.

## Tooltip design

Chart.js 4 shows **one colored swatch per tooltip item (per dataset)**. `labelColor` is not invoked per line when a single dataset’s `label` callback returns a string array.

### Current pattern

- `interaction.mode`: `'index'`, `intersect: false` — one tooltip for all series at the same x-index.
- `itemSort`: `TOOLTIP_ITEM_ORDER` — Asistidas → Reservadas → Asistidas año anterior → Tendencia 6 meses.
- `label`: `formatTooltipLabel(ctx)` → `dataset.label + ': ' + parsed.y`.
- **`afterLabel` on dataset index 1 (Reservadas) only:** `extraAttendanceTooltipLines(dataIndex)` appends:
  - `No presentado: …`
  - `Canceladas: …` (`—` when `cancelled` is `null`)
  - Plain text, **no** color boxes (by design).

### Display order when hovering a month

1. Title: period label (e.g. `2026-03`)
2. Asistidas (swatch)
3. Reservadas (swatch)
4. No presentado (text)
5. Canceladas (text)
6. Asistidas año anterior (swatch)
7. Tendencia 6 meses (swatch)

### How to extend

| Goal | Approach |
|------|----------|
| New metric **with** colored box | Add a Chart.js dataset; update legend, `TOOLTIP_ITEM_ORDER`, `rerenderChart()` |
| New metric **without** box | Extend `extraAttendanceTooltipLines()` or `afterLabel`; avoid fake datasets |
| Reorder tooltip lines | Change `TOOLTIP_ITEM_ORDER` and/or `afterLabel` placement |

**Do not** return `label: ['line1', 'line2', …]` from dataset 0 expecting multiple swatches — only the first line gets a box.

## Pitfalls

- **Array `label` on one dataset:** multiple text lines, one swatch — use separate datasets or `afterLabel` instead.
- **`TREND_WINDOW` / “Tendencia N meses”:** keep constant, dataset label, legend, and this doc aligned when changing the window.
- **Yearly mode:** fewer points; moving average uses 1–5 month partial windows at the start of the visible series.
- **Static cache:** browsers cache `attendance-history.js`; hard-refresh after JS changes.
- **Cancelled null:** monthly rows may lack historical cancel data; tooltip shows `—`, not `0`.

## Manual test checklist

1. Open `/attendance-history` with several months of data.
2. Hover a bar: six text lines, four colored boxes (not on No presentado / Canceladas).
3. Yellow line matches “Asistidas año anterior”; green dashed line matches “Tendencia 6 meses”.
4. Toggle “Mostrar todos los años” / “Mostrar último año” — chart and trend recompute on visible window.
5. Toggle “Mostrar anualmente” / “Mostrar mensualmente” — yearly aggregation and prev-year semantics.
6. Table below chart still matches tooltip numbers for the same month.

## Related routes

| Path | Purpose |
|------|---------|
| `/attendance-history` | Page (this chart) |
| `/api/attendance/history` | Same JSON as page data |
| `/api/attendance/backfill` | Historical month fetch (UI in `attendance-backfill.js`) |
| `/api/attendance/regenerate-history` | Purge cached history |

Agent entry points: `.cursor/rules/attendance-chart.mdc`, skill `attendance-chart`, `AGENTS.md`.
