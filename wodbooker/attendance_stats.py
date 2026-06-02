"""
Sync and read cached WodBuster athlete attendance stats.
"""
from __future__ import annotations

import datetime
import logging
from typing import Optional

import pytz
from sqlalchemy.exc import OperationalError

from .exceptions import InvalidWodBusterResponse
from .models import db, User, Booking, AthleteMonthlyStats
from .scraper import get_scraper, Scraper
from .wodbuster_attendance import (
    parse_services_payload,
    parse_billing_period_stats,
    parse_reservations_payload,
    stats_from_services_for_calendar_month,
    aggregate_calendar_month,
    compute_quota_mood,
    compute_ytd_yoy_mood,
    month_label_es,
    month_name_es,
    format_period_range,
    MonthStats,
    BillingPeriodStats,
)

_MADRID_TZ = pytz.timezone('Europe/Madrid')
_MAX_HISTORY_MONTHS = 120
_MANUAL_BACKFILL_MONTHS_PER_RUN = 12
_AUTOSYNC_BACKFILL_MONTHS = 0
_STALE_HOURS = 24
_EMPTY_MONTHS_TO_STOP = 2


def sync_wodbuster_all(user: User, box_url: Optional[str] = None) -> dict:
    from .booker import sync_wodbuster_bookings

    bookings = sync_wodbuster_bookings(user)
    attendance = sync_attendance_stats(user, box_url=box_url, backfill_limit=_AUTOSYNC_BACKFILL_MONTHS)
    bookings['attendance'] = attendance
    return bookings


def _resolve_box_url(user: User, box_url: Optional[str] = None) -> Optional[str]:
    if box_url:
        return box_url
    last_booking = (
        db.session.query(Booking)
        .filter_by(user_id=user.id)
        .order_by(Booking.id.desc())
        .first()
    )
    if last_booking and last_booking.url:
        return last_booking.url
    try:
        scraper = get_scraper(user.email, user.cookie)
        return scraper.get_box_url()
    except Exception as e:
        logging.warning('Could not get box URL for user %s: %s', user.email, e)
        return None


def _normalize_reservas_days(raw) -> list:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return raw.get('Dias') or raw.get('dias') or raw.get('Days') or []
    return []


def _monthly_rows_for_user(user_id: int) -> list:
    try:
        return (
            db.session.query(AthleteMonthlyStats)
            .filter_by(user_id=user_id)
            .order_by(AthleteMonthlyStats.year, AthleteMonthlyStats.month)
            .all()
        )
    except OperationalError:
        db.session.rollback()
        return []


def _get_month_row(user_id: int, year: int, month: int) -> AthleteMonthlyStats:
    row = (
        db.session.query(AthleteMonthlyStats)
        .filter_by(user_id=user_id, year=year, month=month)
        .first()
    )
    if row is None:
        row = AthleteMonthlyStats(user_id=user_id, year=year, month=month)
        db.session.add(row)
    return row


def _apply_month_stats(row: AthleteMonthlyStats, stats: MonthStats, source: str) -> None:
    row.booked = stats.booked
    row.attended = stats.attended
    row.no_show = stats.no_show
    row.cancelled = stats.cancelled
    row.source = source
    row.fetched_at = datetime.datetime.now()


def _iter_months_backwards(from_date: datetime.date, count: int):
    y, m = from_date.year, from_date.month
    for _ in range(count):
        yield y, m
        m -= 1
        if m <= 0:
            m = 12
            y -= 1


def _fetch_month_reservas(
    scraper: Scraper,
    box_url: str,
    athlete_id: str,
    year: int,
    month: int,
    now: datetime.datetime,
) -> MonthStats:
    desde, hasta = Scraper.calendar_month_epoch_range(year, month)
    raw = scraper.get_athlete_reservations_range(box_url, athlete_id, desde, hasta)
    records = parse_reservations_payload(_normalize_reservas_days(raw), now=now)
    return aggregate_calendar_month(records, year, month)


