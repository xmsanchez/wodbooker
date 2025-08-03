import logging
import string
import os
from enum import Enum
from abc import abstractmethod, ABC
from queue import Queue
import smtplib
from email.mime.text import MIMEText
from email.message import EmailMessage
from .models import User
from .constants import DAYS_OF_WEEK

_queue = Queue()

_SENDER = os.getenv('EMAIL_SENDER')
_MAIL = os.getenv('EMAIL_USER')
_PASSWORD = os.getenv('EMAIL_PASSWORD')
_HOST = os.getenv('WODBOOKER_HOST')

_HTML_TEMPLATE = """<html>
    <head></head>
    <body>
        <h3>WodBooker - {1}:</h3>
        <p>{2}</p>
        <p style="font-size: small">Box: <a href="{3}">{3}</a>
        Puedes consultar todos los eventos asociados a esta reserva <a href="https://{0}/event/?search=%3D{4}">aquí</a>.</p>
        <p style="font-size: small">Mensaje automático generado por <a href="https://{0}">WodBooker</a>.
        Gestiona tus preferencias de notificaciones <a href="https://{0}/user/">aquí</a>.
        </p>
    </body>
</html>"""

class EmailPermissions(Enum):
    FAILURE = "mail_permission_failure"
    SUCCESS = "mail_permission_success"


class Email(ABC):
    """
    Email templates
    """

    def __init__(self, subject):
        self.subject = subject

    @abstractmethod
    def get_html(self) -> str:
        """
        Returns the mail HTML
        """

    @abstractmethod
    def get_plain_body(self) -> str:
        """
        Returns the mail plain body
        """

    def get_subject(self) -> str:
        """
        Returns the mail subject
        """
        return f"[WodBooker] {self.subject}"

    @abstractmethod
    def required_permission(self) -> EmailPermissions:
        """
        Returns the permission required to send the email
        """


class ErrorEmail(Email):
    """
    Error email template
    """
    def __init__(self, booking, subject, error):
        super().__init__(subject)
        self.booking = booking
        self.error = error if error[-1] in string.punctuation else error + "."
        self.subject = subject

    def required_permission(self):
        return EmailPermissions.FAILURE

    def get_html(self):
        title = f"Error en la reserva del " \
                f"{DAYS_OF_WEEK[self.booking.dow]} a las " \
                f"{self.booking.time.strftime('%H:%M')}"
        return _HTML_TEMPLATE.format(_HOST, title, self.error, self.booking.url,
                                     self.booking.id)

    def get_plain_body(self):
        return self.error


class SuccessEmail(Email):
    """
    Success email template
    """
    def __init__(self, booking, subject, message):
        super().__init__(subject)
        self.booking = booking
        self.message = message if message[-1] in string.punctuation else message + "."

    def required_permission(self):
        return EmailPermissions.SUCCESS

    def get_html(self):
        title = f"Reservada con éxito la clase del " \
                f"{DAYS_OF_WEEK[self.booking.dow]} a las " \
                f"{self.booking.time.strftime('%H:%M')}"
        return _HTML_TEMPLATE.format(_HOST, title, self.message, self.booking.url,
                                     self.booking.id)

    def get_plain_body(self):
        return self.message


class SuccessAfterErrorEmail(SuccessEmail):
    """
    Mails sent with a success but when an error event occurred before
    """

    def required_permission(self):
        return EmailPermissions.FAILURE


def send_email(user: User, email: Email):
    """
    Send an email asynchronously
    :param user: The user to send the email to
    :param email: The mail to be sent
    """
    _queue.put((user, email))


def _send_email(user: User, email: Email):
    """
    Send an email using Amazon SES
    :param user: The user to send the email to
    :param email: The mail to be sent
    """

    to = user.email
    mail_allowed = getattr(user, email.required_permission().value, False)

    if mail_allowed:
        try:
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp_server:
                msg = EmailMessage()
                msg.set_content(email.get_html())
                msg['To'] = to
                msg['From'] = _SENDER
                msg['Subject'] = email.get_subject()
                msg.add_alternative(email.get_html(), subtype='html')

                smtp_server.login(_MAIL, _PASSWORD)
                smtp_server.send_message(msg)

                logging.info("Email '%s' sent to %s successfully", email.get_subject(), to)
        except Exception:
            logging.exception("Error sending email")
    else:
        logging.info("Email to '%s' not sent because of permissions", to)


def process_maling_queue(app_context):
    """
    Process the email queue
    :param app_context: The application context
    """
    app_context.push()
    with app_context:
        while True:
            user, email = _queue.get()
            _send_email(user, email)
            _queue.task_done()
