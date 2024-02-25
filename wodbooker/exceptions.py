from datetime import datetime

class LoginError(Exception):
    """
    Raises wen login fails
    """


class InvalidWodBusterResponse(Exception):
    """
    Raises when WodBuster returns a non expected response
    """


class BookingNotAvailable(Exception):
    """
    Raises when a booking is not available
    """

    def __init__(self, message, available_at: datetime) -> None:
        super().__init__(message)
        self.available_at = available_at

class ClassIsFull(Exception):
    """
    Raises when a class is full
    """

class PasswordRequired(Exception):
    """
    Raises when a password is required
    """

class InvalidBox(Exception):
    """
    Raises when the provided box is invalid
    """


class ClassNotFound(Exception):
    """
    Raises when the class is not found
    """

class BookingFailed(Exception):
    """
    Raises when the booking fails
    """
