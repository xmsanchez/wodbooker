import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, date
from typing import Optional, Tuple, List, Dict, Any, Union
import unicodedata
from urllib.parse import urlparse, quote_plus
import pytz
import requests

from .constants import DAYS_OF_WEEK
from .models import Booking, db

_MADRID_TZ = pytz.timezone('Europe/Madrid')

# In-memory forecast cache: (round(lat, 2), round(lon, 2)) -> (timestamp, data_dict)
_FORECAST_CACHE: Dict[Tuple[float, float], Tuple[float, Dict[str, Any]]] = {}
_CACHE_TTL = 3600  # 1 hour

# In-memory geocoding cache: query_lower -> (timestamp, (lat, lon, display_name))
_GEOCODE_CACHE: Dict[str, Tuple[float, Optional[Tuple[float, float, str]]]] = {}
_GEOCODE_CACHE_TTL = 86400  # 24 hours

# WMO Weather interpretation codes (WW) mapping
# https://open-meteo.com/en/docs
_WMO_WEATHER_MAP: Dict[int, Dict[str, str]] = {
    0: {'icon': 'bi-sun-fill', 'color': '#f59e0b', 'label': 'Despejado'},
    1: {'icon': 'bi-cloud-sun-fill', 'color': '#f59e0b', 'label': 'Mayormente despejado'},
    2: {'icon': 'bi-cloud-sun-fill', 'color': '#0ea5e9', 'label': 'Parcialmente nublado'},
    3: {'icon': 'bi-cloud-fill', 'color': '#64748b', 'label': 'Nublado'},
    45: {'icon': 'bi-cloud-fog2', 'color': '#94a3b8', 'label': 'Niebla'},
    48: {'icon': 'bi-cloud-fog2', 'color': '#94a3b8', 'label': 'Niebla con escarcha'},
    51: {'icon': 'bi-cloud-drizzle-fill', 'color': '#38bdf8', 'label': 'Llovizna ligera'},
    53: {'icon': 'bi-cloud-drizzle-fill', 'color': '#0284c7', 'label': 'Llovizna moderada'},
    55: {'icon': 'bi-cloud-drizzle-fill', 'color': '#0369a1', 'label': 'Llovizna intensa'},
    56: {'icon': 'bi-cloud-snow-fill', 'color': '#06b6d4', 'label': 'Llovizna helada'},
    57: {'icon': 'bi-cloud-snow-fill', 'color': '#06b6d4', 'label': 'Llovizna helada intensa'},
    61: {'icon': 'bi-cloud-rain-fill', 'color': '#38bdf8', 'label': 'Lluvia ligera'},
    63: {'icon': 'bi-cloud-rain-fill', 'color': '#0284c7', 'label': 'Lluvia moderada'},
    65: {'icon': 'bi-cloud-rain-heavy-fill', 'color': '#1d4ed8', 'label': 'Lluvia fuerte'},
    66: {'icon': 'bi-cloud-snow-fill', 'color': '#06b6d4', 'label': 'Lluvia helada'},
    67: {'icon': 'bi-cloud-snow-fill', 'color': '#06b6d4', 'label': 'Lluvia helada fuerte'},
    71: {'icon': 'bi-snow', 'color': '#06b6d4', 'label': 'Nevada ligera'},
    73: {'icon': 'bi-snow', 'color': '#0284c7', 'label': 'Nevada moderada'},
    75: {'icon': 'bi-snow', 'color': '#1d4ed8', 'label': 'Nevada fuerte'},
    77: {'icon': 'bi-snow', 'color': '#06b6d4', 'label': 'Granizo menudo'},
    80: {'icon': 'bi-cloud-rain-fill', 'color': '#38bdf8', 'label': 'Chubascos ligeros'},
    81: {'icon': 'bi-cloud-rain-fill', 'color': '#0284c7', 'label': 'Chubascos moderados'},
    82: {'icon': 'bi-cloud-rain-heavy-fill', 'color': '#1d4ed8', 'label': 'Chubascos violentos'},
    85: {'icon': 'bi-cloud-snow-fill', 'color': '#06b6d4', 'label': 'Chubascos de nieve'},
    86: {'icon': 'bi-cloud-snow-fill', 'color': '#1d4ed8', 'label': 'Chubascos de nieve fuertes'},
    95: {'icon': 'bi-cloud-lightning-rain-fill', 'color': '#8b5cf6', 'label': 'Tormenta'},
    96: {'icon': 'bi-cloud-lightning-rain-fill', 'color': '#7c3aed', 'label': 'Tormenta con granizo ligero'},
    99: {'icon': 'bi-cloud-lightning-rain-fill', 'color': '#6d28d9', 'label': 'Tormenta con granizo fuerte'},
}

_DEFAULT_WEATHER_INFO = {'icon': 'bi-cloud-sun', 'color': '#64748b', 'label': 'Variable'}