def _load_current_period(
    scraper: Scraper,
    box_url: str,
    user: User,
    now: datetime.datetime,
) -> tuple:
    warnings = []
    today = now.date()
    services_data = None
    billing = None

    try:
        services_data = scraper.get_athlete_services(box_url, user.athlete_id)
        billing = parse_billing_period_stats(
            services_data, now=now, year=today.year, month=today.month,
        )
        cal_stats = stats_from_services_for_calendar_month(
            services_data, today.year, today.month, now=now,
        )
        return cal_stats, billing, 'mis_servicios', warnings, services_data
    except (InvalidWodBusterResponse, Exception) as e:
        logging.warning('Master_MisServicios failed for %s: %s', user.email, e)
        warnings.append(
            f'Master_MisServicios no disponible ({e}); '
            'usando reservas del mes (sin canceladas ni cuota de tarifa).',
        )
        cal_stats = _fetch_month_reservas(
            scraper, box_url, user.athlete_id, today.year, today.month, now,
        )
        return cal_stats, None, 'reservas', warnings, None


def _upsert_current_month(
    user: User,
    cal_stats: MonthStats,
    billing: Optional[BillingPeriodStats],
    source: str,
) -> None:
    row = _get_month_row(user.id, cal_stats.year, cal_stats.month)
    _apply_month_stats(row, cal_stats, source)
    if billing:
        row.quota_used = billing.quota_used
        row.quota_total = billing.quota_total
        row.period_from = billing.period_from
        row.period_to = billing.period_to
        row.billing_period_cancelled = billing.cancelled
        row.tariff_name = billing.tariff_name
    else:
        row.quota_used = None
        row.quota_total = None
        row.period_from = None
        row.period_to = None
        row.billing_period_cancelled = None
        row.tariff_name = None


def _month_has_immutable_cache(user_id: int, year: int, month: int, today: datetime.date) -> bool:
    if year == today.year and month == today.month:
        return False
    row = (
        db.session.query(AthleteMonthlyStats)
        .filter_by(user_id=user_id, year=year, month=month)
        .first()
    )
    return row is not None and row.fetched_at is not None and row.source == 'reservas'


def _discover_history_from(
    scraper: Scraper,
    box_url: str,
    athlete_id: str,
    now: datetime.datetime,
) -> datetime.date:
    """Walk backwards until EMPTY_MONTHS_TO_STOP consecutive empty months."""
    today = now.date()
    empty_streak = 0
    earliest = datetime.date(today.year, today.month, 1)
    for y, m in _iter_months_backwards(today, _MAX_HISTORY_MONTHS):
        try:
            stats = _fetch_month_reservas(scraper, box_url, athlete_id, y, m, now)
            if stats.booked == 0 and stats.attended == 0 and stats.no_show == 0:
                empty_streak += 1
                if empty_streak >= _EMPTY_MONTHS_TO_STOP:
                    break
            else:
                empty_streak = 0
                earliest = datetime.date(y, m, 1)
        except Exception as e:
            logging.warning('History discovery failed at %04d-%02d: %s', y, m, e)
            break
    return earliest


def _months_to_backfill(user: User, today: datetime.date, limit: int) -> list:
    history_from = user.attendance_history_from
    if history_from is None:
        history_from = datetime.date(today.year, today.month, 1)

    missing = []
    for y, m in _iter_months_backwards(today, _MAX_HISTORY_MONTHS):
        month_start = datetime.date(y, m, 1)
        if month_start < history_from.replace(day=1):
            break
        if y == today.year and m == today.month:
            continue
        if _month_has_immutable_cache(user.id, y, m, today):
            continue
        missing.append((y, m))
        if limit and len(missing) >= limit:
            break
    return missing


def _backfill_months(
    user: User,
    box_url: str,
    scraper: Scraper,
    now: datetime.datetime,
    limit: int,
) -> int:
    today = now.date()
    months_updated = 0
    for y, m in _months_to_backfill(user, today, limit):
        try:
            stats = _fetch_month_reservas(scraper, box_url, user.athlete_id, y, m, now)
            row = _get_month_row(user.id, y, m)
            _apply_month_stats(row, stats, 'reservas')
            db.session.commit()
            months_updated += 1
        except Exception as e:
            db.session.rollback()
            logging.warning('Backfill failed %04d-%02d for %s: %s', y, m, user.email, e)
    return months_updated


