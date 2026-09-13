"""Tests for booking locking, error message detection, and retry logic."""
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime
import pytz

from wodbooker.exceptions import BookingLockedException, BookingFailed
from wodbooker.scraper import Scraper
from wodbooker.booker import _get_user_booking_lock, Booker, BOOKING_LOCKED_DELAY


class BookingLockingTests(unittest.TestCase):

    def test_scraper_raises_booking_locked_on_otro_sitio(self):
        scraper = Scraper("test_user", "test_cookie")
        scraper.login = MagicMock()
        
        target_dt = datetime(2026, 9, 18, 10, 0, tzinfo=pytz.timezone('Europe/Madrid'))
        
        class_item = {
            'Hora': '10:00:00',
            'Valores': {
                'WOD': {
                    'TipoEstado': 'Inscribible',
                    'Valor': {
                        'Id': 12345,
                        'Plazas': 15,
                        'AtletasEntrenando': [],
                    }
                }
            }
        }
        scraper.get_classes = MagicMock(return_value=(
            {'Data': [class_item]},
            1234567890
        ))
        
        # Mock _book_request to return the exact Spanish message from WodBuster
        error_msg = "Estás usando la reserva de clases en otro sitio, espera que termine y vuelve a intentarlo"
        scraper._book_request = MagicMock(return_value={
            'Res': {
                'EsCorrecto': False,
                'ErrorMsg': error_msg
            }
        })
        
        with self.assertRaises(BookingLockedException) as ctx:
            scraper.book("https://box.wodbuster.com", target_dt, "WOD")
        
        self.assertIn("otro sitio", str(ctx.exception).lower())

    def test_per_user_booking_lock_identity(self):
        lock_user_1_a = _get_user_booking_lock(1)
        lock_user_1_b = _get_user_booking_lock(1)
        lock_user_2 = _get_user_booking_lock(2)
        
        self.assertIs(lock_user_1_a, lock_user_1_b)
        self.assertIsNot(lock_user_1_a, lock_user_2)

    @patch('time.sleep', return_value=None)
    def test_attempt_booking_retries_on_locked_and_succeeds(self, mock_sleep):
        booker = object.__new__(Booker)
        booker._booking = MagicMock()
        booker._booking.url = "https://box.wodbuster.com"
        booker._booking.type_class = "WOD"
        booker._booking.user.email = "test@example.com"
        
        mock_scraper = MagicMock()
        # Fails twice with BookingLockedException, then succeeds
        mock_scraper.book.side_effect = [
            BookingLockedException("Estás usando la reserva de clases en otro sitio"),
            BookingLockedException("Estás usando la reserva de clases en otro sitio"),
            True
        ]
        
        result = booker._attempt_booking(datetime(2026, 9, 18, 10, 0), mock_scraper)
        self.assertTrue(result)
        self.assertEqual(mock_scraper.book.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)
        mock_sleep.assert_called_with(BOOKING_LOCKED_DELAY)

    @patch('time.sleep', return_value=None)
    def test_attempt_booking_fails_after_max_locked_retries(self, mock_sleep):
        booker = object.__new__(Booker)
        booker._booking = MagicMock()
        booker._booking.url = "https://box.wodbuster.com"
        booker._booking.type_class = "WOD"
        booker._booking.user.email = "test@example.com"
        
        mock_scraper = MagicMock()
        mock_scraper.book.side_effect = BookingLockedException("Estás usando la reserva de clases en otro sitio")
        
        with self.assertRaises(BookingFailed) as ctx:
            booker._attempt_booking(datetime(2026, 9, 18, 10, 0), mock_scraper)
        
        self.assertIn("Reserva bloqueada", str(ctx.exception))
        self.assertEqual(mock_scraper.book.call_count, 100)


if __name__ == '__main__':
    import os
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(BookingLockingTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    os._exit(0 if result.wasSuccessful() else 1)
