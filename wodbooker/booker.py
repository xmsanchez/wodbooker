from datetime import datetime, timedelta, date, time
from abc import ABC, abstractmethod
import random
import logging
import time as time_module
import threading
import pytz
import os
from flask import current_app as app
from func_timeout import StoppableThread
from requests.exceptions import RequestException
from .constants import EventMessage, UNEXPECTED_ERROR_MAIL_SUBJECT, \
    UNEXPECTED_ERROR_MAIL_BODY, FULL_CLASS_BOOKED_MAIL_SUBJECT, \
    FULL_CLASS_BOOKED_MAIL_BODY, ERROR_AUTOHEALED_MAIL_SUBJECT, \
    ERROR_AUTOHEALED_MAIL_BODY, CLASS_BOOKED_MAIL_SUBJECT, \
    CLASS_BOOKED_MAIL_BODY
from .scraper import get_scraper, Scraper
from .mailer import send_email, ErrorEmail, SuccessAfterErrorEmail, SuccessEmail
from .push_notifications import send_booking_status_notification
from .exceptions import BookingNotAvailable, InvalidWodBusterResponse, \
    ClassIsFull, LoginError, PasswordRequired, InvalidBox, \
    ClassNotFound, BookingFailed, BookingPenalization, BookingLockedException
from .models import db, Booking, Event, User, WodBusterBooking

# Import high-level logger for important business events
high_level_logger = logging.getLogger('high_level')

_MADRID_TZ = pytz.timezone('Europe/Madrid')

# Priority users list - users in this list will have precedence over others
# Priority users are read from environment variable PRIORITY_USERS_EMAILS
# Emails should be separated by spaces
PRIORITY_USERS = os.getenv('PRIORITY_USERS_EMAILS', '').split()

# Whitelist for bookings - if set, only emails in this list can book
# Whitelist is read from environment variable BOOKING_WHITELIST_EMAILS
# Emails should be separated by spaces
# If empty/null, all emails are allowed (current behavior)
WHITELIST_EMAILS = os.getenv('BOOKING_WHITELIST_EMAILS', '').split()

# Increase the max errors, this is to prevent bookings
# from not succeeding when there is a penalization
# I know it's a weird workaround :-)
_MAX_ERRORS = 500
_MAX_BOOKING_ATTEMPTS = 20
GLOBAL_BOOKING_INTERVAL = 0.5
BOOKING_RETRY_DELAY = 1
BOOKING_LOCKED_DELAY = 0.2

__CURRENT_THREADS = {
}

# Simple in-memory coordination for user bookings
_GLOBAL_BOOKING_LOCK = threading.Lock()
_LAST_GLOBAL_BOOKING_TIME = None


def _get_next_date_for_weekday(base_date: date, weekday: int) -> date:
    """ 
    Get the next date for a given weekday. If the weekday is the same as the base date, the base date is returned
    :param base_date: The base date to start the search
    :param weekday: The weekday to search 
    """
    days_ahead = weekday - base_date.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return base_date + timedelta(days_ahead)


def _get_datetime_to_book(last_booking_date: date, dow: int, booking_time: time) -> datetime:
    """
    Get the day to book for a given date and day of week
    :param last_booking_date: The last booking date
    :param dow: The day of week
    :param booking_time: The time to book
    :return: The datetime to book
    """
    now = datetime.now(_MADRID_TZ)
    base_date = now.date() if not last_booking_date else last_booking_date + timedelta(days=1)
    day_to_book = _get_next_date_for_weekday(base_date, dow)
    datetime_to_book = _MADRID_TZ.localize(datetime.combine(day_to_book, booking_time))

    if now > datetime_to_book:
        day_to_book = _get_next_date_for_weekday(now.date() + timedelta(days=1), dow)
        datetime_to_book = _MADRID_TZ.localize(datetime.combine(day_to_book, booking_time))

    return datetime_to_book


class _StopThreadException(BaseException):
    pass


