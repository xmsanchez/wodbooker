from enum import StrEnum

DAYS_OF_WEEK = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

# Default offset values for each day of the week (0=Monday, 6=Sunday)
# Updated with correct values: Saturday=7, Sunday=1, Monday=2, etc.
DEFAULT_OFFSETS_BY_DAY = {
    0: 2,  # Monday: 2 days in advance
    1: 3,  # Tuesday: 3 days in advance  
    2: 4,  # Wednesday: 4 days in advance
    3: 5,  # Thursday: 5 days in advance
    4: 6,  # Friday: 6 days in advance
    5: 7,  # Saturday: 7 days in advance
    6: 1,  # Sunday: 1 day in advance
}

UNEXPECTED_ERROR_MAIL_SUBJECT = "Error en la reserva"
UNEXPECTED_ERROR_MAIL_BODY = "En este momento es imposible gestionar tu reserva por un error inesperado. Te recomendamos acceder a WodBuster y hacer la reserva manualmente"
FULL_CLASS_BOOKED_MAIL_SUBJECT = "Clase reservada"
FULL_CLASS_BOOKED_MAIL_BODY = "¡Enhorabuena! Parece que se quedó una plaza libre y he conseguido reservarte la clase. ¡Nos vemos en el box!"
ERROR_AUTOHEALED_MAIL_SUBJECT = "Clase reservada"
ERROR_AUTOHEALED_MAIL_BODY = "Parece que he podido recuperarme del error y he conseguido reservar la clase. ¡A darlo todo en el box!"
CLASS_BOOKED_MAIL_SUBJECT = "Clase reservada con éxito"
CLASS_BOOKED_MAIL_BODY = "La clase se ha reservado con éxito. ¡Disfruta del WOD!"


class EventMessage(StrEnum):
    CLASS_WAITING_OVER = "La clase del %s ya ha pasado y no se pudo reservar. Comenzando reserva para el %s"
    WAIT_UNTIL_BOOKING_OPEN = "Esperando hasta el %s cuando las reservas para el %s estén disponibles"
    BOOKING_COMPLETED = "Reserva para el %s completada correctamente"
    CLASS_FULL = "La clase del %s está llena. Esperando a que haya plazas disponibles"
    BOOKING_PENALIZATION = "%s. Se intentará de nuevo en cuanto termine la cuenta atrás."
    WAIT_CLASS_LOADED = "Esperando a que las clases del día %s estén cargadas"
    UNEXPECTED_NETWORK_ERROR = "Error inesperado de red. Esperando %s segundos antes de volver a intentarlo..."
    UNEXPECTED_WODBUSTER_RESPONSE = "Respuesta inesperada de WodBuster. Esperando %s segundos antes de volver a intentarlo..."
    CREDENTIALS_EXPIRED = "Tus credenciales están caducadas. Vuelve a logarte y edita esta reserva para que vuelva a activarse"
    LOGIN_FAILED = "Login fallido: credenciales inválidas. Vuelve a logarte y vuelve a intentarlo"
    INVALID_BOX_URL = "La URL del box introducida no es válida o no tienes acceso al mismo. Actualiza la URL y vuelve a intentarlo"
    TOO_MANY_ERRORS = "Se han producido demasiados errores al intentar reservar. Reserva parada"
    _IGNORE_WEEK_MESSAGE = "Se ignora esta semana y se intentará reservar para el mismo día de la siguiente semana"
    CLASS_NOT_FOUND = f"El %s no hay clase a las %s. {_IGNORE_WEEK_MESSAGE}"
    BOOKING_ERROR = f"Error al reservar la clase del %s: %s. {_IGNORE_WEEK_MESSAGE}"
    PAUSED = "Pausado"

    def __str__(self):
        return self.value