def sync_attendance_stats(
    user: User,
    box_url: Optional[str] = None,
    scraper: Optional[Scraper] = None,
    backfill_limit: int = _AUTOSYNC_BACKFILL_MONTHS,
) -> dict:
    result = {
        'success': False,
        'months_updated': 0,
        'current_period': None,
        'errors': [],
    }
    if not user.athlete_id:
        result['errors'].append('No athlete_id set')
        return result

    box_url = _resolve_box_url(user, box_url)
    if not box_url:
        result['errors'].append('No box URL available')
        return result

    try:
        if scraper is None:
            scraper = get_scraper(user.email, user.cookie)
        now = datetime.datetime.now(_MADRID_TZ)
        today = now.date()

        if user.attendance_history_from is None:
            user.attendance_history_from = _discover_history_from(
                scraper, box_url, user.athlete_id, now,
            )
            db.session.commit()

        cal_stats, billing, source, warnings, _ = _load_current_period(
            scraper, box_url, user, now,
        )
        result['errors'].extend(warnings)
        _upsert_current_month(user, cal_stats, billing, source)
        result['current_period'] = {
            'booked': cal_stats.booked,
            'attended': cal_stats.attended,
            'no_show': cal_stats.no_show,
            'cancelled': cal_stats.cancelled,
            'source': source,
        }
        if billing:
            result['current_period'].update({
                'quota_used': billing.quota_used,
                'quota_total': billing.quota_total,
                'usage_pct': billing.usage_pct,
                'billing_cancelled': billing.cancelled,
            })
        db.session.commit()

        result['months_updated'] = _backfill_months(
            user, box_url, scraper, now, backfill_limit,
        )
        result['success'] = True
    except Exception as e:
        db.session.rollback()
        logging.warning('Attendance sync failed: %s', e, exc_info=True)
        result['errors'].append(f'Attendance sync failed: {e}')

    return result


def manual_backfill_history(user: User, box_url: Optional[str] = None) -> dict:
    """Fetch up to _MANUAL_BACKFILL_MONTHS_PER_RUN missing historical months."""
    if not user.athlete_id:
        return {'success': False, 'error': 'No athlete_id set'}

    box_url = _resolve_box_url(user, box_url)
    if not box_url:
        return {'success': False, 'error': 'No box URL available'}

    scraper = get_scraper(user.email, user.cookie)
    now = datetime.datetime.now(_MADRID_TZ)
    today = now.date()

    if user.attendance_history_from is None:
        user.attendance_history_from = _discover_history_from(
            scraper, box_url, user.athlete_id, now,
        )
        db.session.commit()

    months_updated = _backfill_months(
        user, box_url, scraper, now, _MANUAL_BACKFILL_MONTHS_PER_RUN,
    )
    remaining = len(_months_to_backfill(user, today, limit=9999))

    return {
        'success': True,
        'months_filled': months_updated,
        'months_remaining': remaining,
        'complete': remaining == 0,
        'earliest_month': (
            user.attendance_history_from.isoformat()
            if user.attendance_history_from else None
        ),
    }


def regenerate_history(
    user: User,
    include_current: bool = False,
) -> dict:
    """Delete cached history and clear signup bound (optional current month)."""
    today = datetime.date.today()
    query = db.session.query(AthleteMonthlyStats).filter_by(user_id=user.id)
    if include_current:
        query.delete()
    else:
        rows = query.all()
        for row in rows:
            if row.year == today.year and row.month == today.month:
                continue
            db.session.delete(row)
    user.attendance_history_from = None
    db.session.commit()
    return {'success': True, 'include_current': include_current}


