"""Tests for weather forecast service, temporal resolution and preference toggles."""
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, date, time, timedelta
import pytz

from wodbooker.weather import (
    get_weather_info_for_code,
    extract_box_subdomain,
    get_default_city_for_box,
    KNOWN_BOX_CITY_MAPPINGS,
    geocode_location,
    resolve_user_coordinates,
    get_weather_forecast,
    parse_hourly_forecast,
    parse_daily_extremes,
    parse_daily_forecast,
    adjust_code_for_rain_probability,
    format_precipitation_sum,
    format_hourly_precipitation,
    slugify_city,
    get_external_weather_url,
    get_daily_weather_info,
    get_hourly_rain_info,
    resolve_booking_weather,
    resolve_wodbuster_bookings_weather,
    resolve_weekday_weather,
    resolve_wodbuster_date_weather,
    _FORECAST_CACHE,
    _GEOCODE_CACHE,
)

_MADRID_TZ = pytz.timezone('Europe/Madrid')


class WeatherServiceTests(unittest.TestCase):

    def setUp(self):
        _FORECAST_CACHE.clear()
        _GEOCODE_CACHE.clear()

    def test_wmo_code_mapping(self):
        clear = get_weather_info_for_code(0)
        self.assertEqual(clear['icon'], 'bi-sun-fill')
        self.assertEqual(clear['label'], 'Despejado')

        partly_cloudy = get_weather_info_for_code(2)
        self.assertEqual(partly_cloudy['icon'], 'bi-cloud-sun-fill')
        self.assertEqual(partly_cloudy['label'], 'Parcialmente nublado')

        rain = get_weather_info_for_code(63)
        self.assertEqual(rain['icon'], 'bi-cloud-rain-fill')
        self.assertEqual(rain['label'], 'Lluvia moderada')

        thunder = get_weather_info_for_code(95)
        self.assertEqual(thunder['icon'], 'bi-cloud-lightning-rain-fill')
        self.assertEqual(thunder['label'], 'Tormenta')

        unknown = get_weather_info_for_code(9999)
        self.assertEqual(unknown['icon'], 'bi-cloud-sun')
        self.assertEqual(unknown['label'], 'Variable')

    def test_extract_box_subdomain(self):
        self.assertEqual(extract_box_subdomain('https://corbera.wodbuster.com'), 'corbera')
        self.assertEqual(extract_box_subdomain('https://mayantibox.wodbuster.com/user/bookings.aspx'), 'mayantibox')
        self.assertIsNone(extract_box_subdomain('https://wodbuster.com'))
        self.assertIsNone(extract_box_subdomain('https://www.wodbuster.com'))
        self.assertIsNone(extract_box_subdomain(None))
        self.assertIsNone(extract_box_subdomain(''))

    def test_get_default_city_for_box(self):
        self.assertEqual(get_default_city_for_box('https://corbera.wodbuster.com'), 'Corbera de Llobregat')
        self.assertEqual(get_default_city_for_box('corbera.wodbuster.com'), 'Corbera de Llobregat')
        self.assertEqual(get_default_city_for_box('corbera'), 'Corbera de Llobregat')
        self.assertIsNone(get_default_city_for_box('https://otherbox.wodbuster.com'))
        self.assertIsNone(get_default_city_for_box(None))

    @patch('wodbooker.weather.requests.get')
    def test_geocode_location_success_and_cache(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'results': [{
                'name': 'Corbera de Llobregat',
                'latitude': 41.417,
                'longitude': 1.919,
            }]
        }
        mock_get.return_value = mock_resp

        res = geocode_location('Corbera')
        self.assertIsNotNone(res)
        self.assertAlmostEqual(res[0], 41.417)
        self.assertAlmostEqual(res[1], 1.919)
        self.assertEqual(res[2], 'Corbera de Llobregat')

        # Calling again uses cache (no second HTTP request)
        res2 = geocode_location('Corbera')
        self.assertEqual(res, res2)
        mock_get.assert_called_once()

    def test_resolve_user_coordinates_saved_lat_lon(self):
        user = MagicMock()
        user.weather_lat = 40.5
        user.weather_lon = -3.5
        user.weather_city = "Alcobendas"

        coords = resolve_user_coordinates(user)
        self.assertEqual(coords, (40.5, -3.5, "Alcobendas"))

    @patch('wodbooker.weather.geocode_location')
    def test_resolve_user_coordinates_fallback_to_box_subdomain(self, mock_geocode):
        mock_geocode.return_value = (41.417, 1.919, "Corbera de Llobregat")
        user = MagicMock()
        user.weather_lat = None
        user.weather_lon = None
        user.weather_city = None
        user.id = 1

        booking = MagicMock()
        booking.url = "https://corbera.wodbuster.com"

        with patch('wodbooker.weather.db.session.commit'):
            coords = resolve_user_coordinates(user, bookings=[booking])
            self.assertEqual(coords, (41.417, 1.919, "Corbera de Llobregat"))
            mock_geocode.assert_called_with('Corbera de Llobregat')

    def test_parse_hourly_forecast(self):
        raw_data = {
            'hourly': {
                'time': ['2026-09-28T18:00', '2026-09-28T19:00'],
                'temperature_2m': [22.1, 20.8],
                'precipitation_probability': [10, 35],
                'weather_code': [1, 61],
            }
        }
        indexed = parse_hourly_forecast(raw_data)
        self.assertIn(('2026-09-28', 18), indexed)
        self.assertEqual(indexed[('2026-09-28', 18)]['temp'], 22)
        self.assertEqual(indexed[('2026-09-28', 18)]['pop'], 10)
        self.assertEqual(indexed[('2026-09-28', 18)]['code'], 1)

        self.assertIn(('2026-09-28', 19), indexed)
        self.assertEqual(indexed[('2026-09-28', 19)]['temp'], 21)
        self.assertEqual(indexed[('2026-09-28', 19)]['pop'], 35)
        self.assertEqual(indexed[('2026-09-28', 19)]['code'], 61)

    def test_resolve_booking_weather_disabled_preference(self):
        user = MagicMock()
        user.weather_enabled = False

        booking = MagicMock()
        booking.weather = "placeholder"

        resolve_booking_weather([booking], user)
        self.assertIsNone(booking.weather)

    @patch('wodbooker.weather.get_weather_forecast')
    @patch('wodbooker.weather.resolve_user_coordinates')
    def test_resolve_booking_weather_upcoming_current_week(self, mock_coords, mock_forecast):
        mock_coords.return_value = (40.4, -3.7, "Madrid")
        # Wednesday is 2026-09-30
        mock_forecast.return_value = {
            'hourly': {
                'time': ['2026-09-30T19:00'],
                'temperature_2m': [23.4],
                'precipitation_probability': [5],
                'weather_code': [0],
            }
        }

        user = MagicMock()
        user.weather_enabled = True

        booking = MagicMock()
        booking.dow = 2  # Wednesday
        booking.time = time(19, 30)
        booking.class_badge = {'state': 'upcoming', 'name': 'WOD'}

        # Now is Monday 2026-09-28 10:00 (before Wednesday)
        now_dt = _MADRID_TZ.localize(datetime(2026, 9, 28, 10, 0))

        resolve_booking_weather([booking], user, now_dt=now_dt)

        self.assertIsNotNone(booking.weather)
        self.assertEqual(booking.weather['temp_str'], '23°')
        self.assertEqual(booking.weather['icon'], 'bi-sun-fill')
        self.assertEqual(booking.weather['description'], 'Despejado')
        self.assertFalse(booking.weather['is_next_week'])
        self.assertEqual(booking.weather['target_date'], date(2026, 9, 30))
        self.assertIn("Miércoles 30/09 19:30 · 23°C · Despejado", booking.weather['tooltip'])

    @patch('wodbooker.weather.get_weather_forecast')
    @patch('wodbooker.weather.resolve_user_coordinates')
    def test_resolve_booking_weather_passed_targets_next_week(self, mock_coords, mock_forecast):
        mock_coords.return_value = (40.4, -3.7, "Madrid")
        # Monday next week is 2026-10-05
        mock_forecast.return_value = {
            'hourly': {
                'time': ['2026-10-05T09:00'],
                'temperature_2m': [16.2],
                'precipitation_probability': [60],
                'weather_code': [61],
            }
        }

        user = MagicMock()
        user.weather_enabled = True

        booking = MagicMock()
        booking.dow = 0  # Monday
        booking.time = time(9, 0)
        booking.class_badge = {'state': 'next_week', 'name': 'WOD'}

        # Now is Monday 2026-09-28 12:00 (after Monday 09:00)
        now_dt = _MADRID_TZ.localize(datetime(2026, 9, 28, 12, 0))

        resolve_booking_weather([booking], user, now_dt=now_dt)

        self.assertIsNotNone(booking.weather)
        self.assertEqual(booking.weather['temp_str'], '16°')
        self.assertEqual(booking.weather['icon'], 'bi-cloud-rain-fill')
        self.assertEqual(booking.weather['pop_str'], '60%')
        self.assertTrue(booking.weather['is_next_week'])
        self.assertEqual(booking.weather['target_date'], date(2026, 10, 5))
        self.assertIn("Próx. semana (Lunes 05/10 09:00): 16°C · Lluvia ligera (60% lluvia)", booking.weather['tooltip'])

    @patch('wodbooker.weather.get_weather_forecast')
    @patch('wodbooker.weather.resolve_user_coordinates')
    def test_resolve_wodbuster_bookings_weather(self, mock_coords, mock_forecast):
        mock_coords.return_value = (40.4, -3.7, "Madrid")
        mock_forecast.return_value = {
            'hourly': {
                'time': ['2026-09-29T18:00'],
                'temperature_2m': [20.0],
                'precipitation_probability': [10],
                'weather_code': [2],
            }
        }

        user = MagicMock()
        user.weather_enabled = True

        wb_booking = MagicMock()
        wb_booking.class_date = date(2026, 9, 29)
        wb_booking.class_time = time(18, 0)

        resolve_wodbuster_bookings_weather([wb_booking], user)

        self.assertIsNotNone(wb_booking.weather)
        self.assertEqual(wb_booking.weather['temp_str'], '20°')
        self.assertEqual(wb_booking.weather['icon'], 'bi-cloud-sun-fill')
        self.assertIn("Martes 29/09 18:00 · 20°C · Parcialmente nublado", wb_booking.weather['tooltip'])

    @patch('wodbooker.weather.get_weather_forecast')
    @patch('wodbooker.weather.resolve_user_coordinates')
    def test_resolve_weekday_weather(self, mock_coords, mock_forecast):
        mock_coords.return_value = (40.4, -3.7, "Madrid")
        # Wednesday is 2026-09-30
        mock_forecast.return_value = {
            'hourly': {
                'time': ['2026-09-30T18:00', '2026-10-05T09:00'],
                'temperature_2m': [24.0, 15.0],
                'precipitation_probability': [0, 40],
                'weather_code': [0, 61],
            }
        }

        user = MagicMock()
        user.weather_enabled = True

        # Booking 1: Wednesday at 18:00
        b1 = MagicMock()
        b1.dow = 2
        b1.time = time(18, 0)

        # Booking 2: Monday at 09:00
        b2 = MagicMock()
        b2.dow = 0
        b2.time = time(9, 0)

        # Let now be Monday 2026-09-28 at 12:00:
        # Wednesday (b1) is upcoming this week.
        # Monday (b2) 09:00 has already passed, so b2 targets next week (2026-10-05).
        now_dt = _MADRID_TZ.localize(datetime(2026, 9, 28, 12, 0))

        result = resolve_weekday_weather([b1, b2], user, now_dt=now_dt)

        self.assertIn(2, result)
        self.assertIn(0, result)

        wed_weather = result[2]
        self.assertEqual(wed_weather['temp_str'], '24°')
        self.assertEqual(wed_weather['icon'], 'bi-sun-fill')
        self.assertFalse(wed_weather['is_next_week'])
        self.assertIn("Miércoles 30/09: 24°C · Despejado", wed_weather['tooltip'])

        mon_weather = result[0]
        self.assertEqual(mon_weather['temp_str'], '15°')
        self.assertEqual(mon_weather['icon'], 'bi-cloud-rain-fill')
        self.assertTrue(mon_weather['is_next_week'])
        self.assertEqual(mon_weather['pop_str'], '40%')
        self.assertIn("Próx. semana (Lunes 05/10): 15°C · Lluvia ligera (40% lluvia)", mon_weather['tooltip'])

    @patch('wodbooker.weather.get_weather_forecast')
    @patch('wodbooker.weather.resolve_user_coordinates')
    def test_resolve_wodbuster_date_weather(self, mock_coords, mock_forecast):
        mock_coords.return_value = (40.4, -3.7, "Madrid")
        mock_forecast.return_value = {
            'hourly': {
                'time': ['2026-09-29T18:00'],
                'temperature_2m': [22.0],
                'precipitation_probability': [15],
                'weather_code': [1],
            }
        }

        user = MagicMock()
        user.weather_enabled = True

        wb = MagicMock()
        wb.class_date = date(2026, 9, 29)
        wb.class_time = time(18, 0)

        result = resolve_wodbuster_date_weather([wb], user)

        self.assertIn(date(2026, 9, 29), result)
        entry = result[date(2026, 9, 29)]
        self.assertEqual(entry['temp_str'], '22°')
        self.assertIn("Martes 29/09: 22°C · Mayormente despejado", entry['tooltip'])
        self.assertIn("(15% lluvia)", entry['tooltip'])

    @patch('wodbooker.weather.get_weather_forecast')
    @patch('wodbooker.weather.resolve_user_coordinates')
    def test_daily_min_max_temperatures(self, mock_coords, mock_forecast):
        """Test daily max and min temperature resolution and formatting."""
        mock_coords.return_value = (40.4, -3.7, "Madrid")
        # 1. Test parse_daily_extremes from daily block
        data_with_daily = {
            'daily': {
                'time': ['2026-09-30'],
                'temperature_2m_max': [26.4],
                'temperature_2m_min': [14.1],
            }
        }
        extremes = parse_daily_extremes(data_with_daily)
        self.assertEqual(extremes['2026-09-30'], (26, 14))

        # 2. Test parse_daily_extremes fallback to hourly block
        data_with_hourly = {
            'hourly': {
                'time': ['2026-09-30T08:00', '2026-09-30T14:00', '2026-09-30T20:00'],
                'temperature_2m': [13.8, 25.6, 21.0],
            }
        }
        extremes_hourly = parse_daily_extremes(data_with_hourly)
        self.assertEqual(extremes_hourly['2026-09-30'], (26, 14))

        # 3. Test resolve_weekday_weather with min/max
        mock_forecast.return_value = {
            'daily': {
                'time': ['2026-09-30'],
                'temperature_2m_max': [26.0],
                'temperature_2m_min': [14.0],
            },
            'hourly': {
                'time': ['2026-09-30T18:00'],
                'temperature_2m': [22.0],
                'precipitation_probability': [0],
                'weather_code': [0],
            }
        }

        user = MagicMock()
        user.weather_enabled = True

        b = MagicMock()
        b.dow = 2  # Wednesday
        b.time = time(18, 0)

        # Wednesday 2026-09-30 is upcoming (now is Monday 2026-09-28)
        now_dt = _MADRID_TZ.localize(datetime(2026, 9, 28, 12, 0))
        result = resolve_weekday_weather([b], user, now_dt=now_dt)

        self.assertIn(2, result)
        wed = result[2]
        self.assertEqual(wed['temp_max'], 26)
        self.assertEqual(wed['temp_min'], 14)
        self.assertEqual(wed['temp_str'], '26° / 14°')
        self.assertIn("26° / 14°C", wed['tooltip'])

    def test_adjust_code_for_rain_probability(self):
        # High rain probability (>= 50%) promotes clear/partly cloudy (0, 1, 2, 3) or fog (45, 48) to 80 (Chubascos)
        self.assertEqual(adjust_code_for_rain_probability(2, 70), 80)
        self.assertEqual(adjust_code_for_rain_probability(0, 50), 80)
        self.assertEqual(adjust_code_for_rain_probability(3, 85), 80)
        self.assertEqual(adjust_code_for_rain_probability(45, 60), 80)
        self.assertEqual(adjust_code_for_rain_probability(48, 90), 80)
        # Moderate or low rain probability (< 50%) keeps original code
        self.assertEqual(adjust_code_for_rain_probability(2, 30), 2)
        self.assertEqual(adjust_code_for_rain_probability(0, 20), 0)
        self.assertEqual(adjust_code_for_rain_probability(45, 30), 45)
        # Already rainy/severe codes (>= 50) are kept intact
        self.assertEqual(adjust_code_for_rain_probability(95, 75), 95)
        self.assertEqual(adjust_code_for_rain_probability(61, 80), 61)

    @patch('wodbooker.weather.requests.get')
    def test_get_weather_forecast_ecmwf_ifs_primary(self, mock_get):
        """get_weather_forecast requests ECMWF IFS model first."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'daily': {'time': ['2026-09-29']}}
        mock_get.return_value = mock_resp

        data = get_weather_forecast(41.417, 1.919)
        self.assertIsNotNone(data)
        self.assertEqual(data, {'daily': {'time': ['2026-09-29']}})

        # Verify first call used models='ecmwf_ifs'
        args, kwargs = mock_get.call_args
        self.assertEqual(kwargs['params']['models'], 'ecmwf_ifs')

    @patch('wodbooker.weather.requests.get')
    def test_get_weather_forecast_fallback_on_error(self, mock_get):
        """If ecmwf_ifs and ecmwf_ifs025 fail, falls back to default model."""
        fail_resp = MagicMock()
        fail_resp.status_code = 400
        fail_resp.text = 'Model error'

        success_resp = MagicMock()
        success_resp.status_code = 200
        success_resp.json.return_value = {'daily': {'time': ['2026-09-29']}}

        mock_get.side_effect = [fail_resp, fail_resp, success_resp]

        data = get_weather_forecast(41.417, 1.919)
        self.assertIsNotNone(data)
        self.assertEqual(mock_get.call_count, 3)
        # The 3rd call is fallback without 'models'
        last_kwargs = mock_get.call_args[1]
        self.assertNotIn('models', last_kwargs['params'])

    def test_format_precipitation_sum(self):
        self.assertEqual(format_precipitation_sum(0.0), '0 mm')
        self.assertEqual(format_precipitation_sum(0.04), '0 mm')
        self.assertEqual(format_precipitation_sum(0.1), '0.1 mm')
        self.assertEqual(format_precipitation_sum(1.0), '1 mm')
        self.assertEqual(format_precipitation_sum(1.3), '1.3 mm')
        self.assertEqual(format_precipitation_sum(11.7), '11.7 mm')
        self.assertEqual(format_precipitation_sum(12.0), '12 mm')

    def test_format_hourly_precipitation(self):
        self.assertIsNone(format_hourly_precipitation(None))
        self.assertIsNone(format_hourly_precipitation(0.0))
        self.assertIsNone(format_hourly_precipitation(0.04))
        self.assertEqual(format_hourly_precipitation(0.1), '0.1 mm')
        self.assertEqual(format_hourly_precipitation(0.4), '0.4 mm')
        self.assertEqual(format_hourly_precipitation(1.0), '1 mm')
        self.assertEqual(format_hourly_precipitation(1.3), '1.3 mm')
        self.assertEqual(format_hourly_precipitation(11.7), '11.7 mm')
        self.assertEqual(format_hourly_precipitation(12.0), '12 mm')

    def test_get_daily_weather_info_by_rainfall(self):
        # Negligible rainfall (< 0.2 mm) -> overcast/clear, never rain cloud
        info_dry = get_daily_weather_info(code=53, pop_max=80, precip_sum=0.1)
        self.assertEqual(info_dry['icon'], 'bi-cloud-fill')
        self.assertEqual(info_dry['label'], 'Nublado')

        # Light rainfall (0.2 - 2.0 mm) -> cloudy with light drizzle, not heavy rain
        info_light = get_daily_weather_info(code=53, pop_max=84, precip_sum=1.3)
        self.assertEqual(info_light['icon'], 'bi-cloud-fill')
        self.assertEqual(info_light['label'], 'Nublado (llovizna débil)')

        # Moderate rainfall (2.0 - 8.0 mm) -> rain cloud
        info_mod = get_daily_weather_info(code=61, pop_max=70, precip_sum=4.5)
        self.assertEqual(info_mod['icon'], 'bi-cloud-rain-fill')
        self.assertEqual(info_mod['label'], 'Lluvia moderada')

        # Heavy rainfall (>= 8.0 mm) -> heavy rain cloud
        info_heavy = get_daily_weather_info(code=65, pop_max=90, precip_sum=15.0)
        self.assertEqual(info_heavy['icon'], 'bi-cloud-rain-heavy-fill')
        self.assertEqual(info_heavy['label'], 'Lluvia fuerte')

        # Thunderstorm (code 95) -> storm cloud
        info_storm = get_daily_weather_info(code=95, pop_max=86, precip_sum=11.7)
        self.assertEqual(info_storm['icon'], 'bi-cloud-lightning-rain-fill')
        self.assertEqual(info_storm['label'], 'Tormenta')

    def test_get_hourly_rain_info_intensity(self):
        # 0% pop -> no rain
        info_zero = get_hourly_rain_info(pop=0, precip=0.0, code=0)
        self.assertEqual(info_zero['icon'], 'bi-droplet')
        self.assertIsNone(info_zero['precip_str'])

        # High pop but negligible rainfall (e.g. Monday afternoon, 0.0 mm/h) -> outline droplet, None precip_str (hidden)
        info_trace = get_hourly_rain_info(pop=49, precip=0.0, code=3)
        self.assertEqual(info_trace['icon'], 'bi-droplet')
        self.assertIsNone(info_trace['precip_str'])
        self.assertIn('Lluvia inapreciable', info_trace['tooltip'])

        # Moderate rainfall (1.0 mm/h) -> solid droplet, shows '1 mm'
        info_mod = get_hourly_rain_info(pop=70, precip=1.0, code=61)
        self.assertEqual(info_mod['icon'], 'bi-droplet-fill')
        self.assertEqual(info_mod['precip_str'], '1 mm')
        self.assertIn('Lluvia moderada', info_mod['tooltip'])

        # Storm (code 95) -> storm cloud, shows '2.2 mm'
        info_storm = get_hourly_rain_info(pop=86, precip=2.2, code=95)
        self.assertEqual(info_storm['icon'], 'bi-cloud-lightning-rain-fill')
        self.assertEqual(info_storm['precip_str'], '2.2 mm')
        self.assertIn('Tormenta', info_storm['tooltip'])

    @patch('wodbooker.weather.get_weather_forecast')
    @patch('wodbooker.weather.resolve_user_coordinates')
    def test_high_rain_probability_avoids_partially_cloudy(self, mock_coords, mock_forecast):
        """When rain probability is high (>=50%) and rain is expected, weather should NOT display 'Parcialmente nublado'."""
        mock_coords.return_value = (40.4, -3.7, "Madrid")
        # Simulate Open-Meteo returning partly cloudy code=2 with pop=75% and precip_sum=4.0mm
        mock_forecast.return_value = {
            'daily': {
                'time': ['2026-09-29'],
                'weather_code': [2],
                'temperature_2m_max': [22.0],
                'temperature_2m_min': [18.0],
                'precipitation_probability_max': [75],
                'precipitation_sum': [4.0],
            },
            'hourly': {
                'time': ['2026-09-29T18:00'],
                'temperature_2m': [20.0],
                'precipitation_probability': [75],
                'precipitation': [1.5],
                'weather_code': [2],
            }
        }

        user = MagicMock()
        user.weather_enabled = True

        b = MagicMock()
        b.dow = 1  # Tuesday
        b.time = time(18, 0)

        # Tuesday 2026-09-29 is upcoming (now is Monday 2026-09-28)
        now_dt = _MADRID_TZ.localize(datetime(2026, 9, 28, 12, 0))
        result = resolve_weekday_weather([b], user, now_dt=now_dt)

        self.assertIn(1, result)
        tue = result[1]
        self.assertEqual(tue['icon'], 'bi-cloud-rain-fill')
        self.assertEqual(tue['description'], 'Lluvia moderada')
        self.assertNotIn('Parcialmente nublado', tue['tooltip'])
        self.assertIn('Lluvia moderada', tue['tooltip'])
        self.assertEqual(tue['precip_str'], '4 mm')
        self.assertEqual(tue['weather_url'], 'https://www.tiempo.com/madrid.htm#dd-2')

    def test_slugify_city(self):
        self.assertEqual(slugify_city('Corbera de Llobregat'), 'corbera-de-llobregat')
        self.assertEqual(slugify_city('Barcelona'), 'barcelona')
        self.assertEqual(slugify_city('Málaga'), 'malaga')
        self.assertEqual(slugify_city('A Coruña'), 'a-coruna')
        self.assertEqual(slugify_city(''), '')

    def test_get_external_weather_url(self):
        url_corbera = get_external_weather_url('Corbera de Llobregat')
        self.assertEqual(url_corbera, 'https://www.tiempo.com/corbera-de-llobregat.htm')

        url_none = get_external_weather_url(None)
        self.assertEqual(url_none, 'https://www.tiempo.com/')

        # Dynamic anchors: Saturday 2026-09-26 -> Thursday 2026-10-01 is dd-6
        sat = date(2026, 9, 26)
        thu = date(2026, 10, 1)
        fri = date(2026, 10, 2)
        url_thu = get_external_weather_url('Corbera de Llobregat', target_date=thu, now_date=sat)
        self.assertEqual(url_thu, 'https://www.tiempo.com/corbera-de-llobregat.htm#dd-6')

        url_fri = get_external_weather_url('Corbera de Llobregat', target_date=fri, now_date=sat)
        self.assertEqual(url_fri, 'https://www.tiempo.com/corbera-de-llobregat.htm#dd-7')

        url_today = get_external_weather_url('Corbera de Llobregat', target_date=sat, now_date=sat)
        self.assertEqual(url_today, 'https://www.tiempo.com/corbera-de-llobregat.htm#dd-1')

        # With dow only (Thursday = dow 3)
        url_dow_thu = get_external_weather_url('Corbera de Llobregat', dow=3, now_date=sat)
        self.assertEqual(url_dow_thu, 'https://www.tiempo.com/corbera-de-llobregat.htm#dd-6')


if __name__ == '__main__':
    unittest.main()


