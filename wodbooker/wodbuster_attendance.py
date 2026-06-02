"""
Parse WodBuster Master_MisServicios / Master_MisServicios_Reservas payloads.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import pytz

_MADRID_TZ = pytz.timezone('Europe/Madrid')
_UTC_TZ = pytz.timezone('UTC')

_QUOTA_MOOD_TIERS = (
    # TODO: we need to replace with image/gif (maybe use a library to handle this)
    (0.90, '¡Estás on fire!'),
    (0.75, 'Este mes vas muy bien, ¡sigue así!'),
    (0.50, 'No vas mal este mes, ¡ánimo!'),
    (0.0, 'Por ahora no es tu mejor mes'),
)

_YOY_MSG_BETTER = '¡Lo estás haciendo mejor que el año pasado! ¡Sigue así!'
_YOY_MSG_WORSE = 'Vas por debajo del año pasado, sabemos que no nos apetece ir al box, pero ¡ánimo!'
_YOY_MSG_EQUAL = 'Vas igual que el año pasado, ¡buen ritmo!'
_YOY_MSG_NO_DATA = 'Aún no tenemos datos del año pasado para comparar'

_MONTH_NAMES_ES = (
    '', 'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
)


@dataclass
class ClassAttendanceRecord:
    class_datetime: datetime.datetime
    attended: bool
    no_show: bool
    cancelled: bool
    booked: bool


@dataclass
class MonthStats:
    year: int
    month: int
    booked: int = 0
    attended: int = 0
    no_show: int = 0
    cancelled: int = 0


@dataclass
class CurrentPeriodStats:
    period_from: Optional[datetime.date] = None
    period_to: Optional[datetime.date] = None
    tariff_name: Optional[str] = None
    booked: int = 0
    attended: int = 0
    no_show: int = 0
    cancelled: int = 0
    quota_used: int = 0
    quota_total: int = 0
    usage_pct: float = 0.0
    records: list = field(default_factory=list)


@dataclass
class BillingPeriodStats:
    period_from: Optional[datetime.date] = None
    period_to: Optional[datetime.date] = None
    tariff_name: Optional[str] = None
    quota_used: int = 0
    quota_total: Optional[int] = None
    usage_pct: float = 0.0
    cancelled: int = 0


@dataclass
class QuotaMood:
    label: str
    usage_pct: float
    quota_used: int
    quota_total: int


@dataclass
class YoYMoodResult:
    message: str
    detail: str
    ytd_attended_current: int
    ytd_attended_previous: int
    has_comparison: bool


def month_name_es(month: int) -> str:
    if 1 <= month <= 12:
        return _MONTH_NAMES_ES[month]
    return str(month)


def month_label_es(year: int, month: int) -> str:
    return f'{month_name_es(month)} {year}'


def _epoch_to_date(epoch: Any) -> Optional[datetime.date]:
    if epoch is None:
        return None
    if isinstance(epoch, (int, float)):
        return datetime.datetime.fromtimestamp(epoch, tz=_UTC_TZ).date()
    return None


def _parse_class_datetime(fecha: Any, hora: Any) -> Optional[datetime.datetime]:
    if fecha is None:
        return None
    d = _epoch_to_date(fecha)
    if d is None and isinstance(fecha, str):
        try:
            d = datetime.date.fromisoformat(fecha[:10])
        except ValueError:
            return None
    elif d is None and isinstance(fecha, datetime.datetime):
        d = fecha.date()
    elif d is None and isinstance(fecha, datetime.date):
        d = fecha
    elif d is None:
        return None

    hour, minute = 0, 0
    if hora:
        m = re.match(r'(\d{1,2}):(\d{2})', str(hora).strip())
        if m:
            hour, minute = int(m.group(1)), int(m.group(2))
    naive = datetime.datetime.combine(d, datetime.time(hour, minute))
    return _MADRID_TZ.localize(naive)


def _classify_clase(clase: dict, now: datetime.datetime) -> ClassAttendanceRecord:
    borrada = bool(clase.get('Borrada', False))
    perdida = bool(clase.get('Perdida', False))
    dt = _parse_class_datetime(clase.get('Fecha'), clase.get('Hora'))
    if dt is None:
        dt = now
    is_past = dt < now
    if borrada:
        return ClassAttendanceRecord(
            class_datetime=dt, attended=False, no_show=False,
            cancelled=True, booked=False,
        )
    if perdida:
        return ClassAttendanceRecord(
            class_datetime=dt, attended=False, no_show=True,
            cancelled=False, booked=True,
        )
    if is_past:
        return ClassAttendanceRecord(
            class_datetime=dt, attended=True, no_show=False,
            cancelled=False, booked=True,
        )
    return ClassAttendanceRecord(
        class_datetime=dt, attended=False, no_show=False,
        cancelled=False, booked=True,
    )


def _clase_with_day_fecha(day_fecha: Any, clase: dict) -> dict:
    merged = dict(clase)
    if day_fecha is not None and merged.get('Fecha') is None:
        merged['Fecha'] = day_fecha
    return merged


def _iter_clases_from_services(data: dict) -> list:
    clases = []
    for reserva in data.get('Reservas') or []:
        day_fecha = reserva.get('Fecha')
        for clase in reserva.get('Clases') or []:
            clases.append(_clase_with_day_fecha(day_fecha, clase))
    return clases


def _iter_clases_from_reservations(days: list) -> list:
    clases = []
    for day in days or []:
        day_fecha = day.get('Fecha')
        for clase in day.get('Clases') or []:
            clases.append(_clase_with_day_fecha(day_fecha, clase))
    return clases


def _parse_period_dates(data: dict) -> tuple:
    period_from = period_to = None
    for key_from, key_to in (
        ('PeriodoActualDesde', 'PeriodoActualHasta'),
        ('Desde', 'Hasta'),
        ('PeriodoDesde', 'PeriodoHasta'),
    ):
        raw_from = data.get(key_from)
        raw_to = data.get(key_to)
        period_from = _epoch_to_date(raw_from)
        period_to = _epoch_to_date(raw_to)
        if period_from is None and raw_from:
            try:
                period_from = datetime.date.fromisoformat(str(raw_from)[:10])
            except ValueError:
                pass
        if period_to is None and raw_to:
            try:
                period_to = datetime.date.fromisoformat(str(raw_to)[:10])
            except ValueError:
                pass
        if period_from or period_to:
            break
    return period_from, period_to


def _quota_from_tarifa_desc(tarifa_desc: list) -> tuple:
    quota_total = 0
    quota_used = 0
    for group in tarifa_desc or []:
        reservas = group.get('Reservas') or {}
        quota_total += int(reservas.get('DeTarifa') or 0)
        quota_used += int(reservas.get('DeUsuario') or 0)
    return quota_used, quota_total


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    # Accept common numeric formats from WodBuster payloads.
    text = text.replace(',', '.')
    m = re.search(r'-?\d+(?:\.\d+)?', text)
    if not m:
        return None
    try:
        return int(float(m.group(0)))
    except (TypeError, ValueError):
        return None


def _first_dict_value(data: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in data and data.get(key) is not None:
            return data.get(key)
    return None


def _normalize_training_type_id(raw: Any) -> Optional[int]:
    return _to_int(raw)


def _extract_class_training_type_id(clase: dict) -> Optional[int]:
    raw = _first_dict_value(
        clase,
        (
            'IdTipoEntrenamiento',
            'IdEntrenamiento',
            'EntrenamientoId',
            'IdTipo',
            'TipoEntrenamientoId',
        ),
    )
    if raw is None:
        entrenamiento = clase.get('Entrenamiento') or clase.get('Training') or {}
        if isinstance(entrenamiento, dict):
            raw = _first_dict_value(
                entrenamiento,
                ('Id', 'ID', 'id', 'IdEntrenamiento', 'IdTipoEntrenamiento'),
            )
    return _normalize_training_type_id(raw)


def _extract_group_training_type_ids(group: dict) -> set[int]:
    ids: set[int] = set()
    entrenamientos = (
        group.get('Entrenamientos')
        or group.get('TiposEntrenamiento')
        or group.get('TrainingTypes')
        or []
    )
    if not isinstance(entrenamientos, list):
        entrenamientos = []
    for item in entrenamientos:
        if isinstance(item, dict):
            raw = _first_dict_value(
                item,
                ('Id', 'ID', 'id', 'IdEntrenamiento', 'IdTipoEntrenamiento'),
            )
        else:
            raw = item
        normalized = _normalize_training_type_id(raw)
        if normalized is not None:
            ids.add(normalized)
    return ids


def _quota_for_calendar_month(
    data: dict,
    year: int,
    month: int,
    now: Optional[datetime.datetime] = None,
) -> tuple:
    """Compute credits using only training types booked in the given calendar month.

    credits_total = sum of DeTarifa for TarifaDesc groups whose Entrenamientos
                    intersect the booked IdTipoEntrenamiento set.
    credits_used  = count of non-cancelled booked classes in the month.
    """
    if now is None:
        now = datetime.datetime.now(_MADRID_TZ)

    clases = _iter_clases_from_services(data)
    booked_type_ids: set[int] = set()
    credits_used = 0
    for clase in clases:
        dt = _parse_class_datetime(clase.get('Fecha'), clase.get('Hora'))
        if dt is None:
            continue
        if dt.year != year or dt.month != month:
            continue
        if bool(clase.get('Borrada', False)):
            continue
        tipo_id = _extract_class_training_type_id(clase)
        if tipo_id is not None:
            booked_type_ids.add(tipo_id)
        credits_used += 1

    credits_total = 0
    matched_group = False
    for group in data.get('TarifaDesc') or []:
        group_ids = _extract_group_training_type_ids(group)
        if group_ids & booked_type_ids:
            matched_group = True
            reservas = group.get('Reservas') or {}
            quota_group = _to_int(_first_dict_value(reservas, ('DeTarifa', 'Tarifa', 'Total')))
            if quota_group is not None:
                credits_total += quota_group

    return credits_used, (credits_total if matched_group else None)


def filter_records_to_calendar_month(
    records: list,
    year: int,
    month: int,
) -> list:
    return [
        r for r in records
        if r.class_datetime.year == year and r.class_datetime.month == month
    ]


def records_to_month_stats(records: list, year: int, month: int) -> MonthStats:
    stats = MonthStats(year=year, month=month)
    for rec in records:
        if rec.cancelled:
            stats.cancelled += 1
        elif rec.no_show:
            stats.no_show += 1
            stats.booked += 1
        elif rec.attended:
            stats.attended += 1
            stats.booked += 1
        elif rec.booked:
            stats.booked += 1
    return stats


def parse_services_payload(
    data: dict,
    now: Optional[datetime.datetime] = None,
) -> CurrentPeriodStats:
    """Full billing-period parse (all classes in Master_MisServicios)."""
    if now is None:
        now = datetime.datetime.now(_MADRID_TZ)
    period_from, period_to = _parse_period_dates(data)
    tariff_name = data.get('Tarifa') or data.get('TarifaNombre') or data.get('NombreTarifa')
    quota_used, quota_total = _quota_from_tarifa_desc(data.get('TarifaDesc') or [])
    usage_pct = (quota_used / quota_total) if quota_total else 0.0

    stats = CurrentPeriodStats(
        period_from=period_from,
        period_to=period_to,
        tariff_name=tariff_name,
        quota_used=quota_used,
        quota_total=quota_total,
        usage_pct=usage_pct,
    )
    for clase in _iter_clases_from_services(data):
        rec = _classify_clase(clase, now)
        stats.records.append(rec)
        if rec.cancelled:
            stats.cancelled += 1
        elif rec.no_show:
            stats.no_show += 1
            stats.booked += 1
        elif rec.attended:
            stats.attended += 1
            stats.booked += 1
        elif rec.booked:
            stats.booked += 1
    return stats


def parse_billing_period_stats(
    data: dict,
    now: Optional[datetime.datetime] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
) -> BillingPeriodStats:
    """Quota and billing-period cancellations only.

    When year/month are supplied, credits are scoped to training types booked
    in that calendar month (credits_used from CosteTarifa, credits_total from
    matching TarifaDesc groups).  Otherwise falls back to raw TarifaDesc sums.
    """
    full = parse_services_payload(data, now=now)
    if year is not None and month is not None:
        quota_used, quota_total = _quota_for_calendar_month(data, year, month, now=now)
    else:
        quota_used, quota_total = full.quota_used, full.quota_total
    usage_pct = (quota_used / quota_total) if quota_total else 0.0
    return BillingPeriodStats(
        period_from=full.period_from,
        period_to=full.period_to,
        tariff_name=full.tariff_name,
        quota_used=quota_used,
        quota_total=quota_total,
        usage_pct=usage_pct,
        cancelled=full.cancelled,
    )


def stats_from_services_for_calendar_month(
    data: dict,
    year: int,
    month: int,
    now: Optional[datetime.datetime] = None,
) -> MonthStats:
    full = parse_services_payload(data, now=now)
    filtered = filter_records_to_calendar_month(full.records, year, month)
    return records_to_month_stats(filtered, year, month)


def parse_reservations_payload(
    days: list,
    now: Optional[datetime.datetime] = None,
) -> list:
    if now is None:
        now = datetime.datetime.now(_MADRID_TZ)
    records = []
    for clase in _iter_clases_from_reservations(days):
        records.append(_classify_clase(clase, now))
    return records


def aggregate_calendar_month(
    records: list,
    year: int,
    month: int,
) -> MonthStats:
    filtered = filter_records_to_calendar_month(records, year, month)
    return records_to_month_stats(filtered, year, month)


def quota_mood_from_usage(usage_pct: float) -> str:
    for threshold, label in _QUOTA_MOOD_TIERS:
        if usage_pct >= threshold:
            return label
    return _QUOTA_MOOD_TIERS[-1][1]


def compute_quota_mood(usage_pct: float, quota_used: int, quota_total: int) -> QuotaMood:
    return QuotaMood(
        label=quota_mood_from_usage(usage_pct),
        usage_pct=usage_pct,
        quota_used=quota_used,
        quota_total=quota_total,
    )


def _ytd_month_range_label(month: int) -> str:
    if month <= 1:
        return _MONTH_NAMES_ES[1]
    return f'{_MONTH_NAMES_ES[1]}–{_MONTH_NAMES_ES[month]}'


def compute_ytd_yoy_mood(
    monthly_rows: list,
    today: Optional[datetime.date] = None,
) -> YoYMoodResult:
    if today is None:
        today = datetime.date.today()

    def _get(row, key):
        if isinstance(row, dict):
            return row.get(key, 0)
        return getattr(row, key, 0)

    current_year = today.year
    prev_year = current_year - 1
    max_month = today.month

    ytd_current = sum(
        _get(r, 'attended') for r in monthly_rows
        if _get(r, 'year') == current_year and _get(r, 'month') <= max_month
    )
    prev_rows = [
        r for r in monthly_rows
        if _get(r, 'year') == prev_year and _get(r, 'month') <= max_month
    ]
    ytd_previous = sum(_get(r, 'attended') for r in prev_rows)
    range_label = _ytd_month_range_label(max_month)
    detail = f'{ytd_current} clases vs {ytd_previous} el año pasado ({range_label})'

    if not prev_rows:
        return YoYMoodResult(
            message=_YOY_MSG_NO_DATA,
            detail=detail,
            ytd_attended_current=ytd_current,
            ytd_attended_previous=ytd_previous,
            has_comparison=False,
        )
    if ytd_current > ytd_previous:
        message = _YOY_MSG_BETTER
    elif ytd_current < ytd_previous:
        message = _YOY_MSG_WORSE
    else:
        message = _YOY_MSG_EQUAL

    return YoYMoodResult(
        message=message,
        detail=detail,
        ytd_attended_current=ytd_current,
        ytd_attended_previous=ytd_previous,
        has_comparison=True,
    )


def format_period_range(period_from: Optional[datetime.date], period_to: Optional[datetime.date]) -> str:
    if period_from and period_to:
        return f'{period_from.strftime("%d/%m")}–{period_to.strftime("%d/%m")}'
    if period_from:
        return f'desde {period_from.strftime("%d/%m/%Y")}'
    return ''
