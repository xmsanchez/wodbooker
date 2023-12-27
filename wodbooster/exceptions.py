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
