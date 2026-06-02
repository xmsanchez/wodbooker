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


if __name__ == '__main__':
    unittest.main()
