"""Tests for booking class badge resolution and color mapping."""
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, date, time, timedelta
import pytz

from wodbooker.views import resolve_booking_class_badges, _get_class_badge_color
from wodbooker.models import WodBusterClassSchedule, Booking, User

_MADRID_TZ = pytz.timezone('Europe/Madrid')


class BookingClassBadgeTests(unittest.TestCase):

    def test_class_badge_colors(self):
        self.assertEqual(_get_class_badge_color('WOD'), '#059669')
        self.assertEqual(_get_class_badge_color('Functional'), '#eab308')
        self.assertEqual(_get_class_badge_color('Gymnastics'), '#2563eb')
        self.assertEqual(_get_class_badge_color('GAP'), '#ec4899')
        self.assertEqual(_get_class_badge_color('Endurance'), '#0ea5e9')
        self.assertEqual(_get_class_badge_color('Open Box'), '#000000')
        self.assertEqual(_get_class_badge_color('Unknown Class', 14), '#64748b')
        self.assertEqual(_get_class_badge_color(None, 1), '#059669')

    @patch('wodbooker.views.db.session.query')
    def test_rule_1_upcoming_current_week(self, mock_query):
        """Rule 1: Class has NOT passed yet -> show current week class as 'upcoming'."""
        user = MagicMock()
        user.id = 1

        booking = MagicMock()
        booking.dow = 0  # Monday
        booking.time = time(16, 30)
        booking.type_class = 0

        # Current week Monday is 2026-09-14
        current_monday = date(2026, 9, 14)
        sched = WodBusterClassSchedule(
            user_id=1,
            box_url="https://box.wodbuster.com",
            class_date=current_monday,
            class_time=time(16, 30),
            class_name="Gymnastics",
            class_type=0,
            class_type_id=9,
            wodbuster_class_id=101,
        )

        query_mock = MagicMock()
        query_mock.filter.return_value.all.return_value = [sched]
        mock_query.return_value = query_mock

        # now is Monday 14:00 (before 16:30)
        now_dt = _MADRID_TZ.localize(datetime(2026, 9, 14, 14, 0))

        resolve_booking_class_badges([booking], user, now_dt=now_dt)

        self.assertIsNotNone(booking.class_badge)
        self.assertEqual(booking.class_badge['name'], 'Gymnastics')
        self.assertEqual(booking.class_badge['state'], 'upcoming')
        self.assertIsNone(booking.class_badge['subtext_label'])
        self.assertEqual(booking.class_badge['color'], '#2563eb')

    @patch('wodbooker.views.db.session.query')
    def test_rule_2_passed_next_week_not_available(self, mock_query):
        """Rule 2: Class passed, next week not available -> show current week class as 'completed'."""
        user = MagicMock()
        user.id = 1

        booking = MagicMock()
        booking.dow = 0  # Monday
        booking.time = time(16, 30)
        booking.type_class = 0

        current_monday = date(2026, 9, 14)
        sched = WodBusterClassSchedule(
            user_id=1,
            box_url="https://box.wodbuster.com",
            class_date=current_monday,
            class_time=time(16, 30),
            class_name="Gymnastics",
            class_type=0,
            class_type_id=9,
            wodbuster_class_id=101,
        )

        query_mock = MagicMock()
        # Only current week sched exists, next week (2026-09-21) does NOT exist
        query_mock.filter.return_value.all.return_value = [sched]
        mock_query.return_value = query_mock

        # now is Monday 17:00 (after 16:30)
        now_dt = _MADRID_TZ.localize(datetime(2026, 9, 14, 17, 0))

        resolve_booking_class_badges([booking], user, now_dt=now_dt)

        self.assertIsNotNone(booking.class_badge)
        self.assertEqual(booking.class_badge['name'], 'Gymnastics')
        self.assertEqual(booking.class_badge['state'], 'completed')
        self.assertEqual(booking.class_badge['subtext_label'], 'Esta semana')

    @patch('wodbooker.views.db.session.query')
    def test_rule_3_passed_next_week_available(self, mock_query):
        """Rule 3: Class passed AND next week available -> show next week class as 'next_week'."""
        user = MagicMock()
        user.id = 1

        booking = MagicMock()
        booking.dow = 0  # Monday
        booking.time = time(16, 30)
        booking.type_class = 0

        current_monday = date(2026, 9, 14)
        next_monday = date(2026, 9, 21)

        sched_current = WodBusterClassSchedule(
            user_id=1,
            box_url="https://box.wodbuster.com",
            class_date=current_monday,
            class_time=time(16, 30),
            class_name="Gymnastics",
            class_type=0,
            class_type_id=9,
            wodbuster_class_id=101,
        )
        sched_next = WodBusterClassSchedule(
            user_id=1,
            box_url="https://box.wodbuster.com",
            class_date=next_monday,
            class_time=time(16, 30),
            class_name="Functional",
            class_type=0,
            class_type_id=17,
            wodbuster_class_id=201,
        )

        query_mock = MagicMock()
        query_mock.filter.return_value.all.return_value = [sched_current, sched_next]
        mock_query.return_value = query_mock

        # now is Monday 17:00 (after 16:30)
        now_dt = _MADRID_TZ.localize(datetime(2026, 9, 14, 17, 0))

        resolve_booking_class_badges([booking], user, now_dt=now_dt)

        self.assertIsNotNone(booking.class_badge)
        self.assertEqual(booking.class_badge['name'], 'Functional')
        self.assertEqual(booking.class_badge['state'], 'next_week')
        self.assertEqual(booking.class_badge['subtext_label'], 'Próx. semana')
        self.assertEqual(booking.class_badge['color'], '#eab308')

    @patch('wodbooker.views.db.session.query')
    def test_weekend_sunday_shows_prox_for_next_week(self, mock_query):
        """When today is Sunday, past week Monday passed, so tomorrow Monday shows as next_week (prox)."""
        user = MagicMock()
        user.id = 1

        b_monday = MagicMock()
        b_monday.dow = 0  # Monday
        b_monday.time = time(16, 30)
        b_monday.type_class = 0

        # Current calendar week Monday was 2026-09-07
        past_monday = date(2026, 9, 7)
        tomorrow_monday = date(2026, 9, 14)
        sched_past = WodBusterClassSchedule(
            user_id=1,
            box_url="https://box.wodbuster.com",
            class_date=past_monday,
            class_time=time(16, 30),
            class_name="Gymnastics",
            class_type=0,
            class_type_id=9,
            wodbuster_class_id=101,
        )
        sched_next = WodBusterClassSchedule(
            user_id=1,
            box_url="https://box.wodbuster.com",
            class_date=tomorrow_monday,
            class_time=time(16, 30),
            class_name="Gymnastics",
            class_type=0,
            class_type_id=9,
            wodbuster_class_id=102,
        )

        query_mock = MagicMock()
        query_mock.filter.return_value.all.return_value = [sched_past, sched_next]
        mock_query.return_value = query_mock

        # today is Sunday 2026-09-13
        now_dt = _MADRID_TZ.localize(datetime(2026, 9, 13, 16, 45))

        resolve_booking_class_badges([b_monday], user, now_dt=now_dt)

        self.assertIsNotNone(b_monday.class_badge)
        self.assertEqual(b_monday.class_badge['name'], 'Gymnastics')
        self.assertEqual(b_monday.class_badge['state'], 'next_week')

    @patch('wodbooker.views.db.session.query')
    def test_monday_morning_current_week_no_prox(self, mock_query):
        """When Monday arrives, it is the current week, so before class time it has NO prox tag."""
        user = MagicMock()
        user.id = 1

        b_monday = MagicMock()
        b_monday.dow = 0  # Monday
        b_monday.time = time(16, 30)
        b_monday.type_class = 0

        today_monday = date(2026, 9, 14)
        sched_monday = WodBusterClassSchedule(
            user_id=1,
            box_url="https://box.wodbuster.com",
            class_date=today_monday,
            class_time=time(16, 30),
            class_name="Gymnastics",
            class_type=0,
            class_type_id=9,
            wodbuster_class_id=102,
        )

        query_mock = MagicMock()
        query_mock.filter.return_value.all.return_value = [sched_monday]
        mock_query.return_value = query_mock

        # Monday morning at 10:00 (before 16:30)
        now_dt = _MADRID_TZ.localize(datetime(2026, 9, 14, 10, 0))

        resolve_booking_class_badges([b_monday], user, now_dt=now_dt)

        self.assertIsNotNone(b_monday.class_badge)
        self.assertEqual(b_monday.class_badge['name'], 'Gymnastics')
        self.assertEqual(b_monday.class_badge['state'], 'upcoming')

    @patch('wodbooker.views.db.session.query')
    def test_type_class_distinction_at_same_hour(self, mock_query):
        """Ensure regular class (0) vs OpenBox (1) at the same hour are matched correctly."""
        user = MagicMock()
        user.id = 1

        b_wod = MagicMock()
        b_wod.dow = 2  # Wednesday
        b_wod.time = time(8, 0)
        b_wod.type_class = 0

        b_openbox = MagicMock()
        b_openbox.dow = 2  # Wednesday
        b_openbox.time = time(8, 0)
        b_openbox.type_class = 1

        wednesday = date(2026, 9, 16)
        sched_functional = WodBusterClassSchedule(
            user_id=1,
            box_url="https://box.wodbuster.com",
            class_date=wednesday,
            class_time=time(8, 0),
            class_name="Functional",
            class_type=0,
            class_type_id=17,
            wodbuster_class_id=301,
        )
        sched_openbox = WodBusterClassSchedule(
            user_id=1,
            box_url="https://box.wodbuster.com",
            class_date=wednesday,
            class_time=time(8, 0),
            class_name="Open box*",
            class_type=1,
            class_type_id=7,
            wodbuster_class_id=302,
        )

        query_mock = MagicMock()
        query_mock.filter.return_value.all.return_value = [sched_functional, sched_openbox]
        mock_query.return_value = query_mock

        # now is Monday 2026-09-14 (before Wednesday)
        now_dt = _MADRID_TZ.localize(datetime(2026, 9, 14, 10, 0))

        resolve_booking_class_badges([b_wod, b_openbox], user, now_dt=now_dt)

        self.assertEqual(b_wod.class_badge['name'], 'Functional')
        self.assertEqual(b_wod.class_badge['color'], '#eab308')

        self.assertEqual(b_openbox.class_badge['name'], 'Open box*')
        self.assertEqual(b_openbox.class_badge['color'], '#000000')

    @patch('wodbooker.booker.db_commit_with_retry')
    @patch('wodbooker.booker.db.session')
    @patch('wodbooker.booker.get_scraper')
    def test_sync_weekly_classes_success(self, mock_get_scraper, mock_session, mock_commit):
        from wodbooker.booker import sync_weekly_classes

        user = MagicMock()
        user.id = 42
        user.email = 'test@example.com'
        user.cookie = 'dummy_cookie'
        user.athlete_id = 'ath123'

        scraper = MagicMock()
        target_date = date(2026, 9, 14)
        scraper.get_week_classes.return_value = {
            target_date: [
                {
                    'Hora': '18:30',
                    'NombreE': 'WOD',
                    'IdE': 1,
                    'Id': 999,
                    'class_type': 0,
                }
            ]
        }
        mock_get_scraper.return_value = scraper

        mock_session.query.return_value.filter_by.return_value.all.return_value = []
        mock_session.query.return_value.filter.return_value.delete.return_value = 0

        res = sync_weekly_classes(user, box_url="https://box.wodbuster.com")

        self.assertTrue(res['success'])
        self.assertEqual(res['synced'], 1)
        mock_session.add.assert_called_once()
        added_obj = mock_session.add.call_args[0][0]
        self.assertEqual(added_obj.class_name, 'WOD')
        self.assertEqual(added_obj.class_time, time(18, 30))
        self.assertEqual(added_obj.class_type, 0)
        self.assertEqual(added_obj.wodbuster_class_id, 999)
        mock_commit.assert_called_once()

    @patch('wodbooker.scraper.Scraper._book_request')
    @patch('wodbooker.scraper.Scraper.login')
    def test_scraper_get_week_classes_empty_data_falls_back_to_list_clases(self, mock_login, mock_book_request):
        from wodbooker.scraper import Scraper

        scraper = Scraper('test@example.com', cookie=b'')
        # Simulate WodBuster response when booking hasn't opened yet: Data is empty list, ListClases has classes
        mock_book_request.return_value = {
            'Data': [],
            'ListClases': [
                {
                    'Hora': '07:00:00',
                    'NombreE': 'Wod',
                    'IdE': 1,
                    'Id': 19725,
                    'Borrable': False,
                },
                {
                    'Hora': '09:00:00',
                    'NombreE': 'Open box*',
                    'IdE': 7,
                    'Id': 19727,
                    'Borrable': False,
                },
            ],
        }

        res = scraper.get_week_classes('https://corbera.wodbuster.com', date(2026, 9, 28), days=1)
        classes_day = res[date(2026, 9, 28)]
        self.assertEqual(len(classes_day), 2)
        self.assertEqual(classes_day[0]['NombreE'], 'Wod')
        self.assertEqual(classes_day[0]['Hora'], '07:00:00')
        self.assertEqual(classes_day[0]['class_type'], 0)
        self.assertEqual(classes_day[1]['NombreE'], 'Open box*')
        self.assertEqual(classes_day[1]['class_type'], 1)


if __name__ == '__main__':
    unittest.main()


