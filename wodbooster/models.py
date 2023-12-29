from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    dow = db.Column(db.Integer)
    time = db.Column(db.Time)
    booked_at = db.Column(db.DateTime)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    user = db.relationship('User')
    available_at = db.Column(db.DateTime)
    last_book_date = db.Column(db.Date)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    password = db.Column(db.String(100))
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
