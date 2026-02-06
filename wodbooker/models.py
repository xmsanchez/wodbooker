from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date

db = SQLAlchemy()


class Booking(db.Model):

    id = db.Column(db.Integer, primary_key=True)
    dow = db.Column(db.Integer)
    time = db.Column(db.Time)
    booked_at = db.Column(db.DateTime)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    user = db.relationship('User')
    last_book_date = db.Column(db.Date)
    url = db.Column(db.String(128))
    available_at = db.Column(db.Time)
    type_class = db.Column(db.Integer)
    offset = db.Column(db.Integer, default=0)  # Made optional with default 0
    events = db.relationship('Event', backref='booking', lazy=True, cascade="all, delete-orphan")
    is_active = db.Column(db.Boolean, default=True)


class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('booking.id'))
    date = db.Column(db.DateTime, default=datetime.now)
    event = db.Column(db.String(256))

    def __str__(self):
        return f"{self.date.strftime('%d/%m/%Y %H:%M:%S')}: {self.event}"


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True)
    cookie = db.Column(db.String(1024))
    force_login = db.Column(db.Boolean, default=False)
    mail_permission_success = db.Column(db.Boolean, default=True)
    mail_permission_failure = db.Column(db.Boolean, default=True)
    push_permission_success = db.Column(db.Boolean, default=True)
    push_permission_failure = db.Column(db.Boolean, default=True)
    athlete_id = db.Column(db.String(128), nullable=True)
    profile_picture_url = db.Column(db.String(512), nullable=True)
    wodbuster_bookings = db.relationship('WodBusterBooking', backref='user', lazy=True, cascade="all, delete-orphan")
    training_descriptions = db.relationship('ClassTrainingDescription', backref='user', lazy=True, cascade="all, delete-orphan")
    
    # Push notification settings
    push_notifications_enabled = db.Column(db.Boolean, default=False)
    push_reminder_1h = db.Column(db.Boolean, default=False)
    push_reminder_30m = db.Column(db.Boolean, default=False)
    push_reminder_15m = db.Column(db.Boolean, default=False)
    wodbuster_autosync_enabled = db.Column(db.Boolean, default=False)
    auto_sync_training_descriptions = db.Column(db.Boolean, default=False)
    
    # Push notification subscriptions
    push_subscriptions = db.relationship('PushSubscription', backref='user', lazy=True, cascade="all, delete-orphan")

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


class WodBusterBooking(db.Model):
    __tablename__ = 'wodbuster_booking'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    class_id = db.Column(db.Integer, nullable=False)
    class_date = db.Column(db.Date, nullable=False, index=True)
    class_time = db.Column(db.Time, nullable=False)
    class_name = db.Column(db.String(128), nullable=True)
    class_type = db.Column(db.String(32), nullable=True)  # 'wod', 'openbox', etc.
    box_url = db.Column(db.String(128), nullable=False)
    fetched_at = db.Column(db.DateTime, default=datetime.now)
    is_cancelled = db.Column(db.Boolean, default=False)

    # Unique constraint to prevent duplicates
    __table_args__ = (db.UniqueConstraint('user_id', 'class_id', 'class_date', name='_user_class_date_uc'),)

    def __str__(self):
        return f"{self.class_date.strftime('%d/%m/%Y')} {self.class_time.strftime('%H:%M')} - {self.class_name or 'N/A'}"
    
    # Relationship for notification tracking
    notifications_sent = db.relationship('NotificationSent', backref='wodbuster_booking', lazy=True, cascade="all, delete-orphan")


class PushSubscription(db.Model):
    __tablename__ = 'push_subscription'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    endpoint = db.Column(db.String(512), nullable=False)
    p256dh = db.Column(db.String(256), nullable=False)
    auth = db.Column(db.String(128), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    # Unique constraint to prevent duplicate subscriptions
    __table_args__ = (db.UniqueConstraint('user_id', 'endpoint', name='_user_endpoint_uc'),)


class NotificationSent(db.Model):
    __tablename__ = 'notification_sent'
    id = db.Column(db.Integer, primary_key=True)
    wodbuster_booking_id = db.Column(db.Integer, db.ForeignKey('wodbuster_booking.id'), nullable=False, index=True)
    reminder_minutes = db.Column(db.Integer, nullable=False)  # 60, 30, or 15
    sent_at = db.Column(db.DateTime, default=datetime.now)
    
    # Unique constraint to prevent duplicate notifications
    __table_args__ = (db.UniqueConstraint('wodbuster_booking_id', 'reminder_minutes', name='_booking_reminder_uc'),)


class ClassTrainingDescription(db.Model):
    __tablename__ = 'class_training_description'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    class_date = db.Column(db.Date, nullable=False, index=True)
    training_name = db.Column(db.String(128), nullable=False)  # e.g., "WOD", "CROSSFIT", "OPEN BOX"
    description = db.Column(db.Text, nullable=True)  # Cleaned text description
    id_pizarra = db.Column(db.Integer, nullable=False)  # ID to link with class (required for uniqueness)
    fetched_at = db.Column(db.DateTime, default=datetime.now)
    
    # Unique constraint to prevent duplicates per user, date, and id_pizarra
    # Using id_pizarra instead of training_name because multiple pizarras can have the same name
    __table_args__ = (db.UniqueConstraint('user_id', 'class_date', 'id_pizarra', name='_user_date_pizarra_uc'),)
    
    def __str__(self):
        return f"{self.class_date.strftime('%d/%m/%Y')} - {self.training_name}"
