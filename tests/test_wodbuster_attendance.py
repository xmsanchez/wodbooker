"""Tests for WodBuster attendance parsing."""
import datetime
import importlib.util
import sys
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    'wodbuster_attendance',
    Path(__file__).resolve().parent.parent / 'wodbooker' / 'wodbuster_attendance.py',
)
_wa = importlib.util.module_from_spec(_spec)
sys.modules['wodbuster_attendance'] = _wa
_spec.loader.exec_module(_wa)

parse_services_payload = _wa.parse_services_payload
stats_from_services_for_calendar_month = _wa.stats_from_services_for_calendar_month
parse_billing_period_stats = _wa.parse_billing_period_stats
filter_records_to_calendar_month = _wa.filter_records_to_calendar_month
compute_ytd_yoy_mood = _wa.compute_ytd_yoy_mood
compute_quota_mood = _wa.compute_quota_mood

# Minimal fixture based on real Master_MisServicios shape
_SERVICES_FIXTURE = {
    'Tarifa': 'Strong.12',
    'PeriodoActualDesde': 1780272000,
    'PeriodoActualHasta': 1782777600,
    'TarifaDesc': [
        {'Reservas': {'DeTarifa': 12, 'DeUsuario': 12}},
        {'Reservas': {'DeTarifa': 4, 'DeUsuario': 4}},
    ],
    'Reservas': [
        {
            'Fecha': 1780272000,
            'Clases': [
                {'Hora': '17:30:00', 'Perdida': False, 'Borrada': False},
            ],
        },
        {
            'Fecha': 1780358400,
            'Clases': [
                {'Hora': '18:30:00', 'Perdida': False, 'Borrada': True},
                {'Hora': '20:30:00', 'Perdida': False, 'Borrada': False},
            ],
        },
    ],
}


class WodBusterAttendanceTests(unittest.TestCase):
    def test_quota_sums_tarifa_groups(self):
        billing = parse_billing_period_stats(_SERVICES_FIXTURE)
        self.assertEqual(billing.quota_used, 16)
        self.assertEqual(billing.quota_total, 16)
        self.assertEqual(billing.cancelled, 1)

    def test_calendar_month_filter_excludes_other_months(self):
        full = parse_services_payload(_SERVICES_FIXTURE)
        self.assertGreaterEqual(len(full.records), 2)
        sample = full.records[0]
        y, m = sample.class_datetime.year, sample.class_datetime.month
        cal = stats_from_services_for_calendar_month(_SERVICES_FIXTURE, y, m)
        filtered = filter_records_to_calendar_month(full.records, y, m)
        self.assertLessEqual(cal.booked, full.booked)
        self.assertLessEqual(len(filtered), len(full.records))

    def test_yoy_mood_comparison(self):
        rows = [
            {'year': 2026, 'month': 1, 'attended': 5},
            {'year': 2026, 'month': 2, 'attended': 3},
            {'year': 2025, 'month': 1, 'attended': 4},
            {'year': 2025, 'month': 2, 'attended': 2},
        ]
        yoy = compute_ytd_yoy_mood(rows, today=datetime.date(2026, 2, 15))
        self.assertEqual(yoy.ytd_attended_current, 8)
        self.assertEqual(yoy.ytd_attended_previous, 6)
        self.assertTrue(yoy.has_comparison)

    def test_quota_mood_early_month_good_pace(self):
        # On Sept 3rd (day 3/30), 2 attended classes out of 16 target is ~125% expected pace
        mood = compute_quota_mood(
            attended=2,
            booked=3,
            quota_total=16,
            period_from=datetime.date(2026, 9, 1),
            period_to=datetime.date(2026, 9, 30),
            today=datetime.date(2026, 9, 3),
        )
        self.assertEqual(mood.label, '¡Estás on fire!')
        self.assertIn('Semana 1', mood.subtitle)
        self.assertIn('2 asistidas de 16', mood.subtitle)

    def test_quota_mood_mid_month_low_attended_does_not_say_on_fire(self):
        # On Sept 13th (day 13/30), 5 attended classes out of 16 target (even with 10 booked)
        # Expected to date is (13/30)*16 ~= 6.93. 5 attended is ~72.1% pace -> "No vas mal este mes, ¡ánimo!"
        mood = compute_quota_mood(
            attended=5,
            booked=10,
            quota_total=16,
            period_from=datetime.date(2026, 9, 1),
            period_to=datetime.date(2026, 9, 30),
            today=datetime.date(2026, 9, 13),
        )
        self.assertEqual(mood.label, 'No vas mal este mes, ¡ánimo!')
        self.assertIn('Semana 2', mood.subtitle)
        self.assertIn('5 asistidas de 16', mood.subtitle)

    def test_quota_mood_early_month_moderate_pace(self):
        # On Sept 3rd (day 3/30), 1 attended class out of 16 target is ~62.5% expected pace
        mood = compute_quota_mood(
            attended=1,
            booked=2,
            quota_total=16,
            period_from=datetime.date(2026, 9, 1),
            period_to=datetime.date(2026, 9, 30),
            today=datetime.date(2026, 9, 3),
        )
        self.assertEqual(mood.label, 'No vas mal este mes, ¡ánimo!')
        self.assertIn('Semana 1', mood.subtitle)

    def test_quota_mood_unlimited_subscription(self):
        # Unlimited subscription (quota_total=0) with 11 attended classes on Sept 13th
        # Should treat 16 classes as target, expected to date is (13/30)*16 ~= 6.9, 11 attended is ~159% pace
        mood = compute_quota_mood(
            attended=11,
            quota_total=0,
            period_from=datetime.date(2026, 9, 1),
            period_to=datetime.date(2026, 9, 30),
            today=datetime.date(2026, 9, 13),
        )
        self.assertEqual(mood.label, '¡Estás on fire!')
        self.assertIn('ilimitada', mood.subtitle)
        self.assertIn('Semana 2', mood.subtitle)

    def test_quota_mood_late_month_behind_pace(self):
        # On Sept 28th (day 28/30), only 2 attended classes out of 16 target
        mood = compute_quota_mood(
            attended=2,
            quota_total=16,
            period_from=datetime.date(2026, 9, 1),
            period_to=datetime.date(2026, 9, 30),
            today=datetime.date(2026, 9, 28),
        )
        self.assertEqual(mood.label, 'Por ahora no es tu mejor mes')
        self.assertIn('Semana 4', mood.subtitle)

    def test_quota_mood_full_quota_reached_early(self):
        # 16 classes attended by mid month
        mood = compute_quota_mood(
            attended=16,
            quota_total=16,
            period_from=datetime.date(2026, 9, 1),
            period_to=datetime.date(2026, 9, 30),
            today=datetime.date(2026, 9, 15),
        )
        self.assertEqual(mood.label, '¡Estás on fire!')
        self.assertGreaterEqual(mood.usage_pct, 1.0)


if __name__ == '__main__':
    unittest.main()
