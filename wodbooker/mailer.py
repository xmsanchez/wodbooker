import logging
from abc import abstractmethod, ABC
from queue import Queue
import boto3
from botocore.exceptions import ClientError
from .models import User
from .constants import DAYS_OF_WEEK

client = boto3.client('ses',region_name="eu-west-1")

_queue = Queue()

_SENDER = "WodBooker <wodbooker@aitormagan.es>"
_CHARSET = "UTF-8"
_ERROR_HTML_TEMPLATE = """<html>
    <head></head>
    <body>
        <h3>WodBooker - Error en la reserva del %s a las %s:</h3>
        <p>%s</a>.</p>
    </body>
</html>"""


class Email(ABC):
    """
    Email templates
    """

    @abstractmethod
    def get_html(self):
        """
        Returns the mail HTML
        """

    @abstractmethod
    def get_plain_body(self):
        """
        Returns the mail plain body
        """

    @abstractmethod
    def get_subject(self):
        """
        Returns the mail subject
        """


class ErrorEmail(Email):
    """
    Error email template
    """
    def __init__(self, booking, subject, error):
        self.booking = booking
        self.error = error
        self.subject = subject

    def get_html(self):
        return _ERROR_HTML_TEMPLATE % (DAYS_OF_WEEK[self.booking.dow], 
                                       self.booking.time.strftime("%H:%M"), 
                                       self.error)

    def get_plain_body(self):
        return self.error

    def get_subject(self):
        return f"[WodBooker] {self.subject}"


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
