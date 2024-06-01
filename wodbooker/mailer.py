import logging
from enum import Enum
from abc import abstractmethod, ABC
from queue import Queue
import boto3
from botocore.exceptions import ClientError
from .models import User
from .constants import DAYS_OF_WEEK

client = boto3.client('ses',region_name="eu-west-1")

_queue = Queue()

_SENDER = "WodBooker <wodbooker@xavimiranda.es>"
_CHARSET = "UTF-8"
_HOST = "home.xavimiranda.es"
_ERROR_HTML_TEMPLATE = """<html>
    <head></head>
    <body>
        <h3>WodBooker - Error en la reserva del {1} a las {2}:</h3>
        <p>{3}</a>.</p>
        <p style="font-size: small">Box: <a href="{4}">{4}</a>
        Puedes consultar todos los eventos asociados a esta reserva <a href="https://{0}/event/?search=%3D{5}">aquí</a>.</p>
        <p style="font-size: small">Mensaje automático generado por <a href="https://{0}">WodBooker</a>.
        Gestiona tus preferenias de notificaciones <a href="https://{0}/user/">aquí</a>.
        </p>
    </body>
</html>"""

_SUCCESS_HTML_TEMPLATE = """<html>
    <head></head>
    <body>
        <h3>WodBooker - Reservada con éxito la clase del {1} a las {2}:</h3>
        <p>{3}</a></p>
        <p style="font-size: small">Box: <a href="{4}">{4}</a>
        Puedes consultar todos los eventos asociados a esta reserva <a href="https://{0}/event/?search=%3D{5}">aquí</a>.</p>
        <p style="font-size: small">Mensaje automático generado por <a href="https://{0}">WodBooker</a>.
        Gestiona tus preferenias de notificaciones <a href="https://{0}/user/">aquí</a>.
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
        self.error = error
        self.subject = subject

    def required_permission(self):
        return EmailPermissions.FAILURE

    def get_html(self):
        return _ERROR_HTML_TEMPLATE.format(_HOST, DAYS_OF_WEEK[self.booking.dow],
                                           self.booking.time.strftime("%H:%M"),
                                           self.error, self.booking.url,
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
        self.message = message

    def required_permission(self):
        return EmailPermissions.SUCCESS

    def get_html(self):
        return _SUCCESS_HTML_TEMPLATE.format(_HOST, DAYS_OF_WEEK[self.booking.dow],
                                             self.booking.time.strftime("%H:%M"),
                                             self.message, self.booking.url,
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
    pass
    # _queue.put((user, email))


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
            client.send_email(
                Destination={
                    'ToAddresses': [
                        to,
                    ],
                },
                Message={
                    'Body': {
                        'Html': {
                            'Charset': _CHARSET,
                            'Data': email.get_html(),
                        },
                        'Text': {
                            'Charset': _CHARSET,
                            'Data': email.get_plain_body(),
                        },
                    },
                    'Subject': {
                        'Charset': _CHARSET,
                        'Data': email.get_subject(),
                    },
                },
                Source=_SENDER,
            )
            logging.info("Email '%s' sent to %s successfully", email.get_subject(), to)
        except ClientError:
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