def _empty_dashboard(message: Optional[str] = None) -> dict:
    return {
        'needs_sync': True,
        'calendar_month': {
            'label': month_label_es(datetime.date.today().year, datetime.date.today().month),
            'booked': 0, 'attended': 0, 'no_show': 0, 'cancelled': 0,
            'summary_line': message or 'Pulsa Sync WodBuster para cargar tus estadísticas.',
        },
        'billing_period': None,
        'quota_mood': {'label': 'Tu asistencia', 'usage_pct': 0, 'quota_used': 0, 'quota_total': 0},
        'yoy_mood': {'message': '', 'detail': '', 'has_comparison': False},
        'stale': False,
        'stale_message': None,
    }


def _build_calendar_block(stats: MonthStats) -> dict:
    label = month_label_es(stats.year, stats.month)
    summary = (
        f'En {month_name_es(stats.month)}: '
        f'Asistidas {stats.attended} · Reservadas {stats.booked}'
    )
    block = {
        'label': label,
        'year': stats.year,
        'month': stats.month,
        'booked': stats.booked,
        'attended': stats.attended,
        'no_show': stats.no_show,
        'cancelled': stats.cancelled,
        'summary_line': summary,
    }
    if stats.no_show:
        block['no_show_line'] = f'No te has presentado a {stats.no_show} clases'
    if stats.cancelled:
        block['cancelled_line'] = f'Canceladas en el mes: {stats.cancelled}'
    return block


def _build_billing_block(billing: BillingPeriodStats) -> dict:
    period_str = format_period_range(billing.period_from, billing.period_to)
    tariff = billing.tariff_name or 'tarifa'
    credits_line = (
        f'Créditos del periodo ({tariff}): '
        f'{billing.quota_used}/{billing.quota_total} usados'
    )
    if period_str:
        credits_line += f' · {period_str}'
    block = {
        'from': billing.period_from.isoformat() if billing.period_from else None,
        'to': billing.period_to.isoformat() if billing.period_to else None,
        'quota_used': billing.quota_used,
        'quota_total': billing.quota_total,
        'usage_pct': round(billing.usage_pct * 100, 1),
        'cancelled': billing.cancelled,
        'tariff_name': billing.tariff_name,
        'credits_line': credits_line,
    }
    if billing.cancelled:
        block['cancelled_line'] = (
            f'En tu periodo de facturación: {billing.cancelled} cancelaciones'
        )
    return block


def get_attendance_dashboard(user: User) -> Optional[dict]:
    if not user.athlete_id:
        return None

    rows = _monthly_rows_for_user(user.id)
    if not rows:
        return _empty_dashboard()

    today = datetime.date.today()
    current_row = next(
        (r for r in rows if r.year == today.year and r.month == today.month),
        None,
    )
    if current_row is None:
        return _empty_dashboard('Aún no hay datos del mes actual. Sincroniza con WodBuster.')

    cal_stats = MonthStats(
        year=current_row.year,
        month=current_row.month,
        booked=current_row.booked or 0,
        attended=current_row.attended or 0,
        no_show=current_row.no_show or 0,
        cancelled=current_row.cancelled or 0,
    )
    billing = None
    if current_row.quota_total:
        usage = (current_row.quota_used or 0) / current_row.quota_total
        billing = BillingPeriodStats(
            period_from=current_row.period_from,
            period_to=current_row.period_to,
            tariff_name=current_row.tariff_name,
            quota_used=current_row.quota_used or 0,
            quota_total=current_row.quota_total,
            usage_pct=usage,
            cancelled=current_row.billing_period_cancelled or 0,
        )

    quota_mood = compute_quota_mood(
        billing.usage_pct if billing else 0,
        billing.quota_used if billing else 0,
        billing.quota_total if billing else 0,
    )

    monthly_for_yoy = [
        {'year': r.year, 'month': r.month, 'attended': r.attended or 0}
        for r in rows if r.source == 'reservas'
    ]
    if current_row.source in ('mis_servicios', 'reservas'):
        if not any(
            r['year'] == today.year and r['month'] == today.month
            for r in monthly_for_yoy
        ):
            monthly_for_yoy.append({
                'year': today.year,
                'month': today.month,
                'attended': current_row.attended or 0,
            })
    yoy = compute_ytd_yoy_mood(monthly_for_yoy, today=today)

    latest = max((r.fetched_at for r in rows if r.fetched_at), default=None)
    stale = (
        latest is None
        or latest < datetime.datetime.now() - datetime.timedelta(days=7)
    )

    dash = {
        'needs_sync': False,
        'calendar_month': _build_calendar_block(cal_stats),
        'billing_period': _build_billing_block(billing) if billing else None,
        'quota_mood': {
            'label': quota_mood.label,
            'usage_pct': round(quota_mood.usage_pct * 100, 1),
            'quota_used': quota_mood.quota_used,
            'quota_total': quota_mood.quota_total,
            'subtitle': 'Según créditos del periodo de facturación, no clases reservadas',
        },
        'yoy_mood': {
            'message': yoy.message,
            'detail': yoy.detail,
            'has_comparison': yoy.has_comparison,
        },
        'stale': stale,
        'stale_message': 'Datos desactualizados' if stale else None,
    }
    return dash


