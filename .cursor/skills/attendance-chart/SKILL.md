---
name: attendance-chart
description: Change attendance history Chart.js chart, tooltip, toggles, or trend line. Use when editing attendance-history.js, attendance_history.html, or chart behavior on /attendance-history.
---

# Attendance history chart

## Before coding

1. Read [docs/attendance-history-chart.md](../../../docs/attendance-history-chart.md).
2. Open `.cursor/rules/attendance-chart.mdc` if editing `attendance-history.js` or `attendance_history.html`.

## Classify the change

| Change type | Touch |
|-------------|--------|
| New chart series / colors | `attendance-history.js` datasets, `rerenderChart()`, legend |
| Tooltip line (with box) | New dataset + `TOOLTIP_ITEM_ORDER` + `formatTooltipLabel` |
| Tooltip line (no box) | `extraAttendanceTooltipLines()` / `afterLabel` on Reservadas |
| Trend window / label | `TREND_WINDOW`, dataset label “Tendencia N meses”, doc |
| Range / yearly toggle | `applyRangeLimit`, `aggregateYearly`, button handlers |
| New month field from API | `attendance_stats.py` `get_attendance_history()`, template table, JS `monthlySeries` / `aggregateYearly`, doc |

Backfill/regenerate UI: `attendance-backfill.js` — not chart rendering.

## Tooltip decision tree

- **Colored swatch needed?** → Add a Chart.js dataset (update indices in `rerenderChart` if inserting before index 3).
- **Text only?** → Do not add a hidden dataset; use `afterLabel` on dataset 1 or `afterBody`.
- **Never** rely on `label: ['a', 'b', …]` on one dataset for multiple swatches.

## Implementation checklist

- [ ] Minimal diff in `attendance-history.js`; match existing IIFE style
- [ ] `buildSeries()` / `rerenderChart()` stay consistent for all 4 datasets
- [ ] `TOOLTIP_ITEM_ORDER` matches desired tooltip order for dataset lines
- [ ] Spanish labels match legend and tooltip text
- [ ] Update [docs/attendance-history-chart.md](../../../docs/attendance-history-chart.md) if behavior or data shape changed

## Manual test

1. Login; open `/attendance-history` with multi-month data
2. Hover a month: 6 lines, 4 color boxes (not on No presentado / Canceladas)
3. Toggle “Mostrar todos los años” and “Mostrar anualmente”
4. Hard-refresh if static JS appears cached

## After merge

Update `docs/attendance-history-chart.md` and cross-links in `AGENTS.md` if new routes or API fields were added.
