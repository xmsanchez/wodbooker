from datetime import datetime, timedelta, date, time
import logging
import pause
import pytz
from flask import current_app as app
from func_timeout import StoppableThread
from requests.exceptions import RequestException

from .scraper import get_scraper
from .exceptions import BookingNotAvailable, InvalidWodBusterResponse, \
    ClassIsFull, LoginError, PasswordRequired, InvalidBox
from .models import db, Booking

_MADRID_TZ = pytz.timezone('Europe/Madrid')

__CURRENT_THREADS = {
}

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
        day_to_book = _get_next_date_for_weekday(now.date(), dow)

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
            wait_for_event = None
            wait_for_datetime = None
            datetime_to_book = None
            while errors < 5 and not force_exit:
                try:
                    # Refresh the scraper in case a new one is avaiable
                    scraper = get_scraper(self._booking.user.email, self._booking.user.cookie)
                    book_time = time(self._booking.time.hour, self._booking.time.minute, 0)
                    _datetime_to_book = _get_datetime_to_book(self._booking.last_book_date, self._booking.dow, book_time)
                    if (wait_for_datetime or wait_for_event) and datetime_to_book != _datetime_to_book:
                        logging.info("Waiting for class %s is over.", datetime_to_book.strftime('%d/%m/%Y %H:%M'))
                        self._set_booking_status(f"La clase del {datetime_to_book.strftime('%d/%m/%Y')} ya ha pasado y no se pudo reservar. Comenzando reserva para el {_datetime_to_book.strftime('%d/%m/%Y')}")
                        wait_for_event = None
                        wait_for_datetime = None
                    datetime_to_book = _datetime_to_book
                    day_to_book = datetime_to_book.date()

                    book_available_at = _MADRID_TZ.localize(
                        datetime.combine(
                            day_to_book - timedelta(days=self._booking.offset),
                            self._booking.available_at))

                    wait_for_datetime = wait_for_datetime or book_available_at
                    if wait_for_datetime and wait_for_datetime > datetime.now(_MADRID_TZ):
                        logging.info("Waiting until %s", wait_for_datetime.strftime('%d/%m/%Y %H:%M'))
                        self._set_booking_status(f"Esperando hasta el {wait_for_datetime.strftime('%d/%m/%Y a las %H:%M')} cuando las reservas para el {day_to_book.strftime('%d/%m/%Y')} estén disponibles")
                        pause.until(wait_for_datetime)
                    wait_for_datetime = None

                    if wait_for_event:
                        logging.info("Waiting with SSE for event %s", wait_for_event)
                        if wait_for_event == "changedPizarra":
                            self._set_booking_status(f"Esperando a que las clases del día {day_to_book.strftime('%d/%m/%Y')} estén cargadas")
                        elif wait_for_event == "changedBooking":
                            self._set_booking_status(f"La clase del {day_to_book.strftime('%d/%m/%Y')} está llena. Esperando a que haya plazas disponibles")

                        scraper.wait_until_event(self._booking.url, day_to_book, wait_for_event, datetime_to_book)
                        wait_for_event = None

                    if scraper.book(self._booking.url, datetime_to_book):
                        logging.info("Booking for user %s at %s completed successfully", self._booking.user.email, datetime_to_book.strftime('%d/%m/%Y %H:%M'))
                        self._set_booking_status(f"Reserva para el {day_to_book.strftime('%d/%m/%Y')} completada correctamente")
                        errors = 0
                    else:
                        logging.warning("Impossible to book classes for %s for %s. Class is already booked or user cannot book. Igoning week and attempting booking for next week",
                                        self._booking.user.email, datetime_to_book)
                        self._set_booking_status("La clase no se ha podido reservar por un motivo desconocido. Se ignora esta semana y se intentará reservar para el mismo día de la siguiente semana")
                        errors = 0

                    self._booking.last_book_date = day_to_book
                    self._booking.booked_at = datetime.now().replace(microsecond=0)
                    self._booking.user.cookie = scraper.get_cookies()
                    db.session.commit()
                except ClassIsFull:
                    logging.info("Class is full. Setting wait for event to 'changedBooking'")
                    wait_for_event = 'changedBooking'
                except BookingNotAvailable as e:
                    if e.available_at:
                        logging.info("Class is not bookeable yet. Setting wait for datetime to %s", e.available_at.strftime('%d/%m/%Y %H:%M'))
                        wait_for_datetime = e.available_at
                    else:
                        logging.info("Classes for %s are not loaded yet. Setting wait for event to 'changedPizarra'", day_to_book.strftime('%d/%m/%Y'))
                        wait_for_event = 'changedPizarra'
                    continue
                except RequestException as e:
                    sleep_for = (errors + 1) * 60
                    logging.warning("Request Exception: %s", e)
                    self._set_booking_status(f"Error inesperado de red. Esperando {sleep_for} segundos antes de volver a intentarlo...")
                    errors += 1
                    pause.seconds(sleep_for)
                except InvalidWodBusterResponse as e:
                    sleep_for = (errors + 1) * 60
                    logging.warning("Invalid WodBuster response: %s", e)
                    self._set_booking_status(f"Respuesta inesperada de WodBuster. Esperando {sleep_for} segundos antes de volver a intentarlo...")
                    errors += 1
                    pause.seconds(sleep_for)
                except PasswordRequired:
                    force_exit = True
                    logging.warning("Credentials for user %s are outdated. Aborting...", self._booking.user.email)
                    self._set_booking_status("Tus credenciales están caducadas. Vuelve a logarte y actualiza esta entrada para reactivar las reservas")
                except LoginError:
                    force_exit = True
                    logging.warning("User %s cannot be logged in into WodBuster. Aborting...", self._booking.user.email)
                    self._set_booking_status("Login fallido: credenciales inválidas. Vuelve a logarte y vuelve a intentarlo")
                except InvalidBox:
                    force_exit = True
                    logging.warning("User %s accessing to an invalid box detected. Aborting...", self._booking.user.email)
                    self._set_booking_status("La URL del box introducida no es válida o no tienes acceso al mismo. Actualiza la URL y vuelve a intentarlo")

            if errors >= 5:
                logging.error("Exiting thread as maximum number of retries has been reached. Review logs for more information")
                self._set_booking_status("Se han producido demasiados errores al intentar reservar. Proceso abortado")
                self._session.commit()
        except _StopThreadException:
            logging.info("Thread %s has been stopped", self._name)
        except Exception:
            logging.exception("Unexpected error while booking. Aborting...")

    def _set_booking_status(self, new_status: str) -> None:
        split_sep = '\n'
        previous_status = self._booking.status.split(split_sep) if self._booking.status else []
        if not previous_status or new_status not in previous_status[-1]:
            current_date = datetime.now().strftime('%d/%m/%Y %H:%M')
            updated_status = previous_status[-10:] + [f"{current_date}: {new_status}"]
            self._booking.status = split_sep.join(updated_status)
            db.session.commit()


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