class Booker(StoppableThread):

    def __init__(self, booking: Booking, app_context):
        """
        :param booking: The booking to run
        :param app_context: The Flask app context
        """
        super(Booker, self).__init__()
        self._booking = None
        self._booking_id = booking.id
        self._session = None
        self._app_context = app_context
        self.name = f"Booker {self._booking_id}"

    def _wait_for_booking_window(self, waiter, day_to_book):
        book_available_at = _MADRID_TZ.localize(
            datetime.combine(
                day_to_book - timedelta(days=self._booking.offset),
                self._booking.available_at))

        waiter = waiter or _TimeWaiter(self._booking, EventMessage.WAIT_UNTIL_BOOKING_OPEN % (book_available_at.strftime('%d/%m/%Y a las %H:%M:%S'),
                                                                                                        day_to_book.strftime('%d/%m/%Y')),
                                                book_available_at)
        if waiter:
            waiter.wait()
        return None

    def _attempt_booking(self, datetime_to_book, scraper):
        booking_successful = False
        force_exit = False
        while not booking_successful and not force_exit:
            try:
                scraper.book(self._booking.url, datetime_to_book, self._booking.type_class)
                booking_successful = True
            except BookingLockedException as e:
                logging.warning("Booking locked for user %s: %s. Retrying in %.2f second...",
                                self._booking.user.email, str(e), BOOKING_LOCKED_DELAY)
                time_module.sleep(BOOKING_LOCKED_DELAY)
                continue
        return booking_successful

    def _handle_successful_booking(self, day_to_book, scraper, errors, class_is_full_notification_sent):
        high_level_logger.info("Booking for user %s at %s completed successfully", self._booking.user.email, day_to_book.strftime('%d/%m/%Y %H:%M:%S'))
        event = Event(booking_id=self._booking.id, event=EventMessage.BOOKING_COMPLETED % day_to_book.strftime('%d/%m/%Y'))
        _add_event(event)

        email = None
        if errors > 0:
            email = SuccessAfterErrorEmail(self._booking, ERROR_AUTOHEALED_MAIL_SUBJECT, ERROR_AUTOHEALED_MAIL_BODY)
            errors = 0

        if class_is_full_notification_sent:
            email = SuccessAfterErrorEmail(self._booking, FULL_CLASS_BOOKED_MAIL_SUBJECT, FULL_CLASS_BOOKED_MAIL_BODY)
            class_is_full_notification_sent = False

        email = email or SuccessEmail(self._booking, CLASS_BOOKED_MAIL_SUBJECT, CLASS_BOOKED_MAIL_BODY)
        # send_email(self._booking.user, email)

        self._booking.last_book_date = day_to_book
        self._booking.booked_at = datetime.now().replace(microsecond=0)
        self._booking.user.cookie = scraper.get_cookies()
        return errors, class_is_full_notification_sent

    def run(self) -> None:
        try:
            self._app_context.push()
            self._booking = db.session.query(Booking).filter_by(id=self._booking_id).first()
            errors = 0
            force_exit = False
            waiter = None
            datetime_to_book = None
            skip_current_week = False
            class_is_full_notification_sent = False
            sleep_milliseconds = random.randint(1, 1000) / 1000
            while errors < _MAX_ERRORS and not force_exit:
                try:
                    booking_attempts = 0
                    book_time = time(self._booking.time.hour, self._booking.time.minute, 0)
                    _datetime_to_book = _get_datetime_to_book(self._booking.last_book_date, self._booking.dow, book_time)

                    if waiter and datetime_to_book != _datetime_to_book:
                        logging.info("Waiting for class %s is over.", datetime_to_book.strftime('%d/%m/%Y %H:%M:%S'))

                        # Add another sleep here in case we are trying to make multiple books due to previous penalizations
                        logging.info("Sleeping for %s seconds", sleep_milliseconds)
                        time_module.sleep(sleep_milliseconds)

                        # Continue after the sleep
                        event = Event(booking_id=self._booking.id,
                                      event=EventMessage.CLASS_WAITING_OVER % (datetime_to_book.strftime('%d/%m/%Y'), _datetime_to_book.strftime('%d/%m/%Y')))
                        _add_event(event)
                        print(f'Event is: ' + str(event.event))
                        class_is_full_notification_sent = False
                        waiter = None
                    elif datetime_to_book == _datetime_to_book and skip_current_week:
                        _datetime_to_book = _datetime_to_book + timedelta(days=7)
                        skip_current_week = False

                    datetime_to_book = _datetime_to_book
                    day_to_book = datetime_to_book.date()

                    waiter = self._wait_for_booking_window(waiter, day_to_book)

                    # Check if user has priority - non-priority users wait 1 second
                    if self._booking.user.email not in PRIORITY_USERS:
                        logging.info("User %s is not in priority list, waiting 1 second before booking", self._booking.user.email)
                        time_module.sleep(1)
                    else:
                        high_level_logger.info("User %s has priority, proceeding with booking immediately", self._booking.user.email)

                    # Use coordinator to ensure 1-second minimum interval between bookings
                    with _GLOBAL_BOOKING_LOCK:
                        global _LAST_GLOBAL_BOOKING_TIME
                        now = datetime.now(_MADRID_TZ)
                        if _LAST_GLOBAL_BOOKING_TIME:
                            time_since_last = (now - _LAST_GLOBAL_BOOKING_TIME).total_seconds()
                            if time_since_last < GLOBAL_BOOKING_INTERVAL:
                                sleep_time = GLOBAL_BOOKING_INTERVAL - time_since_last
                                logging.info("Waiting %.2f seconds to maintain %.2f-second global booking interval", sleep_time, GLOBAL_BOOKING_INTERVAL)
                                time_module.sleep(sleep_time)
                        
                        _LAST_GLOBAL_BOOKING_TIME = datetime.now(_MADRID_TZ)

                    # Refresh the scraper in case a new one is avaiable
                    scraper = get_scraper(self._booking.user.email, self._booking.user.cookie)

                    # generate a random number in milliseconds to avoid being detected as a bot
                    logging.info("Sleeping for %s seconds", sleep_milliseconds)
                    time_module.sleep(sleep_milliseconds)

                    if self._attempt_booking(datetime_to_book, scraper):
                        errors, class_is_full_notification_sent = self._handle_successful_booking(day_to_book, scraper, errors, class_is_full_notification_sent)

                    # Send push notification for successful booking
                    send_booking_status_notification(
                        self._booking.user,
                        self._booking,
                        True,
                        event.event
                    )

                    email = None
                    if errors > 0:
                        email = SuccessAfterErrorEmail(self._booking, ERROR_AUTOHEALED_MAIL_SUBJECT, ERROR_AUTOHEALED_MAIL_BODY)
                        errors = 0

                    if class_is_full_notification_sent:
                        email = SuccessAfterErrorEmail(self._booking, FULL_CLASS_BOOKED_MAIL_SUBJECT, FULL_CLASS_BOOKED_MAIL_BODY)
                        class_is_full_notification_sent = False

                    email = email or SuccessEmail(self._booking, CLASS_BOOKED_MAIL_SUBJECT, CLASS_BOOKED_MAIL_BODY)
                    # send_email(self._booking.user, email)

                    self._booking.last_book_date = day_to_book
                    self._booking.booked_at = datetime.now().replace(microsecond=0)
                    self._booking.user.cookie = scraper.get_cookies()
                except ClassNotFound as e:
                    booking_attempts += 1
                    logging.warning("Class not found. Attempt %d/%d. Retrying in %d second. %s",
                                  booking_attempts, _MAX_BOOKING_ATTEMPTS, BOOKING_RETRY_DELAY, e)
                    if booking_attempts >= _MAX_BOOKING_ATTEMPTS:
                        logging.error("Max attempts reached for ClassNotFound. Skipping this week.")
                        skip_current_week = True
                        event = Event(booking_id=self._booking.id, event=EventMessage.CLASS_NOT_FOUND % (datetime_to_book.strftime("%d/%m/%Y"), datetime_to_book.strftime("%H:%M:%S")))
                        _add_event(event)
                    else:
                        time_module.sleep(BOOKING_RETRY_DELAY)

                # In some boxes a penalty can be set in place when people make a book cancellation
                # This should be managed in the scraper.py book function but I don't really know
                # What's the API response and I won't risk it so I'll treat it as a "CLASS IS FULL" event
                except BookingPenalization as e:
                    logging.warning("There is a penalty for your bookings this week: %s", e)
                    # The minimum wait are 10 seconds, therefore let's sleep the thread for 10 seconds
                    time_module.sleep(10)
                    time_module.sleep(sleep_milliseconds)
                    waiter = _EventWaiter(self._booking, EventMessage.BOOKING_PENALIZATION % e,
                                          scraper, self._booking.url, day_to_book, ['changedBooking'], datetime_to_book)
                except BookingFailed as e:
                    logging.warning("Class cannot be booked %s", e)
                    skip_current_week = True
                    event = Event(booking_id=self._booking.id, event=EventMessage.BOOKING_ERROR % (datetime_to_book.strftime("%d/%m/%Y"), str(e).rstrip(".")))
                    _add_event(event)
                    
                    # Send push notification for failed booking
                    send_booking_status_notification(
                        self._booking.user,
                        self._booking,
                        False,
                        event.event
                    )
                    
                    # send_email(self._booking.user, ErrorEmail(self._booking, "Error en la reserva", event.event))
                except ClassIsFull:
                    logging.info("Class is full. Setting wait for event to 'changedBooking'")
                    waiter = _EventWaiter(self._booking, EventMessage.CLASS_FULL % day_to_book.strftime('%d/%m/%Y'),
                                          scraper, self._booking.url, day_to_book, ['changedBooking'], datetime_to_book)
                    if not class_is_full_notification_sent:
                        # send_email(self._booking.user, ErrorEmail(self._booking, "Clase llena", waiter.log_message))
                        class_is_full_notification_sent = True
                except BookingNotAvailable as e:
                    if e.available_at:
                        logging.info("Class is not bookeable yet. Setting wait for datetime to %s", e.available_at.strftime('%d/%m/%Y %H:%M:%S'))
                        waiter = _TimeWaiter(self._booking, EventMessage.WAIT_UNTIL_BOOKING_OPEN % (e.available_at.strftime('%d/%m/%Y a las %H:%M:%S'),
                                                                                                    day_to_book.strftime('%d/%m/%Y')),
                                             e.available_at)
                    else:
                        logging.info("Classes for %s are not loaded yet. Waiting for any type of event", day_to_book.strftime('%d/%m/%Y'))
                        waiter = _EventWaiter(self._booking, EventMessage.WAIT_CLASS_LOADED % day_to_book.strftime('%d/%m/%Y'),
                                              scraper, self._booking.url, day_to_book,
                                              ['changedPizarra', 'changedBooking'], datetime_to_book)
                    continue
                except RequestException as e:
                    sleep_for = (errors + 1) * 60
                    logging.warning("Request Exception: %s", e)
                    waiter = _TimeWaiter(self._booking, EventMessage.UNEXPECTED_NETWORK_ERROR % sleep_for,
                                            datetime.now(_MADRID_TZ) + timedelta(seconds=sleep_for))
                    if errors == 0:
                        # send_email(self._booking.user, ErrorEmail(self._booking, UNEXPECTED_ERROR_MAIL_SUBJECT,
                        #                                          UNEXPECTED_ERROR_MAIL_BODY))
                        pass
                    errors += 1
                except InvalidWodBusterResponse as e:
                    sleep_for = (errors + 1) * 60
                    logging.warning("Invalid WodBuster response: %s", e)
                    waiter = _TimeWaiter(self._booking, EventMessage.UNEXPECTED_WODBUSTER_RESPONSE % sleep_for,
                                         datetime.now(_MADRID_TZ) + timedelta(seconds=sleep_for))
                    if errors == 0:
                        #send_email(self._booking.user, ErrorEmail(self._booking, UNEXPECTED_ERROR_MAIL_SUBJECT,
                        #                                          UNEXPECTED_ERROR_MAIL_BODY))
                        pass
                    errors += 1
                except PasswordRequired:
                    force_exit = True
                    logging.warning("Credentials for user %s are outdated. Aborting...", self._booking.user.email)
                    self._booking.user.force_login = True
                    event = Event(booking_id=self._booking.id, event=EventMessage.CREDENTIALS_EXPIRED)
                    _add_event(event)
                    # send_email(self._booking.user, ErrorEmail(self._booking, "Credenciales caducadas", event.event))
                except LoginError:
                    force_exit = True
                    logging.warning("User %s cannot be logged in into WodBuster. Aborting...", self._booking.user.email)
                    self._booking.user.force_login = True
                    event = Event(booking_id=self._booking.id, event=EventMessage.LOGIN_FAILED)
                    _add_event(event)
                    # send_email(self._booking.user, ErrorEmail(self._booking, "Login fallido", event.event))
                except InvalidBox:
                    force_exit = True
                    logging.warning("User %s accessing to an invalid box detected. Aborting...", self._booking.user.email)
                    event = Event(booking_id=self._booking.id, event=EventMessage.INVALID_BOX_URL)
                    _add_event(event)
                    # send_email(self._booking.user, ErrorEmail(self._booking, "Box inválido", event.event))
                finally:
                    db.session.commit()

            if errors >= _MAX_ERRORS:
                logging.error("Exiting thread as maximum number of retries has been reached. Review logs for more information")
                event = Event(booking_id=self._booking.id, event=EventMessage.TOO_MANY_ERRORS)
                _add_event(event)
                db.session.commit()
            high_level_logger.info("Exiting thread...")
        except _StopThreadException:
            logging.info("Thread %s has been stopped", self._name)
        except Exception:
            logging.exception("Unexpected error while booking. Aborting...")