def get_weather_info_for_code(code: Optional[int]) -> Dict[str, str]:
    """Return icon, color, and label for a WMO weather code."""
    if code is not None and code in _WMO_WEATHER_MAP:
        return _WMO_WEATHER_MAP[code]
    return _DEFAULT_WEATHER_INFO


def geocode_location(query: str) -> Optional[Tuple[float, float, str]]:
    """
    Search coordinates for a city or location name using Open-Meteo Geocoding API.
    Returns (latitude, longitude, display_name) or None if not found or on error.
    """
    if not query or not query.strip():
        return None

    clean_query = query.strip().lower()
    now = time.time()
    if clean_query in _GEOCODE_CACHE:
        cached_time, cached_val = _GEOCODE_CACHE[clean_query]
        if now - cached_time < _GEOCODE_CACHE_TTL:
            return cached_val

    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {
        'name': query.strip(),
        'count': 1,
        'language': 'es',
        'format': 'json',
    }

    try:
        resp = requests.get(url, params=params, timeout=2.5)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get('results')
            if results and len(results) > 0:
                first = results[0]
                lat = float(first['latitude'])
                lon = float(first['longitude'])
                display_name = first.get('name', query.strip())
                res = (lat, lon, display_name)
                _GEOCODE_CACHE[clean_query] = (now, res)
                return res
        _GEOCODE_CACHE[clean_query] = (now, None)
    except Exception as e:
        logging.warning("Error geocoding location '%s': %s", query, e)

    return None


# Known box URL/subdomain mappings to default weather cities
KNOWN_BOX_CITY_MAPPINGS: Dict[str, str] = {
    'corbera': 'Corbera de Llobregat',
    'corbera.wodbuster.com': 'Corbera de Llobregat',
    'https://corbera.wodbuster.com': 'Corbera de Llobregat',
}


def get_default_city_for_box(box_url_or_subdomain: Optional[str]) -> Optional[str]:
    """
    Get the default weather city for a known box URL or subdomain.
    E.g. 'https://corbera.wodbuster.com' or 'corbera' -> 'Corbera de Llobregat'.
    """
    if not box_url_or_subdomain:
        return None
    cleaned = box_url_or_subdomain.strip().lower().rstrip('/')
    if cleaned in KNOWN_BOX_CITY_MAPPINGS:
        return KNOWN_BOX_CITY_MAPPINGS[cleaned]
    sub = extract_box_subdomain(box_url_or_subdomain)
    if sub and sub.lower() in KNOWN_BOX_CITY_MAPPINGS:
        return KNOWN_BOX_CITY_MAPPINGS[sub.lower()]
    return None


def extract_box_subdomain(box_url: Optional[str]) -> Optional[str]:
    """Extract subdomain from box URL (e.g., https://corbera.wodbuster.com -> corbera)."""
    if not box_url:
        return None
    try:
        parsed = urlparse(box_url)
        hostname = parsed.hostname or ''
        parts = hostname.split('.')
        if len(parts) >= 3 and parts[-2] == 'wodbuster':
            sub = parts[0]
            if sub not in ('www', 'app', 'account', 'cdn'):
                return sub
    except Exception:
        pass
    return None


def resolve_user_coordinates(user, bookings: Optional[List[Any]] = None) -> Tuple[float, float, str]:
    """
    Resolve coordinates for a user:
    1. Saved user lat/lon
    2. Geocode user.weather_city
    3. Infer from box URL subdomain (with KNOWN_BOX_CITY_MAPPINGS)
    4. Fallback to Madrid
    """
    if getattr(user, 'weather_lat', None) is not None and getattr(user, 'weather_lon', None) is not None:
        return (user.weather_lat, user.weather_lon, user.weather_city or "Ubicación")

    if getattr(user, 'weather_city', None) and user.weather_city.strip():
        geocoded = geocode_location(user.weather_city.strip())
        if geocoded:
            try:
                user.weather_lat = geocoded[0]
                user.weather_lon = geocoded[1]
                db.session.commit()
            except Exception:
                db.session.rollback()
            return geocoded

    # Try to infer from booking box_url
    box_subdomain = None
    box_url = None
    if bookings:
        for b in bookings:
            u = getattr(b, 'url', None)
            if u:
                box_url = u
                box_subdomain = extract_box_subdomain(u)
                if box_subdomain or box_url:
                    break

    if not box_url and getattr(user, 'id', None):
        last_b = Booking.query.filter_by(user_id=user.id).order_by(Booking.id.desc()).first()
        if last_b:
            box_url = last_b.url
            box_subdomain = extract_box_subdomain(last_b.url)

    target_city = get_default_city_for_box(box_url) or get_default_city_for_box(box_subdomain) or box_subdomain

    if target_city:
        geocoded = geocode_location(target_city)
        if geocoded:
            try:
                user.weather_city = target_city
                user.weather_lat = geocoded[0]
                user.weather_lon = geocoded[1]
                db.session.commit()
            except Exception:
                db.session.rollback()
            return geocoded

    # Fallback to Madrid
    return (40.4168, -3.7038, "Madrid")


