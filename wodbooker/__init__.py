import os
import os.path as op
from datetime import datetime, timedelta
import time
import threading
import subprocess
import pickle
import requests
import cloudscraper
import logging
from logging.handlers import TimedRotatingFileHandler
from flask import Flask, redirect, request, session, g, jsonify, render_template, flash, url_for

from flask_admin import Admin
import flask_login as login
from flask_babel import Babel
from flask_wtf.csrf import CSRFProtect
from .views import MyAdminIndexView, BookingAdmin, EventView, UserView
from .models import User, Booking, Event, db, PushSubscription, WodBusterBooking
from .booker import start_booking_loop, stop_booking_loop, is_booking_running, sync_wodbuster_bookings, _get_next_date_for_weekday, _MADRID_TZ
from .scraper import refresh_scraper, get_scraper
from .constants import DAYS_OF_WEEK
from .exceptions import InvalidWodBusterResponse, PasswordRequired, LoginError
from .mailer import process_maling_queue
from .notification_scheduler import _notification_scheduler_loop

# Configure logging
# Create logs directory if it doesn't exist
app_dir = op.realpath(os.path.dirname(__file__))
project_dir = op.dirname(app_dir)
logs_dir = op.join(project_dir, 'logs')
os.makedirs(logs_dir, exist_ok=True)

# Configure main logger with file and console handlers
log_format = '%(asctime)s - %(threadName)s - %(message)s'
main_logger = logging.getLogger()
main_logger.setLevel(logging.INFO)

# Remove existing handlers to avoid duplicates
main_logger.handlers.clear()

# File handler with daily rotation
log_file = op.join(logs_dir, 'wodbooker.log')
file_handler = TimedRotatingFileHandler(
    log_file,
    when='midnight',
    interval=1,
    backupCount=7,
    encoding='utf-8'
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter(log_format))
main_logger.addHandler(file_handler)

# Console handler for Docker logs
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter(log_format))
main_logger.addHandler(console_handler)

# Create high-level logger for important business events
high_level_logger = logging.getLogger('high_level')
high_level_logger.setLevel(logging.INFO)
high_level_logger.propagate = False  # Don't propagate to root logger

# High-level file handler
high_level_log_file = op.join(logs_dir, 'wodbooker-high-level.log')
high_level_file_handler = TimedRotatingFileHandler(
    high_level_log_file,
    when='midnight',
    interval=1,
    backupCount=7,
    encoding='utf-8'
)
high_level_file_handler.setLevel(logging.INFO)
high_level_file_handler.setFormatter(logging.Formatter(log_format))
high_level_logger.addHandler(high_level_file_handler)

# High-level console handler
high_level_console_handler = logging.StreamHandler()
high_level_console_handler.setLevel(logging.INFO)
high_level_console_handler.setFormatter(logging.Formatter(log_format))
high_level_logger.addHandler(high_level_console_handler)

# Configure Flask/Werkzeug loggers to WARNING level to filter out HTTP request noise
logging.getLogger('werkzeug').setLevel(logging.WARNING)
logging.getLogger('flask').setLevel(logging.WARNING)

# # Get version
# __git_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/.git"
# _VERSION = subprocess.check_output(["git", f"--git-dir={__git_dir}",
#                                         "describe", "--tags"]).strip().decode('utf-8')


def get_locale():
    if request.args.get('lang'):
        session['lang'] = request.args.get('lang')
    return session.get('lang', 'es')

# Create application
app = Flask(__name__)
babel = Babel(app, locale_selector=get_locale)
csrf = CSRFProtect()
csrf.init_app(app)

# Create dummy secrey key so we can use sessions
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', '123456790')

# Create in-memory database
app.config['DATABASE_FILE'] = 'db.sqlite'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + \
    app.config['DATABASE_FILE'] + '?check_same_thread=False'
app.config['SQLALCHEMY_ECHO'] = False
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['CSRF_ENABLED'] = True
app.config['RECAPTCHA_PUBLIC_KEY'] = os.environ.get('RECAPTCHA_PUBLIC_KEY')
app.config['RECAPTCHA_PRIVATE_KEY'] = os.environ.get('RECAPTCHA_PRIVATE_KEY')

# VAPID keys for Web Push API
app.config['VAPID_PUBLIC_KEY'] = os.environ.get('VAPID_PUBLIC_KEY')
app.config['VAPID_PRIVATE_KEY'] = os.environ.get('VAPID_PRIVATE_KEY')
app.config['VAPID_CLAIM_EMAIL'] = os.environ.get('VAPID_CLAIM_EMAIL', 'mailto:admin@example.com')