class _Waiter(ABC):

    def __init__(self, booking: Booking, log_message: str) -> None:
        """
        Waiter construction
        :param booking: The booking to run
        :param log_message: The message to log
        """
        self.booking = booking
        self.log_message = log_message

    @abstractmethod
    def wait(self):
        """
        Wait until the condition is met
        """
        raise NotImplementedError()


class _TimeWaiter(_Waiter):

    def __init__(self, booking: Booking, log_message: str, wait_datetime: datetime) -> None:
        """
        Time Waiter construction
        :param booking: The booking the waiter is related to
        :param log_message: The message related to the waiter
        :param datetime: The datetime to wait for
        """
        super().__init__(booking, log_message)
        self._wait_datetime = wait_datetime

    def wait(self):
        """
        Wait until the provided date is reached
        """
        if self._wait_datetime > datetime.now(_MADRID_TZ):
            high_level_logger.info("Waiting until %s", self._wait_datetime.strftime('%d/%m/%Y %H:%M:%S'))
            event = Event(booking_id=self.booking.id, event=self.log_message)
            _add_event(event)
            db.session.commit()
            # Calculate seconds to wait and use time.sleep instead of pause.until
            seconds_to_wait = (self._wait_datetime - datetime.now(_MADRID_TZ)).total_seconds()
            if seconds_to_wait > 0:
                time_module.sleep(seconds_to_wait)


