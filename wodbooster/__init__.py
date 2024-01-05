import os
import os.path as op
import logging
from flask import Flask, redirect, request, session
from flask_sqlalchemy import SQLAlchemy

from flask_admin import Admin
import flask_login as login
from flask_admin.contrib import sqla
from flask_babelex import Babel
from .views import MyAdminIndexView, BookingAdmin, EventView
from .models import User, Booking, Event, db
from .booker import start_booking_loop

# Configure logging
logging.basicConfig(format='%(asctime)s - %(threadName)s - %(message)s', level=logging.INFO)

# Create application
app = Flask(__name__)
babel = Babel(app)

@babel.localeselector
def get_locale():
    if request.args.get('lang'):
        session['lang'] = request.args.get('lang')
    return session.get('lang', 'es')

# Create dummy secrey key so we can use sessions
app.config['SECRET_KEY'] = '123456790'

# Create in-memory database
app.config['DATABASE_FILE'] = 'db.sqlite'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + \
    app.config['DATABASE_FILE'] + '?check_same_thread=False'
app.config['SQLALCHEMY_ECHO'] = False
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Build a sample db on the fly, if one does not exist yet.
app_dir = op.realpath(os.path.dirname(__file__))
database_path = op.join(app_dir, app.config['DATABASE_FILE'])
if not os.path.exists(database_path):
    db.app = app
    db.init_app(app)
    db.create_all()
else:
    db.init_app(app)


def init_login():
    login_manager = login.LoginManager()
    login_manager.init_app(app)

    # Create user loader function
    @login_manager.user_loader
    def load_user(user_id):
        return db.session.query(User).get(user_id)


@app.route('/')
def index():
    return redirect('/admin')

init_login()

# Create admin
admin = Admin(app, name='WodBooster', index_view=MyAdminIndexView(),
              base_template='base.html', template_mode='bootstrap4')

# Add views
admin.add_view(BookingAdmin(Booking, db.session, 'Reservas'))
admin.add_view(EventView(Event, db.session, 'Eventos'))

# Start booking loop
with app.app_context():
    bookings = db.session.query(Booking).all()
    for booking in bookings:
        start_booking_loop(booking)
