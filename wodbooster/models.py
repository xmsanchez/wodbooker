from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Booking(db.Model):

    STATUS_SEPARATOR = '\n'

    id = db.Column(db.Integer, primary_key=True)
    dow = db.Column(db.Integer)
    time = db.Column(db.Time)
    booked_at = db.Column(db.DateTime)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    user = db.relationship('User')
    last_book_date = db.Column(db.Date)
    status = db.Column(db.String(512))
    url = db.Column(db.String(128))
    available_at = db.Column(db.Time)
    offset = db.Column(db.Integer)

    def add_status(self, new_status: str) -> None:
        """
        Add a new status to the booking. If the status is the same as the last one, it is not added.
        :param new_status: The new status to add
        """
        previous_status = self.status.split(self.STATUS_SEPARATOR) if self.status else []
        if not previous_status or new_status not in previous_status[-1]:
            current_date = datetime.now().strftime('%d/%m/%Y %H:%M')
            updated_status = previous_status[-10:] + [f"{current_date}: {new_status}"]
            self.status = self.STATUS_SEPARATOR.join(updated_status)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True)
    cookie = db.Column(db.String(1024))

    # Flask-Login integration
    # NOTE: is_authenticated, is_active, and is_anonymous
    # are methods in Flask-Login < 0.3.0
    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    @property
    def is_active(self):
        return True

    def get_id(self):
        return self.id

    # Required for administrative interface
    def __unicode__(self):
        return self.email