class _EventWaiter(_Waiter):

    def __init__(self, booking: Booking, log_message: str, scraper: Scraper, url: str,
                 event_date: date, expected_events:list, max_datetime: datetime=None):
        """
        Event Waiter construction
        :param booking: The booking the waiter is related to
        :param log_message: The message related to the waiter
        :param scraper: The scraper to use
        :param url: The WodBuster URL
        :param date: The day associated with the occurrence of the event
        :param expected_events: A list with the expected events
        :param max_datetime: The maximum datetime to wait for
        """
        super().__init__(booking, log_message)
        self._scraper = scraper
        self._url = url
        self._event_date = event_date
        self._expected_events = expected_events
        self._max_datetime = max_datetime

    def wait(self):
        """
        Wait until the event occurs
        """
        event = Event(booking_id=self.booking.id, event=self.log_message)
        _add_event(event)
        db.session.commit()
        self._scraper.wait_until_event(self._url, self._event_date, self._expected_events,
                                       self._max_datetime)


def _add_event(event: Event) -> None:
    """
    Add the evnet to the session only when the last event is different
    :param event: The event to add
    """
    last_event = db.session.query(Event).filter_by(booking_id=event.booking_id).order_by(Event.id.desc()).first()
    if not last_event or last_event.event != event.event:
        db.session.add(event)