def _count_expected_history_months(user: User, today: datetime.date) -> int:
    if not user.attendance_history_from:
        return 0
    count = 0
    start = user.attendance_history_from.replace(day=1)
    y, m = today.year, today.month
    while datetime.date(y, m, 1) >= start:
        count += 1
        m -= 1
        if m <= 0:
            m = 12
            y -= 1
    return max(0, count - 1)


def get_attendance_history(user: User) -> Optional[dict]:
    if not user.athlete_id:
        return None

    rows = _monthly_rows_for_user(user.id)
    today = datetime.date.today()

    if not rows:
        return {
            'months': [],
            'chart': {'labels': [], 'attended': [], 'booked': [], 'prev_year_attended': []},
            'incomplete': True,
            'months_remaining': 0,
            'stale': True,
        }

    attended_by_ym = {(r.year, r.month): r.attended or 0 for r in rows}
    history_from = user.attendance_history_from
    if history_from:
        start = history_from.replace(day=1)
    else:
        start = datetime.date(rows[0].year, rows[0].month, 1)

    series = []
    y, m = today.year, today.month
    while datetime.date(y, m, 1) >= start:
        row = next((r for r in rows if r.year == y and r.month == m), None)
        label = f'{y:04d}-{m:02d}'
        prev_attended = attended_by_ym.get((y - 1, m), 0)
        if row:
            series.append({
                'label': label,
                'year': y,
                'month': m,
                'booked': row.booked or 0,
                'attended': row.attended or 0,
                'no_show': row.no_show or 0,
                'cancelled': row.cancelled if row.cancelled is not None else None,
                'prev_year_attended': prev_attended,
                'fetched_at': row.fetched_at.isoformat() if row.fetched_at else None,
            })
        else:
            series.append({
                'label': label,
                'year': y,
                'month': m,
                'booked': 0,
                'attended': 0,
                'no_show': 0,
                'cancelled': None,
                'prev_year_attended': prev_attended,
                'fetched_at': None,
            })
        m -= 1
        if m <= 0:
            m = 12
            y -= 1

    series.reverse()
    cached_past = sum(
        1 for s in series
        if not (s['year'] == today.year and s['month'] == today.month)
        and s['fetched_at']
    )
    expected_past = _count_expected_history_months(user, today)
    months_remaining = max(0, expected_past - cached_past)

    latest = max((r.fetched_at for r in rows if r.fetched_at), default=None)
    stale = (
        latest is None
        or latest < datetime.datetime.now() - datetime.timedelta(days=7)
    )

    chart = {
        'labels': [s['label'] for s in series],
        'attended': [s['attended'] for s in series],
        'booked': [s['booked'] for s in series],
        'prev_year_attended': [s['prev_year_attended'] for s in series],
    }

    return {
        'months': series,
        'chart': chart,
        'incomplete': months_remaining > 0,
        'months_remaining': months_remaining,
        'stale': stale,
        'earliest_month': history_from.isoformat() if history_from else None,
    }


def dashboard_to_api(user: User) -> dict:
    dash = get_attendance_dashboard(user)
    if dash is None:
        return {'available': False}
    return {'available': True, **dash}
