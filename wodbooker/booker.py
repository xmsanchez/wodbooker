from datetime import datetime, timedelta, date, time
from abc import ABC, abstractmethod
import logging
import pause
import pytz
from flask import current_app as app
from func_timeout import StoppableThread
from requests.exceptions import RequestException

from .scraper import get_scraper, Scraper
from .exceptions import BookingNotAvailable, InvalidWodBusterResponse, \
    ClassIsFull, LoginError, PasswordRequired, InvalidBox
from .models import db, Booking, Event

_MADRID_TZ = pytz.timezone('Europe/Madrid')

_MAX_ERRORS = 5

__CURRENT_THREADS = {
}

_CLASS_WAITING_OVER = "La clase del %s ya ha pasado y no se pudo reservar. Comenzando reserva para el %s"
_WAIT_UNTIL_BOOKING_OPEN = "Esperando hasta el %s cuando las reservas para el %s estén disponibles"
_BOOKING_COMPLETED = "Reserva para el %s completada correctamente"
_UNKNOWN_BOOKING_ERROR = "La clase no se ha podido reservar por un motivo desconocido. Se ignora esta semana y se intentará reservar para el mismo día de la siguiente semana"
_CLASS_FULL = "La clase del %s está llena. Esperando a que haya plazas disponibles"
_WAIT_CLASS_LOADED = "Esperando a que las clases del día %s estén cargadas"
_UNEXPECTED_NETWORK_ERROR = "Error inesperado de red. Esperando %s segundos antes de volver a intentarlo..."
_UNEXPECTED_WODBUSTER_RESPONSE = "Respuesta inesperada de WodBuster. Esperando %s segundos antes de volver a intentarlo..."
_CREDENTIALS_EXPIRED = "Tus credenciales están caducadas. Vuelve a logarte y actualiza esta entrada para reactivar las reservas"
_LOGIN_FAILED = "Login fallido: credenciales inválidas. Vuelve a logarte y vuelve a intentarlo"
_INVALID_BOX_URL = "La URL del box introducida no es válida o no tienes acceso al mismo. Actualiza la URL y vuelve a intentarlo"
_TOO_MANY_ERRORS = "Se han producido demasiados errores al intentar reservar. Reserva parada"
_PAUSED = "Pausado"

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

    def run(self) -> None:
        try:
            self._app_context.push()
            self._booking = db.session.query(Booking).filter_by(id=self._booking_id).first()
            errors = 0
            force_exit = False
            waiter = None
            datetime_to_book = None
            while errors < _MAX_ERRORS and not force_exit:
                try:
                    # Refresh the scraper in case a new one is avaiable
                    scraper = get_scraper(self._booking.user.email, self._booking.user.cookie)
                    book_time = time(self._booking.time.hour, self._booking.time.minute, 0)
                    _datetime_to_book = _get_datetime_to_book(self._booking.last_book_date, self._booking.dow, book_time)
                    if waiter and datetime_to_book != _datetime_to_book:
                        logging.info("Waiting for class %s is over.", datetime_to_book.strftime('%d/%m/%Y %H:%M'))
                        event = Event(booking_id=self._booking.id,
                                      event=_CLASS_WAITING_OVER % (datetime_to_book.strftime('%d/%m/%Y'), _datetime_to_book.strftime('%d/%m/%Y')))
                        _add_event(event)
                        waiter = None
                    datetime_to_book = _datetime_to_book
                    day_to_book = datetime_to_book.date()

                    book_available_at = _MADRID_TZ.localize(
                        datetime.combine(
                            day_to_book - timedelta(days=self._booking.offset),
                            self._booking.available_at))

                    waiter = waiter or _TimeWaiter(self._booking, _WAIT_UNTIL_BOOKING_OPEN % (book_available_at.strftime('%d/%m/%Y a las %H:%M'),
                                                                                              day_to_book.strftime('%d/%m/%Y')),
                                                   book_available_at)

                    if waiter:
                        waiter.wait()
                    waiter = None

                    if scraper.book(self._booking.url, datetime_to_book):
                        logging.info("Booking for user %s at %s completed successfully", self._booking.user.email, datetime_to_book.strftime('%d/%m/%Y %H:%M'))
                        event = Event(booking_id=self._booking.id, event=_BOOKING_COMPLETED % day_to_book.strftime('%d/%m/%Y'))
                        _add_event(event)
                        errors = 0
                    else:
                        logging.warning("Impossible to book classes for %s for %s. Class is already booked or user cannot book. Igoning week and attempting booking for next week",
                                        self._booking.user.email, datetime_to_book)
                        event = Event(booking_id=self._booking.id, event=_UNKNOWN_BOOKING_ERROR)
                        _add_event(event)
                        errors = 0

                    self._booking.last_book_date = day_to_book
                    self._booking.booked_at = datetime.now().replace(microsecond=0)
                    self._booking.user.cookie = scraper.get_cookies()
                except ClassIsFull:
                    logging.info("Class is full. Setting wait for event to 'changedBooking'")
                    waiter = _EventWaiter(self._booking, _CLASS_FULL % day_to_book.strftime('%d/%m/%Y'),
                                          scraper, self._booking.url, day_to_book, ['changedBooking'], datetime_to_book)
                except BookingNotAvailable as e:
                    if e.available_at:
                        logging.info("Class is not bookeable yet. Setting wait for datetime to %s", e.available_at.strftime('%d/%m/%Y %H:%M'))
                        waiter = waiter or _TimeWaiter(self._booking, _WAIT_UNTIL_BOOKING_OPEN % (e.available_at.strftime('%d/%m/%Y a las %H:%M'),
                                                                                                  day_to_book.strftime('%d/%m/%Y')),
                                                       e.available_at)
                    else:
                        logging.info("Classes for %s are not loaded yet. Waiting for any type of event", day_to_book.strftime('%d/%m/%Y'))
                        waiter = _EventWaiter(self._booking, _WAIT_CLASS_LOADED % day_to_book.strftime('%d/%m/%Y'),
                                              scraper, self._booking.url, day_to_book, 
                                              ['changedPizarra', 'changedBooking'], datetime_to_book)
                    continue
                except RequestException as e:
                    sleep_for = (errors + 1) * 60
                    logging.warning("Request Exception: %s", e)
                    waiter = _TimeWaiter(self._booking, _UNEXPECTED_NETWORK_ERROR % sleep_for,
                                            datetime.now(_MADRID_TZ) + timedelta(seconds=sleep_for))
                    errors += 1
                except InvalidWodBusterResponse as e:
                    sleep_for = (errors + 1) * 60
                    logging.warning("Invalid WodBuster response: %s", e)
                    waiter = _TimeWaiter(self._booking, _UNEXPECTED_WODBUSTER_RESPONSE % sleep_for,
                                         datetime.now(_MADRID_TZ) + timedelta(seconds=sleep_for))
                    errors += 1
                except PasswordRequired:
                    force_exit = True
                    logging.warning("Credentials for user %s are outdated. Aborting...", self._booking.user.email)
                    event = Event(booking_id=self._booking.id, event=_CREDENTIALS_EXPIRED)
                    _add_event(event)
                except LoginError:
                    force_exit = True
                    logging.warning("User %s cannot be logged in into WodBuster. Aborting...", self._booking.user.email)
                    event = Event(booking_id=self._booking.id, event=_LOGIN_FAILED)
                    _add_event(event)
                except InvalidBox:
                    force_exit = True
                    logging.warning("User %s accessing to an invalid box detected. Aborting...", self._booking.user.email)
                    event = Event(booking_id=self._booking.id, event=_INVALID_BOX_URL)
                    _add_event(event)
                finally:
                    db.session.commit()

            if errors >= _MAX_ERRORS:
                logging.error("Exiting thread as maximum number of retries has been reached. Review logs for more information")
                event = Event(booking_id=self._booking.id, event=_TOO_MANY_ERRORS)
                _add_event(event)
                db.session.commit()
            logging.info("Exiting thread...")
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
            logging.info("Waiting until %s", self._wait_datetime.strftime('%d/%m/%Y %H:%M:%S'))
            event = Event(booking_id=self.booking.id, event=self.log_message)
            _add_event(event)
            db.session.commit()
            pause.until(self._wait_datetime)


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
    logging.info("Starting thread for booking %s", booking.id)
    booker = Booker(booking, app.app_context())
    __CURRENT_THREADS[booking.id] = booker
    booker.start()

def stop_booking_loop(booking: Booking) -> None:
    """ 
    Stop the booking loop for a given booking 
    :param booking: The booking to stop
    """
    logging.info("Stopping thread for booking %s", booking)
    if booking.id in __CURRENT_THREADS:
        booker = __CURRENT_THREADS[booking.id]
        booker.stop(_StopThreadException)
        del __CURRENT_THREADS[booking.id]

        event = Event(booking_id=booking.id, event=_PAUSED)
        _add_event(event)
        db.session.commit()

def is_booking_running(booking: Booking) -> bool:
    """
    Check if a booking is running
    :param booking: The booking to check
    :return: True if the booking is running, False otherwise
    """
    return booking.id in __CURRENT_THREADS and __CURRENT_THREADS[booking.id].is_alive()