def start_booking_loop(booking: Booking) -> None:
    """ 
    Start the booking loop for a given booking 
    :param url: The WodBuster URL
    :param booking: The booking to run
    :param offset: The offset from today to book
    :param availabe_at: The time when the booking is available
    """
    # Check whitelist if it's configured
    if WHITELIST_EMAILS and booking.user.email not in WHITELIST_EMAILS:
        high_level_logger.warning("Booking attempt blocked: User %s is not in the whitelist. Whitelist contains: %s", 
                       booking.user.email, ', '.join(WHITELIST_EMAILS))
        # Create an event to log this blocked attempt
        event = Event(booking_id=booking.id, 
                     event=f"Intento de reserva fallido.")
        _add_event(event)
        db.session.commit()
        return

    high_level_logger.info("Starting thread for booking %s (user: %s)", 
                booking.id, booking.user.email)
    booker = Booker(booking, app.app_context())
    __CURRENT_THREADS[booking.id] = booker
    booker.start()

def stop_booking_loop(booking: Booking, log_pause: bool=False) -> None:
    """ 
    Stop the booking loop for a given booking 
    :param booking: The booking to stop
    :param log_pause: If True, a pause event is logged
    """
    logging.info("Stopping thread for booking %s", booking)
    if booking.id in __CURRENT_THREADS:
        booker = __CURRENT_THREADS[booking.id]
        booker.stop(_StopThreadException)
        del __CURRENT_THREADS[booking.id]

        if log_pause:
            event = Event(booking_id=booking.id, event=EventMessage.PAUSED)
            _add_event(event)
            db.session.commit()

def is_booking_running(booking: Booking) -> bool:
    """
    Check if a booking is running
    :param booking: The booking to check
    :return: True if the booking is running, False otherwise
    """
    return booking.id in __CURRENT_THREADS and __CURRENT_THREADS[booking.id].is_alive()


