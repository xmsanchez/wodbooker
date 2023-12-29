from datetime import datetime

class LoginError(Exception):
    """
    Raises wen login fails
    """


class InvalidWodBusterAPIResponse(Exception):
    """
    Raises when WodBuster returns a non expected response
    """


class NotLoggedUser(Exception):
    """
    Raises when an action is performed that requires to be logged
    """


class BookingNotAvailable(Exception):
    """
    Raises when a booking is not available
    """

    def __init__(self, message, available_at: datetime) -> None:
        super().__init__(message)
        self.available_at = available_at