# Build a sample db on the fly, if one does not exist yet.
app_dir = op.realpath(os.path.dirname(__file__))
database_path = op.join(app_dir, app.config['DATABASE_FILE'])

# Check and run migration BEFORE initializing SQLAlchemy to avoid model metadata issues
if os.path.exists(database_path):
    import sqlite3
    migration_needed = False
    try:
        # Check if migration is needed using raw SQLite connection
        conn = sqlite3.connect(database_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(user)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'push_notifications_enabled' not in columns:
            migration_needed = True
        conn.close()
    except Exception as e:
        logging.warning("Could not check migration status: %s", str(e))
        # Assume migration is needed if we can't check
        migration_needed = True
    
    if migration_needed:
        logging.info("Running migration v1.9.0...")
        try:
            import os as os_module
            # Read migration script
            migration_dir = op.join(op.dirname(op.dirname(app_dir)), 'migrations', 'v1.9.0')
            migration_file = op.join(migration_dir, 'push_notifications.sql')
            
            if not os_module.path.exists(migration_file):
                logging.error("Migration script not found at %s", migration_file)
                logging.error("Please run manually: python migrate.py v1.9.0")
                raise Exception("Migration script not found")
            
            with open(migration_file, 'r', encoding='utf-8') as f:
                script = f.read()
            
            # Execute migration using raw SQLite connection
            conn = sqlite3.connect(database_path)
            cursor = conn.cursor()
            
            script_statements = [s.strip() for s in script.split(";") if s.strip()]
            for statement in script_statements:
                if not statement:
                    continue
                try:
                    cursor.execute(statement)
                except sqlite3.OperationalError as e:
                    error_msg = str(e).lower()
                    if 'duplicate column' in error_msg or 'already exists' in error_msg:
                        logging.warning("Column or table already exists, skipping: %s", statement[:50])
                        continue
                    # Re-raise if it's a different error
                    logging.error("Migration error: %s", str(e))
                    logging.error("Statement: %s", statement[:200])
                    conn.close()
                    raise
            
            conn.commit()
            conn.close()
            logging.info("Migration v1.9.0 completed successfully")
            
        except Exception as e:
            logging.error("Error running migration v1.9.0: %s", str(e))
            logging.error("Please run manually: python migrate.py v1.9.0")
            raise  # Fail startup if migration fails

# Now initialize SQLAlchemy (after migration is complete)
if not os.path.exists(database_path):
    db.app = app
    with app.app_context():
        db.init_app(app)
        db.create_all()
else:
    db.init_app(app)


def _init_login():
    login_manager = login.LoginManager()
    login_manager.init_app(app)

    # Create user loader function
    @login_manager.user_loader
    def load_user(user_id):
        return db.session.query(User).get(user_id)


@app.before_request
def check_session_expired():
    """
    Check if the session has expired and logout the user if it has
    """
    if "static" not in request.path and login.current_user.is_authenticated:
        if login.current_user.force_login:
            login.logout_user()
        else:
            _session = cloudscraper.create_scraper()
            _session.cookies.update(pickle.loads(login.current_user.cookie))
            try:
                expiration_timestamp = next(x for x in _session.cookies if x.name == '.WBAuth').expires
                expiration_date = datetime.fromtimestamp(expiration_timestamp)
                if datetime.now() > expiration_date:
                    login.logout_user()
            except (StopIteration, TypeError):
                logging.exception("Error while getting expiration date of cookie")


@app.before_request
def set_version():
    """
    Set version in g object
    """
    # Set a proper version or leave empty instead of "DUMMY"
    g.version = "1.0.0"


@app.before_request
def redirect_admin():
    """
    Redirect users from deprecated /admin/... to /...
    """
    if request.path.startswith('/admin'):
        return redirect(request.full_path.replace('/admin', ''))


def get_vapid_public_key():
    """
    Get VAPID public key for frontend
    """
    return app.config.get('VAPID_PUBLIC_KEY')


def get_vapid_private_key():
    """
    Get VAPID private key for backend
    """
    return app.config.get('VAPID_PRIVATE_KEY')


def get_vapid_claim_email():
    """
    Get VAPID claim email
    """
    return app.config.get('VAPID_CLAIM_EMAIL')


# Push notification API endpoints
@app.route('/api/push/vapid-public-key', methods=['GET'])
def vapid_public_key():
    """
    Return VAPID public key for frontend
    """
    try:
        public_key = get_vapid_public_key()
        if not public_key:
            logging.error("VAPID_PUBLIC_KEY not configured in environment variables")
            logging.error("Please set VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY environment variables")
            return jsonify({
                'error': 'VAPID public key not configured',
                'message': 'Las claves VAPID no están configuradas. Por favor, contacta al administrador.'
            }), 500
        logging.info("VAPID public key retrieved successfully (length: %s)", len(public_key))
        return jsonify({'publicKey': public_key})
    except Exception as e:
        logging.exception("Error retrieving VAPID public key")
        return jsonify({
            'error': 'Error retrieving VAPID public key',
            'message': str(e)
        }), 500


@app.route('/api/push/subscribe', methods=['POST'])
@login.login_required
@csrf.exempt
def push_subscribe():
    """
    Register push subscription
    Note: Exempted from CSRF as it's already protected by login_required
    """
    logging.info("=== Push subscription request received ===")
    logging.info("User ID: %s, Email: %s", login.current_user.id, login.current_user.email)
    
    try:
        # Log raw request data
        raw_data = request.get_data(as_text=True)
        logging.info("Raw request data: %s", raw_data[:500] if len(raw_data) > 500 else raw_data)
        
        data = request.get_json()
        if not data:
            logging.error("No JSON data in request")
            return jsonify({'error': 'Invalid request - no JSON data'}), 400
        
        logging.info("Parsed JSON data keys: %s", list(data.keys()))
        
        endpoint = data.get('endpoint')
        keys = data.get('keys', {})
        p256dh = keys.get('p256dh') if keys else None
        auth = keys.get('auth') if keys else None
        
        logging.info("Endpoint: %s", endpoint[:100] + '...' if endpoint and len(endpoint) > 100 else endpoint)
        logging.info("Has p256dh: %s", bool(p256dh))
        logging.info("Has auth: %s", bool(auth))
        logging.info("p256dh length: %s", len(p256dh) if p256dh else 0)
        logging.info("auth length: %s", len(auth) if auth else 0)
        
        if not endpoint:
            logging.error("Missing endpoint")
            return jsonify({'error': 'Missing required field: endpoint'}), 400
        if not p256dh:
            logging.error("Missing p256dh key")
            return jsonify({'error': 'Missing required field: p256dh'}), 400
        if not auth:
            logging.error("Missing auth key")
            return jsonify({'error': 'Missing required field: auth'}), 400
        
        # Check if subscription already exists
        logging.info("Checking for existing subscription...")
        existing = db.session.query(PushSubscription).filter_by(
            user_id=login.current_user.id,
            endpoint=endpoint
        ).first()
        
        if existing:
            logging.info("Updating existing subscription (ID: %s)", existing.id)
            existing.p256dh = p256dh
            existing.auth = auth
        else:
            logging.info("Creating new subscription...")
            subscription = PushSubscription(
                user_id=login.current_user.id,
                endpoint=endpoint,
                p256dh=p256dh,
                auth=auth
            )
            db.session.add(subscription)
            logging.info("Subscription object created: %s", subscription)
        
        logging.info("Committing to database...")
        db.session.commit()
        logging.info("=== Push subscription successful ===")
        return jsonify({'success': True}), 200
        
    except Exception as e:
        logging.exception("=== Error subscribing to push notifications ===")
        logging.error("Exception type: %s", type(e).__name__)
        logging.error("Exception message: %s", str(e))
        try:
            db.session.rollback()
            logging.info("Database session rolled back")
        except Exception as rollback_error:
            logging.error("Error during rollback: %s", str(rollback_error))
        return jsonify({'error': str(e)}), 500


@app.route('/api/push/unsubscribe', methods=['POST'])
@login.login_required
@csrf.exempt
def push_unsubscribe():
    """
    Remove push subscription
    Note: Exempted from CSRF as it's already protected by login_required
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid request'}), 400
        
        endpoint = data.get('endpoint')
        if not endpoint:
            return jsonify({'error': 'Missing endpoint'}), 400
        
        subscription = db.session.query(PushSubscription).filter_by(
            user_id=login.current_user.id,
            endpoint=endpoint
        ).first()
        
        if subscription:
            db.session.delete(subscription)
            db.session.commit()
            return jsonify({'success': True}), 200
        else:
            return jsonify({'error': 'Subscription not found'}), 404
        
    except Exception as e:
        logging.exception("Error unsubscribing from push notifications")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/push/test', methods=['POST'])
@login.login_required
@csrf.exempt
def push_test():
    """
    Test push notification endpoint - sends a notification immediately without time checks
    Note: Exempted from CSRF as it's already protected by login_required
    """
    try:
        user = login.current_user
        
        # Check if user has push notifications enabled
        if not user.push_notifications_enabled:
            return jsonify({
                'success': False,
                'error': 'Push notifications are not enabled for your account'
            }), 400
        
        # Check if user has any subscriptions
        subscriptions = db.session.query(PushSubscription).filter_by(user_id=user.id).all()
        if not subscriptions:
            return jsonify({
                'success': False,
                'error': 'No push subscriptions found. Please enable push notifications in your browser first.'
            }), 400
        
        delay_seconds = 5

        # Import here to avoid circular imports
        from .push_notifications import send_push_notification
        import threading
        import time
        
        # Capture user_id for the thread (user object won't be accessible in thread)
        user_id = user.id
        
        def send_test_notification(user_id, delay_seconds):
            """Helper function to send notification (with optional delay)"""
            # Create new app context for the thread
            with app.app_context():
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                
                # Re-query user in the new context
                thread_user = db.session.query(User).filter_by(id=user_id).first()
                if not thread_user:
                    logging.error("User %s not found in thread context", user_id)
                    return

                # Send a generic test notification
                title = "Wodbooker - Recordatorio de clase"
                body = "Esta es una notificación de prueba."
                
                # Re-query subscriptions in the new context
                thread_subscriptions = db.session.query(PushSubscription).filter_by(user_id=user_id).all()
                for subscription in thread_subscriptions:
                    send_push_notification(subscription, title, body, {'test': True})
        
        # Start thread to send notification (with optional delay)
        thread = threading.Thread(
            target=send_test_notification,
            args=(user_id, delay_seconds),
            daemon=True
        )
        thread.start()
        
        delay_msg = f" (se enviará en {delay_seconds} segundos)"
        return jsonify({
            'success': True,
            'message': f'Notificación de prueba programada {delay_msg}',
            'delay_seconds': delay_seconds,
            'subscription_count': len(subscriptions)
        }), 200
        
    except Exception as e:
        logging.exception("Error testing push notifications")
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/wodbuster/sync', methods=['POST'])
@login.login_required
@csrf.exempt
def wodbuster_sync():
    """
    Auto-sync WodBuster bookings endpoint (AJAX-compatible)
    Note: Exempted from CSRF as it's already protected by login_required
    """
    try:
        result = sync_wodbuster_bookings(login.current_user)
        if result['success']:
            return jsonify({
                'success': True,
                'new': result['new'],
                'updated': result['updated'],
                'cancelled': result['cancelled'],
                'message': f"Sincronización completada: {result['new']} nuevas, {result['updated']} actualizadas, {result['cancelled']} canceladas"
            }), 200
        else:
            error_msg = "; ".join(result['errors'])
            return jsonify({
                'success': False,
                'error': error_msg
            }), 500
    except Exception as e:
        logging.exception("Error in sync endpoint")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/weekly-classes')
@login.login_required
def weekly_classes():
    """
    Display weekly class summary for the next 7 days
    """
    try:
        user = login.current_user
        
        # Get box URL from user's most recent booking
        box_url = None
        last_booking = db.session.query(Booking).filter_by(user_id=user.id).order_by(Booking.id.desc()).first()
        if last_booking and last_booking.url:
            box_url = last_booking.url
        else:
            # Try to get box URL directly
            try:
                scraper = get_scraper(user.email, user.cookie)
                box_url = scraper.get_box_url()
            except Exception as e:
                logging.warning("Could not get box URL for user %s: %s", user.email, str(e))
                flash("No se pudo obtener la URL del box. Por favor, crea una reserva primero.", "error")
                return redirect(url_for('booking.index_view'))
        
        if not box_url:
            flash("No se encontró URL del box. Por favor, crea una reserva primero.", "error")
            return redirect(url_for('booking.index_view'))
        
        # Calculate start date for the week to show
        today = datetime.now().date()
        days_until_monday = (7 - today.weekday()) % 7
        start_date = today + timedelta(days=days_until_monday if days_until_monday > 0 else 0)

        # Check if we should instead show the week after the next one
        now = datetime.now(_MADRID_TZ)
        user_bookings = db.session.query(Booking).filter_by(user_id=user.id).all()
        
        should_show_next_week = False
        for booking in user_bookings:
            next_week_class_date = _get_next_date_for_weekday(start_date, booking.dow)
            booking_opens_date = next_week_class_date - timedelta(days=booking.offset)
            if booking.available_at:
                booking_opens_datetime = _MADRID_TZ.localize(
                    datetime.combine(booking_opens_date, booking.available_at)
                )
                if now >= booking_opens_datetime:
                    should_show_next_week = True
                    break
        
        if should_show_next_week:
            start_date = start_date + timedelta(days=7)
        
        # Get scraper and fetch week classes
        scraper = get_scraper(user.email, user.cookie)
        athlete_id = user.athlete_id if user.athlete_id else None
        
        week_classes = scraper.get_week_classes(box_url, start_date, athlete_id)
        
        # Map class type IDs to colors (use NombreE from JSON for the name)
        class_color_map_by_id = {
            1: '#059669',  # green - Wod
            2: '#000000',  # black - Open Box
            7: '#000000',  # black - Open Box*
            9: '#2563eb',  # blue - Gymnastics
            10: '#be185d',  # dark pink - Teens
            14: '#64748b',  # gray - Adapted Training
            17: '#eab308',  # yellow - Minimal
        }
        
        # Map class names to colors (takes precedence over ID mapping)
        class_color_map_by_name = {
            'GAP': '#ec4899',  # pink
            'ENDURANCE': '#0ea5e9',  # light blue
        }
        
        # Process classes for template
        processed_classes = {}
        for date, classes in week_classes.items():
            processed_classes[date] = []
            for cls in classes:
                id_e = cls.get('IdE')
                # Use NombreE from JSON as the friendly name
                friendly_name = cls.get('NombreE', f'Type {id_e}')
                # Get color: first check by name (uppercase), then by ID, default to gray
                friendly_name_upper = friendly_name.upper()
                if friendly_name_upper in class_color_map_by_name:
                    color = class_color_map_by_name[friendly_name_upper]
                else:
                    color = class_color_map_by_id.get(id_e, '#64748b')
                processed_classes[date].append({
                    'time': cls.get('Hora', ''),
                    'name': friendly_name,
                    'type': friendly_name,
                    'color': color,
                    'id': cls.get('Id'),
                    'id_e': id_e
                })
            # Sort classes by time
            processed_classes[date].sort(key=lambda x: x['time'])
        
        end_date = start_date + timedelta(days=6)
        
        return render_template('weekly_classes.html', 
                             week_classes=processed_classes,
                             start_date=start_date,
                             end_date=end_date,
                             DAYS_OF_WEEK=DAYS_OF_WEEK,
                             box_url=box_url)
    
    except (InvalidWodBusterResponse, PasswordRequired, LoginError) as e:
        logging.exception("Error fetching weekly classes")
        flash(f"Error al obtener las clases: {str(e)}", "error")
        return redirect(url_for('booking.index_view'))
    except Exception as e:
        logging.exception("Unexpected error in weekly_classes route")
        flash(f"Error inesperado: {str(e)}", "error")
        return redirect(url_for('booking.index_view'))


_init_login()

# Create admin
admin = Admin(app, name='WodBooker', index_view=MyAdminIndexView(url="/"),
              base_template='base.html', template_mode='bootstrap4')

# Add views
admin.add_view(BookingAdmin(Booking, db.session, 'Reservas'))
admin.add_view(EventView(Event, db.session, 'Eventos'))
admin.add_view(UserView(User, db.session, 'Preferencias'))

# Start booking loop
with app.app_context():
    _bookings = db.session.query(Booking).all()
    for _booking in _bookings:
        if _booking.is_active:
            start_booking_loop(_booking)

# Start events cleaning loop
def _cleaning_loop(app_context):
    app_context.push()
    with app_context:
        while True:
            high_level_logger.info("Cleaning events older than 15 days")
            bookings = db.session.query(Booking).all()
            for booking in bookings:
                events_older_than_15_days = list(filter(lambda x: x.date < datetime.now() - timedelta(days=15),
                                                        booking.events[:-1]))
                events_older_than_15_days = sorted(events_older_than_15_days, key=lambda x: x.date)
                for event in events_older_than_15_days:
                    db.session.delete(event)
            db.session.commit()
            time.sleep(60 * 60 * 24)

thread_cleaner = threading.Thread(target=_cleaning_loop,
                                  args=(app.app_context(),),
                                  daemon=True, name="dbcleaner")
thread_cleaner.start()

thread_mailer = threading.Thread(target=process_maling_queue,
                                 args=(app.app_context(),),
                                 daemon=True, name="mailer")
thread_mailer.start()

# Start notification scheduler loop
thread_notification_scheduler = threading.Thread(target=_notification_scheduler_loop,
                                                 args=(app.app_context(),),
                                                 daemon=True, name="notification_scheduler")
thread_notification_scheduler.start()