def sync_wodbuster_bookings(user: User) -> dict:
    """
    Sync WodBuster bookings for a user for the current week (Monday to Sunday).
    :param user: The user to sync bookings for
    :return: Dictionary with sync results: {'success': bool, 'new': int, 'updated': int, 'cancelled': int, 'errors': list}
    """
    if not user.athlete_id:
        logging.warning("Cannot sync bookings for user %s: no athlete_id", user.email)
        return {'success': False, 'new': 0, 'updated': 0, 'cancelled': 0, 'errors': ['No athlete_id set']}
    
    # Get box URL from user's most recent booking
    box_url = None
    last_booking = db.session.query(Booking).filter_by(user_id=user.id).order_by(Booking.id.desc()).first()
    if last_booking and last_booking.url:
        box_url = last_booking.url
    else:
        # Try to get box URL directly
        try:
            scraper = get_scraper(user.email, user.cookie)
            box_url = scraper.get_box_url()
        except Exception as e:
            logging.warning("Could not get box URL for user %s: %s", user.email, str(e))
            return {'success': False, 'new': 0, 'updated': 0, 'cancelled': 0, 'errors': [f'Could not get box URL: {str(e)}']}
    
    if not box_url:
        return {'success': False, 'new': 0, 'updated': 0, 'cancelled': 0, 'errors': ['No box URL available']}
    
    try:
        scraper = get_scraper(user.email, user.cookie)
        today = date.today()
        
        # Calculate current week: Monday to Sunday
        # Get Monday of current week
        days_since_monday = today.weekday()  # 0 = Monday, 6 = Sunday
        monday = today - timedelta(days=days_since_monday)
        # Get Sunday of current week
        sunday = monday + timedelta(days=6)
        
        start_date = monday
        end_date = sunday
        
        new_count = 0
        updated_count = 0
        cancelled_count = 0
        errors = []
        
        current_date = start_date
        while current_date <= end_date:
            try:
                booked_classes = scraper.get_user_booked_classes(box_url, user.athlete_id, current_date)
                
                # Get existing bookings for this date
                existing_bookings = {
                    wb.class_id: wb 
                    for wb in db.session.query(WodBusterBooking).filter_by(
                        user_id=user.id,
                        class_date=current_date,
                        is_cancelled=False
                    ).all()
                }
                
                # Track which bookings we found in the API response
                found_class_ids = set()
                
                for class_info in booked_classes:
                    class_id = class_info['class_id']
                    class_time = class_info['time']
                    found_class_ids.add(class_id)
                    
                    # Check if booking already exists
                    if class_id in existing_bookings:
                        # Update existing booking
                        existing = existing_bookings[class_id]
                        existing.class_name = class_info.get('class_name')
                        existing.class_type = class_info.get('class_type')
                        existing.fetched_at = datetime.now()
                        existing.is_cancelled = False
                        updated_count += 1
                    else:
                        # Create new booking
                        new_booking = WodBusterBooking(
                            user_id=user.id,
                            class_id=class_id,
                            class_date=current_date,
                            class_time=class_time,
                            class_name=class_info.get('class_name'),
                            class_type=class_info.get('class_type'),
                            box_url=box_url,
                            fetched_at=datetime.now(),
                            is_cancelled=False
                        )
                        db.session.add(new_booking)
                        new_count += 1
                
                # Mark bookings as cancelled if they're no longer in the API response
                for class_id, existing_booking in existing_bookings.items():
                    if class_id not in found_class_ids:
                        existing_booking.is_cancelled = True
                        existing_booking.fetched_at = datetime.now()
                        cancelled_count += 1
                
            except Exception as e:
                error_msg = f"Error syncing date {current_date}: {str(e)}"
                logging.exception(error_msg)
                errors.append(error_msg)
            
            current_date += timedelta(days=1)
        
        db.session.commit()
        logging.info("Sync completed for user %s: %d new, %d updated, %d cancelled", 
                    user.email, new_count, updated_count, cancelled_count)
        
        return {
            'success': True,
            'new': new_count,
            'updated': updated_count,
            'cancelled': cancelled_count,
            'errors': errors
        }
        
    except Exception as e:
        logging.exception("Error syncing WodBuster bookings for user %s", user.email)
        db.session.rollback()
        return {
            'success': False,
            'new': 0,
            'updated': 0,
            'cancelled': 0,
            'errors': [f'Sync failed: {str(e)}']
        }