def get_weather_forecast(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Fetch 14-day hourly forecast from Open-Meteo with caching.
    Uses the ECMWF IFS model (European Centre for Medium-Range Weather Forecasts),
    the gold standard used across Spain and Europe (AEMET, Meteocat), with fallbacks.
    """
    cache_key = (round(lat, 2), round(lon, 2))
    now = time.time()

    if cache_key in _FORECAST_CACHE:
        cached_time, cached_data = _FORECAST_CACHE[cache_key]
        if now - cached_time < _CACHE_TTL:
            return cached_data

    url = "https://api.open-meteo.com/v1/forecast"
    base_params = {
        'latitude': lat,
        'longitude': lon,
        'hourly': 'temperature_2m,precipitation_probability,precipitation,weather_code',
        'daily': 'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum',
        'timezone': 'Europe/Madrid',
        'forecast_days': 14,
    }

    # Primary attempt: ECMWF IFS (High-Resolution European model used by AEMET / Meteocat)
    for model_name in ('ecmwf_ifs', 'ecmwf_ifs025'):
        try:
            resp = requests.get(url, params={**base_params, 'models': model_name}, timeout=3.0)
            if resp.status_code == 200:
                data = resp.json()
                _FORECAST_CACHE[cache_key] = (now, data)
                return data
            logging.warning("Open-Meteo with %s returned status %s: %s", model_name, resp.status_code, resp.text[:200])
        except Exception as e:
            logging.warning("Error fetching %s forecast from Open-Meteo: %s", model_name, e)

    # Fallback attempt: default best_match model
    try:
        resp = requests.get(url, params=base_params, timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            _FORECAST_CACHE[cache_key] = (now, data)
            return data
        logging.warning("Open-Meteo default model returned status %s: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logging.warning("Error fetching default weather forecast from Open-Meteo: %s", e)

    return None


def format_precipitation_sum(precip: Optional[float]) -> str:
    """Format precipitation sum in mm (e.g. '0 mm', '1.3 mm', '12 mm')."""
    if precip is None or precip < 0.05:
        return '0 mm'
    rounded = round(precip, 1)
    if rounded == int(rounded):
        return f"{int(rounded)} mm"
    return f"{rounded:.1f} mm"


def format_hourly_precipitation(precip: Optional[float]) -> Optional[str]:
    """
    Format hourly precipitation in mm.
    Returns None if precip is None or < 0.1 mm (considered 0 mm, not displayed).
    Returns formatted string (e.g. '0.4 mm', '1 mm', '1.5 mm', '12 mm') if >= 0.1 mm.
    """
    if precip is None or precip < 0.05:
        return None
    rounded = round(precip, 1)
    if rounded < 0.1:
        return None
    if rounded == int(rounded):
        return f"{int(rounded)} mm"
    return f"{rounded:.1f} mm"


def slugify_city(name: str) -> str:
    """Normalize city name to clean ascii slug for URLs (e.g. 'Corbera de Llobregat' -> 'corbera-de-llobregat')."""
    if not name:
        return ''
    text = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    text = re.sub(r'[^\w\s-]', '', text.lower())
    return re.sub(r'[-\s]+', '-', text).strip('-')

def get_external_weather_url(
    city: Optional[str],
    target_date: Optional[Union[date, datetime]] = None,
    dow: Optional[int] = None,
    now_date: Optional[date] = None,
) -> str:
    """
    Generate external forecast URL for a city on Meteored España (tiempo.com).
    Calculates the exact day anchor (#dd-{N}) dynamically relative to current date:
    - N is the day index from today (1 = hoy, 2 = mañana, ..., 6 = jueves cuando hoy es sábado, up to 14).
    - If target_date is provided, N = (target_date - today).days + 1.
    - If only dow is provided, N = (days_until_next_dow) + 1.
    Falls back to https://www.tiempo.com/ if city is None, or Google search if slug is empty.
    """
    if not city:
        return "https://www.tiempo.com/"
    slug = slugify_city(city)
    if not slug:
        return f"https://www.google.com/search?q={quote_plus('tiempo ' + city)}"

    ref_date = now_date or datetime.now(_MADRID_TZ).date()
    anchor = ""

    if target_date is not None:
        actual_date = target_date.date() if isinstance(target_date, datetime) else target_date
        delta_days = (actual_date - ref_date).days
        day_num = delta_days + 1
        if 1 <= day_num <= 14:
            anchor = f"#dd-{day_num}"
    elif dow is not None:
        days_ahead = (dow - ref_date.weekday()) % 7
        day_num = days_ahead + 1
        if 1 <= day_num <= 14:
            anchor = f"#dd-{day_num}"

    return f"https://www.tiempo.com/{slug}/por-horas{anchor}"


def get_daily_weather_info(
    code: Optional[int],
    pop_max: Optional[int],
    precip_sum: Optional[float]
) -> Dict[str, str]:
    """
    Determine the most accurate weather icon, color and label for the daily header,
    taking into account the total daily expected precipitation amount (precip_sum in mm),
    maximum probability (pop_max in %), and raw WMO code.
    """
    if precip_sum is None:
        if code is not None and code in (0, 1, 2, 3, 45, 48) and pop_max and pop_max >= 50:
            return _WMO_WEATHER_MAP.get(80, {'icon': 'bi-cloud-rain-fill', 'color': '#0284c7', 'label': 'Chubascos ligeros'})
        return get_weather_info_for_code(code)

    precip_val = precip_sum

    # 1. Thunderstorms
    if code in (95, 96, 99):
        return _WMO_WEATHER_MAP.get(code, {'icon': 'bi-cloud-lightning-rain-fill', 'color': '#8b5cf6', 'label': 'Tormenta'})

    # 2. Negligible rain (< 0.2 mm)
    if precip_val < 0.2:
        if code is None or code >= 50:
            return {'icon': 'bi-cloud-fill', 'color': '#64748b', 'label': 'Nublado'}
        return get_weather_info_for_code(code)

    # 3. Light rain / drizzle (0.2 mm <= precip < 1.5 mm)
    if precip_val < 1.5:
        if code is not None and code in (0, 1, 2):
            return get_weather_info_for_code(code)
        return {'icon': 'bi-cloud-fill', 'color': '#64748b', 'label': 'Nublado (llovizna débil)'}

    # 4. Moderate rain (1.5 mm <= precip < 8.0 mm)
    if precip_val < 8.0:
        return {'icon': 'bi-cloud-rain-fill', 'color': '#0284c7', 'label': 'Lluvia moderada'}

    # 5. Heavy rain (>= 8.0 mm)
    return {'icon': 'bi-cloud-rain-heavy-fill', 'color': '#1d4ed8', 'label': 'Lluvia fuerte'}


def get_hourly_rain_info(pop: Optional[int], precip: Optional[float], code: Optional[int]) -> Dict[str, Any]:
    """
    Determine icon, color, description, and precip_str for hourly class rain indicator based on
    precipitation amount (mm/h), probability (%), and weather code.
    """
    precip_val = precip if precip is not None else 0.0
    precip_str = format_hourly_precipitation(precip_val)

    if code in (95, 96, 99):
        icon = 'bi-cloud-lightning-rain-fill'
        color = '#7c3aed'
        desc = f"Tormenta ({precip_val:.1f} mm/h)" if precip_val > 0 else "Tormenta"
    elif precip_val >= 2.5 or (code in (65, 81, 82) and precip_val >= 0.1):
        icon = 'bi-cloud-rain-heavy-fill'
        color = '#1d4ed8'
        desc = f"Lluvia fuerte ({precip_val:.1f} mm/h)"
    elif precip_val >= 1 or (code in (61, 63, 80) and precip_val >= 0.1):
        icon = 'bi-droplet-fill'
        color = '#0284c7'
        desc = f"Lluvia moderada ({precip_val:.1f} mm/h)"
    elif precip_val >= 0.1:
        icon = 'bi-droplet'
        color = '#0284c7'
        desc = f"Llovizna ({precip_val:.1f} mm/h)"
    else:
        # Negligible or zero rain (< 0.1 mm/h)
        icon = 'bi-droplet'
        color = '#94a3b8'
        desc = "Lluvia inapreciable (<0.1 mm/h)" if (pop and pop > 0) else None

    if precip_str:
        if pop is not None and pop > 0:
            tooltip = f"Lluvia prevista: {precip_str} ({pop}% prob.) · {desc}"
        else:
            tooltip = f"Lluvia prevista: {precip_str} · {desc}"
    elif pop is not None and pop > 0:
        tooltip = f"Probabilidad de lluvia: {pop}% · {desc}"
    else:
        tooltip = None

    return {
        'icon': icon,
        'color': color,
        'desc': desc,
        'precip_str': precip_str,
        'tooltip': tooltip,
    }


def adjust_code_for_rain_probability(code: Optional[int], pop: Optional[int], precip: Optional[float] = None) -> Optional[int]:
    """
    If rain probability is >= 50% and weather code indicates clear, partly cloudy or fog (0, 1, 2, 3, 45, 48),
    adjust the code to 80 (Chubascos ligeros) ONLY when there is actual precipitation expected (precip is None or precip >= 2.0).
    If precip is light (< 2.0 mm), keep it cloudy (3) rather than promoting to a rainstorm.
    """
    if pop is not None and pop >= 50 and (code in (0, 1, 2, 3, 45, 48) or code is None):
        if precip is not None and precip < 2.0:
            return 3
        return 80
    return code


def parse_hourly_forecast(forecast_data: Optional[Dict[str, Any]]) -> Dict[Tuple[str, int], Dict[str, Any]]:
    """
    Index Open-Meteo hourly forecast by (date_str 'YYYY-MM-DD', hour_int).
    """
    if not forecast_data or 'hourly' not in forecast_data:
        return {}

    hourly = forecast_data['hourly']
    times = hourly.get('time', [])
    temps = hourly.get('temperature_2m', [])
    pops = hourly.get('precipitation_probability', [])
    precips = hourly.get('precipitation', [])
    codes = hourly.get('weather_code', [])

    indexed: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for i, t_str in enumerate(times):
        # Format is 'YYYY-MM-DDTHH:MM'
        try:
            parts = t_str.split('T')
            d_str = parts[0]
            h_int = int(parts[1].split(':')[0])
            temp = temps[i] if i < len(temps) else None
            pop = pops[i] if i < len(pops) else None
            precip = precips[i] if i < len(precips) else 0.0
            code = codes[i] if i < len(codes) else None

            indexed[(d_str, h_int)] = {
                'temp': round(temp) if temp is not None else None,
                'pop': int(pop) if pop is not None else None,
                'precip': round(float(precip), 1) if precip is not None else 0.0,
                'code': int(code) if code is not None else None,
            }
        except (ValueError, IndexError):
            continue

    return indexed


def parse_daily_forecast(forecast_data: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Parse daily forecast from Open-Meteo, indexed by date_str 'YYYY-MM-DD':
    {
        'temp_max': int,
        'temp_min': int,
        'weather_code': int,
        'pop_max': int,
        'precip_sum': Optional[float],
    }
    Falls back to aggregating hourly data if daily is not available.
    """
    if not forecast_data:
        return {}

    daily_dict: Dict[str, Dict[str, Any]] = {}

    if 'daily' in forecast_data:
        daily = forecast_data['daily']
        times = daily.get('time', [])
        codes = daily.get('weather_code', [])
        maxs = daily.get('temperature_2m_max', [])
        mins = daily.get('temperature_2m_min', [])
        pops = daily.get('precipitation_probability_max', [])
        precip_sums = daily.get('precipitation_sum', [])

        for i, t_str in enumerate(times):
            try:
                t_max = round(maxs[i]) if i < len(maxs) and maxs[i] is not None else None
                t_min = round(mins[i]) if i < len(mins) and mins[i] is not None else None
                code = int(codes[i]) if i < len(codes) and codes[i] is not None else None
                pop = int(pops[i]) if i < len(pops) and pops[i] is not None else None
                precip_sum = round(float(precip_sums[i]), 1) if (i < len(precip_sums) and precip_sums[i] is not None) else None
                code = adjust_code_for_rain_probability(code, pop, precip_sum)
                daily_dict[t_str] = {
                    'temp_max': t_max,
                    'temp_min': t_min,
                    'weather_code': code,
                    'pop_max': pop,
                    'precip_sum': precip_sum,
                }
            except (ValueError, IndexError):
                continue

    # Fall back to aggregating from hourly data if not provided in daily
    if 'hourly' in forecast_data:
        hourly = forecast_data['hourly']
        times = hourly.get('time', [])
        temps = hourly.get('temperature_2m', [])
        pops = hourly.get('precipitation_probability', [])
        precips = hourly.get('precipitation', [])
        codes = hourly.get('weather_code', [])

        by_date = defaultdict(lambda: {'temps': [], 'pops': [], 'codes': [], 'precips': []})
        for i, t_str in enumerate(times):
            try:
                d_str = t_str.split('T')[0]
                if i < len(temps) and temps[i] is not None:
                    by_date[d_str]['temps'].append(round(temps[i]))
                if i < len(pops) and pops[i] is not None:
                    by_date[d_str]['pops'].append(int(pops[i]))
                if i < len(precips) and precips[i] is not None:
                    by_date[d_str]['precips'].append(float(precips[i]))
                if i < len(codes) and codes[i] is not None:
                    by_date[d_str]['codes'].append(int(codes[i]))
            except (ValueError, IndexError):
                continue

        for d_str, vals in by_date.items():
            if d_str not in daily_dict:
                daily_dict[d_str] = {}
            entry = daily_dict[d_str]

            if entry.get('temp_max') is None and vals['temps']:
                entry['temp_max'] = max(vals['temps'])
            if entry.get('temp_min') is None and vals['temps']:
                entry['temp_min'] = min(vals['temps'])
            if entry.get('pop_max') is None and vals['pops']:
                entry['pop_max'] = max(vals['pops'])
            if entry.get('precip_sum') is None:
                entry['precip_sum'] = round(sum(vals['precips']), 1) if vals['precips'] else None
            if entry.get('weather_code') is None and vals['codes']:
                precip_codes = [c for c in vals['codes'] if c >= 50]
                if precip_codes:
                    raw_code = max(precip_codes)
                else:
                    raw_code = max(vals['codes'])
                entry['weather_code'] = adjust_code_for_rain_probability(raw_code, entry.get('pop_max'), entry.get('precip_sum'))

    return daily_dict


def parse_daily_extremes(forecast_data: Optional[Dict[str, Any]]) -> Dict[str, Tuple[int, int]]:
    """
    Parse daily temperature extremes (max, min) from Open-Meteo forecast.
    Returns dict mapping date_str 'YYYY-MM-DD' -> (temp_max, temp_min).
    Maintained for backward compatibility.
    """
    daily = parse_daily_forecast(forecast_data)
    return {
        d: (val['temp_max'], val['temp_min'])
        for d, val in daily.items()
        if val.get('temp_max') is not None and val.get('temp_min') is not None
    }


def resolve_booking_weather(bookings: List[Any], user: Any, now_dt: Optional[datetime] = None) -> None:
    """
    Attach weather widget dictionary onto each Booking row in bookings:
    - If user.weather_enabled is False: does nothing.
    - If class has not passed this week: forecast is for current week date.
    - If schedule is for next week or class has passed: forecast is for next week date.
    - Matches temperature and weather code for the scheduled class hour.
    """
    if not bookings or not user:
        return

    # Check user preference toggle
    if not getattr(user, 'weather_enabled', True):
        for b in bookings:
            b.weather = None
        return

    if now_dt is None:
        now_dt = datetime.now(_MADRID_TZ)
    elif now_dt.tzinfo is None:
        now_dt = _MADRID_TZ.localize(now_dt)

    today = now_dt.date()
    monday = today - timedelta(days=today.weekday())

    coords = resolve_user_coordinates(user, bookings)
    forecast_data = get_weather_forecast(coords[0], coords[1])
    hourly_index = parse_hourly_forecast(forecast_data)

    for b in bookings:
        b.weather = None
        current_week_date = monday + timedelta(days=b.dow)
        next_week_date = current_week_date + timedelta(days=7)

        class_dt_current = _MADRID_TZ.localize(datetime.combine(current_week_date, b.time))
        has_passed = now_dt >= class_dt_current

        class_badge = getattr(b, 'class_badge', None)
        badge_state = class_badge.get('state') if class_badge else None

        # Determine target date
        if badge_state == 'next_week':
            target_date = class_badge.get('date') or next_week_date
            is_next_week = True
        elif badge_state == 'completed':
            target_date = next_week_date
            is_next_week = True
        elif not has_passed:
            target_date = (class_badge.get('date') if class_badge else None) or current_week_date
            is_next_week = False
        else:
            target_date = next_week_date
            is_next_week = True

        target_date_str = target_date.strftime('%Y-%m-%d')
        hour = b.time.hour

        weather_entry = hourly_index.get((target_date_str, hour))
        if not weather_entry:
            # Try adjacent hours (e.g. for half-past classes like 17:30)
            if b.time.minute >= 30:
                weather_entry = hourly_index.get((target_date_str, min(hour + 1, 23)))
            else:
                weather_entry = hourly_index.get((target_date_str, max(hour - 1, 0)))

        if weather_entry and weather_entry.get('temp') is not None:
            temp = weather_entry['temp']
            pop = weather_entry.get('pop')
            precip = weather_entry.get('precip', 0.0)
            code = weather_entry.get('code')
            rain_info = get_hourly_rain_info(pop, precip, code)
            info = get_weather_info_for_code(code)

            dow_name = DAYS_OF_WEEK[target_date.weekday()]
            time_str = b.time.strftime('%H:%M')
            date_short = target_date.strftime('%d/%m')

            if is_next_week:
                prefix = f"Próx. semana ({dow_name} {date_short} {time_str}): "
            else:
                prefix = f"{dow_name} {date_short} {time_str} · "

            tooltip = f"{prefix}{temp}°C · {info['label']}"
            if pop is not None and pop > 0:
                if precip and precip > 0:
                    tooltip += f" ({pop}% lluvia · {precip:.1f} mm/h)"
                else:
                    tooltip += f" ({pop}% lluvia)"

            b.weather = {
                'icon': info['icon'],
                'icon_color': info['color'],
                'temp': temp,
                'temp_str': f"{temp}°",
                'pop': pop,
                'rain_prob': pop,
                'precip': precip,
                'precip_str': rain_info['precip_str'],
                'rain_str': rain_info['precip_str'],
                'rain_icon': rain_info['icon'],
                'rain_icon_color': rain_info['color'],
                'rain_tooltip': rain_info['tooltip'],
                'pop_str': f"{pop}%" if (pop is not None and pop > 0) else None,
                'description': info['label'],
                'tooltip': tooltip,
                'target_date': target_date,
                'is_next_week': is_next_week,
            }


def resolve_wodbuster_bookings_weather(wb_bookings: List[Any], user: Any) -> None:
    """
    Attach weather widget to synced WodBusterBooking rows.
    """
    if not wb_bookings or not user or not getattr(user, 'weather_enabled', True):
        for wb in wb_bookings:
            wb.weather = None
        return

    coords = resolve_user_coordinates(user)
    forecast_data = get_weather_forecast(coords[0], coords[1])
    hourly_index = parse_hourly_forecast(forecast_data)

    for wb in wb_bookings:
        wb.weather = None
        if not wb.class_date or not wb.class_time:
            continue

        target_date = wb.class_date
        target_date_str = target_date.strftime('%Y-%m-%d')
        hour = wb.class_time.hour

        weather_entry = hourly_index.get((target_date_str, hour))
        if not weather_entry:
            if wb.class_time.minute >= 30:
                weather_entry = hourly_index.get((target_date_str, min(hour + 1, 23)))
            else:
                weather_entry = hourly_index.get((target_date_str, max(hour - 1, 0)))

        if weather_entry and weather_entry.get('temp') is not None:
            temp = weather_entry['temp']
            pop = weather_entry.get('pop')
            precip = weather_entry.get('precip', 0.0)
            code = weather_entry.get('code')
            rain_info = get_hourly_rain_info(pop, precip, code)
            info = get_weather_info_for_code(code)

            dow_name = DAYS_OF_WEEK[target_date.weekday()]
            time_str = wb.class_time.strftime('%H:%M')
            date_short = target_date.strftime('%d/%m')

            tooltip = f"{dow_name} {date_short} {time_str} · {temp}°C · {info['label']}"
            if pop is not None and pop > 0:
                if precip and precip > 0:
                    tooltip += f" ({pop}% lluvia · {precip:.1f} mm/h)"
                else:
                    tooltip += f" ({pop}% lluvia)"

            wb.weather = {
                'icon': info['icon'],
                'icon_color': info['color'],
                'temp': temp,
                'temp_str': f"{temp}°",
                'pop': pop,
                'rain_prob': pop,
                'precip': precip,
                'precip_str': rain_info['precip_str'],
                'rain_str': rain_info['precip_str'],
                'rain_icon': rain_info['icon'],
                'rain_icon_color': rain_info['color'],
                'rain_tooltip': rain_info['tooltip'],
                'pop_str': f"{pop}%" if (pop is not None and pop > 0) else None,
                'description': info['label'],
                'tooltip': tooltip,
                'target_date': target_date,
            }


def resolve_weekday_weather(bookings: List[Any], user: Any, now_dt: Optional[datetime] = None) -> Dict[int, Dict[str, Any]]:
    """
    Calculate weather forecast widget for each day-of-week header:
    - Returns a dict keyed by dow (0=Monday..6=Sunday) -> weather dict
    - If all classes for that dow have passed this week: forecast is for next week date.
    - If any class for that dow is upcoming this week: forecast is for current week date.
    - Representative hour: average of class times for that dow.
    """
    if not bookings or not user or not getattr(user, 'weather_enabled', True):
        return {}

    if now_dt is None:
        now_dt = datetime.now(_MADRID_TZ)
    elif now_dt.tzinfo is None:
        now_dt = _MADRID_TZ.localize(now_dt)

    today = now_dt.date()
    monday = today - timedelta(days=today.weekday())

    coords = resolve_user_coordinates(user, bookings)
    forecast_data = get_weather_forecast(coords[0], coords[1])
    hourly_index = parse_hourly_forecast(forecast_data)
    daily_forecast = parse_daily_forecast(forecast_data)

    bookings_by_dow = defaultdict(list)
    for b in bookings:
        bookings_by_dow[b.dow].append(b)

    weekday_weather: Dict[int, Dict[str, Any]] = {}

    for dow, b_list in bookings_by_dow.items():
        current_week_date = monday + timedelta(days=dow)
        next_week_date = current_week_date + timedelta(days=7)

        # Check if any class in this weekday has NOT passed yet
        has_upcoming = False
        for b in b_list:
            class_dt = _MADRID_TZ.localize(datetime.combine(current_week_date, b.time))
            if now_dt < class_dt:
                has_upcoming = True
                break

        if has_upcoming:
            target_date = current_week_date
            is_next_week = False
        else:
            target_date = next_week_date
            is_next_week = True

        target_date_str = target_date.strftime('%Y-%m-%d')
        # Representative hour (mean of class hours, e.g. 18)
        rep_hour = int(sum(b.time.hour for b in b_list) / len(b_list)) if b_list else 18

        weather_entry = hourly_index.get((target_date_str, rep_hour))
        if not weather_entry:
            weather_entry = hourly_index.get((target_date_str, 18)) or hourly_index.get((target_date_str, 12))

        daily_info = daily_forecast.get(target_date_str, {})

        if weather_entry and weather_entry.get('temp') is not None:
            temp = weather_entry['temp']
            temp_max = daily_info.get('temp_max', temp)
            temp_min = daily_info.get('temp_min', temp)

            code = daily_info.get('weather_code')
            if code is None:
                code = weather_entry.get('code')
            pop = daily_info.get('pop_max')
            if pop is None:
                pop = weather_entry.get('pop')
            precip_sum = daily_info.get('precip_sum', 0.0)

            info = get_daily_weather_info(code, pop, precip_sum)
            precip_str = format_precipitation_sum(precip_sum)

            if temp_max is not None and temp_min is not None and temp_max != temp_min:
                temp_str = f"{temp_max}° / {temp_min}°"
                temp_tooltip_val = f"{temp_max}° / {temp_min}°C"
            else:
                temp_str = f"{temp}°"
                temp_tooltip_val = f"{temp}°C"

            dow_name = DAYS_OF_WEEK[dow]
            date_short = target_date.strftime('%d/%m')

            if is_next_week:
                prefix = f"Próx. semana ({dow_name} {date_short}): "
            else:
                prefix = f"{dow_name} {date_short}: "

            tooltip = f"{prefix}{temp_tooltip_val} · {info['label']}"
            if precip_sum is not None and precip_sum > 0:
                tooltip += f" · {precip_str} de lluvia"
            if pop is not None and pop > 0:
                tooltip += f" ({pop}% lluvia)"

            precip_color = '#0284c7' if (precip_sum and precip_sum >= 2.0) else ('#0ea5e9' if (precip_sum and precip_sum > 0) else '#94a3b8')
            weather_url = get_external_weather_url(coords[2], target_date, dow, now_date=today)

            weekday_weather[dow] = {
                'icon': info['icon'],
                'icon_color': info['color'],
                'temp': temp,
                'temp_max': temp_max,
                'temp_min': temp_min,
                'temp_str': temp_str,
                'precip_sum': precip_sum,
                'precip_str': precip_str,
                'precip_color': precip_color,
                'pop': pop,
                'pop_str': f"{pop}%" if (pop is not None and pop >= 25) else None,
                'description': info['label'],
                'tooltip': tooltip,
                'target_date': target_date,
                'is_next_week': is_next_week,
                'weather_url': weather_url,
            }

    return weekday_weather


def resolve_wodbuster_date_weather(wb_bookings: List[Any], user: Any) -> Dict[date, Dict[str, Any]]:
    """
    Calculate weather forecast widget for each WodBuster booking date header:
    - Returns a dict keyed by class_date -> weather dict
    """
    if not wb_bookings or not user or not getattr(user, 'weather_enabled', True):
        return {}

    coords = resolve_user_coordinates(user)
    forecast_data = get_weather_forecast(coords[0], coords[1])
    hourly_index = parse_hourly_forecast(forecast_data)
    daily_forecast = parse_daily_forecast(forecast_data)

    bookings_by_date = defaultdict(list)
    for wb in wb_bookings:
        if wb.class_date:
            bookings_by_date[wb.class_date].append(wb)

    date_weather: Dict[date, Dict[str, Any]] = {}

    for d, b_list in bookings_by_date.items():
        d_str = d.strftime('%Y-%m-%d')
        rep_hour = int(sum(b.class_time.hour for b in b_list) / len(b_list)) if b_list else 18

        weather_entry = hourly_index.get((d_str, rep_hour))
        if not weather_entry:
            weather_entry = hourly_index.get((d_str, 18)) or hourly_index.get((d_str, 12))

        daily_info = daily_forecast.get(d_str, {})

        if weather_entry and weather_entry.get('temp') is not None:
            temp = weather_entry['temp']
            temp_max = daily_info.get('temp_max', temp)
            temp_min = daily_info.get('temp_min', temp)

            code = daily_info.get('weather_code')
            if code is None:
                code = weather_entry.get('code')
            pop = daily_info.get('pop_max')
            if pop is None:
                pop = weather_entry.get('pop')
            precip_sum = daily_info.get('precip_sum', 0.0)

            info = get_daily_weather_info(code, pop, precip_sum)
            precip_str = format_precipitation_sum(precip_sum)

            if temp_max is not None and temp_min is not None and temp_max != temp_min:
                temp_str = f"{temp_max}° / {temp_min}°"
                temp_tooltip_val = f"{temp_max}° / {temp_min}°C"
            else:
                temp_str = f"{temp}°"
                temp_tooltip_val = f"{temp}°C"

            dow_name = DAYS_OF_WEEK[d.weekday()]
            date_short = d.strftime('%d/%m')

            tooltip = f"{dow_name} {date_short}: {temp_tooltip_val} · {info['label']}"
            if precip_sum is not None and precip_sum > 0:
                tooltip += f" · {precip_str} de lluvia"
            if pop is not None and pop > 0:
                tooltip += f" ({pop}% lluvia)"

            precip_color = '#1d4ed8' if (precip_sum and precip_sum >= 2.0) else ('#0284c7' if (precip_sum and precip_sum > 0) else '#64748b')
            weather_url = get_external_weather_url(coords[2], d, d.weekday())

            date_weather[d] = {
                'icon': info['icon'],
                'icon_color': info['color'],
                'temp': temp,
                'temp_max': temp_max,
                'temp_min': temp_min,
                'temp_str': temp_str,
                'precip_sum': precip_sum,
                'precip_str': precip_str,
                'precip_color': precip_color,
                'pop': pop,
                'pop_str': f"{pop}%" if (pop is not None and pop >= 25) else None,
                'description': info['label'],
                'tooltip': tooltip,
                'target_date': d,
                'weather_url': weather_url,
            }

    return date_weather
