import logging
from datetime import datetime, date, timedelta
from collections import defaultdict
import pickle
import requests
import cloudscraper
from flask import redirect, url_for, request, flash
from wtforms import form, fields, validators
from flask_admin.form.fields import TimeField
import flask_login as login
from flask_admin import AdminIndexView, helpers, expose
from flask_admin.contrib import sqla
from flask_admin.model.template import TemplateLinkRowAction
from requests.exceptions import RequestException
from sqlalchemy import and_
from flask_wtf import FlaskForm
from flask_wtf import Recaptcha
from flask_wtf.recaptcha import RecaptchaField
from .models import User, db, Booking, WodBusterBooking, ClassTrainingDescription
from .booker import start_booking_loop, stop_booking_loop, is_booking_running, sync_training_descriptions_for_date
from .attendance_stats import sync_wodbuster_all, get_attendance_dashboard
from .scraper import refresh_scraper, get_scraper
from .exceptions import LoginError, InvalidWodBusterResponse, PasswordRequired
from .constants import EventMessage, DAYS_OF_WEEK, DEFAULT_OFFSETS_BY_DAY
from .training_description_html import (
    prepare_training_description,
    visible_text_len,
    description_is_html,
    extract_board_title,
)
import pytz

# Training description logger (file-only, no console)
training_desc_logger = logging.getLogger('training_descriptions')

_MAX_BOOKINGS_BY_USER = 30


class LoginForm(FlaskForm):
    email = fields.StringField(validators=[validators.DataRequired()])
    password = fields.PasswordField(validators=[validators.DataRequired()])
    #recaptcha = RecaptchaField(validators=[Recaptcha("Verifica que no eres un robot")])

    def __init__(self, formdata, **kwargs):
        super().__init__(formdata=formdata, **kwargs)
        self._scraper = None

    def validate(self):
        validation_result = FlaskForm.validate(self)
        if not validation_result:
            return False

        try:
            self._scraper = refresh_scraper(self.email.data, self.password.data)
        except LoginError:
            logging.exception("Login Error")
            self.password.errors.append("Las credenciales introducidas son incorrectas")
            validation_result = False
        except InvalidWodBusterResponse:
            logging.exception("Invalid WodBuster Response")
            self.password.errors.append("La respuesta de WodBuster no fue la esperada. Inténtalo de nuevo en unos minutos...")
            validation_result = False
        except RequestException:
            logging.exception("Request Error")
            self.password.errors.append("Error inesperado de red al intentar acceder. Inténtalo de nuevo en unos minutos...")
            validation_result = False

        return validation_result

    def get_user(self):
        existing_user = db.session.query(User).filter_by(email=self.email.data).first()
        if existing_user:
            existing_user.cookie = self._scraper.get_cookies()
            existing_user.force_login = False
            db.session.commit()
            user = existing_user
        else:
            user = User()
            user.email = self.email.data
            user.cookie = self._scraper.get_cookies()
            db.session.add(user)
            db.session.commit()
        
        # Try to extract athlete ID and profile picture if not already set
        if not user.athlete_id or not user.profile_picture_url:
            try:
                # Try to get box URL from existing bookings first
                box_url = None
                last_booking = db.session.query(Booking).filter_by(user_id=user.id).order_by(Booking.id.desc()).first()
                if last_booking and last_booking.url:
                    box_url = last_booking.url
                else:
                    # Try to get box URL directly (may fail if user has multiple boxes)
                    try:
                        box_url = self._scraper.get_box_url()
                    except Exception:
                        logging.warning("Could not get box URL for user %s - user may have multiple boxes", user.email)
                
                if box_url:
                    athlete_id, profile_picture_url = self._scraper.get_athlete_id(box_url)
                    if athlete_id:
                        user.athlete_id = athlete_id
                    if profile_picture_url:
                        user.profile_picture_url = profile_picture_url
                    db.session.commit()
            except Exception as e:
                logging.warning("Could not extract athlete ID/profile picture for user %s: %s", user.email, str(e))
        
        return user


# Create customized index view class that handles login & registration
class MyAdminIndexView(AdminIndexView):

    def is_visible(self):
        # This view won't appear in the menu structure
        return False

    @expose('/')
    def index(self):
        if not login.current_user.is_authenticated:
            return redirect(url_for('.login_view'))
        return redirect(url_for('booking.index_view'))

    @expose('/login/', methods=('GET', 'POST'))
    def login_view(self):
        # handle user login
        form = LoginForm(request.form)
        if helpers.validate_form_on_submit(form):
            user = form.get_user()
            login.login_user(user, remember=True)

        if login.current_user.is_authenticated:
            return redirect(url_for('booking.index_view'))
        self._template_args['form'] = form
        return super(MyAdminIndexView, self).index()

    @expose('/logout/')
    def logout_view(self):
        login.logout_user()
        return redirect(url_for('.index'))


def _configure_book_despite_cancel_window_field(form, obj=None):
    if not login.current_user.is_authenticated:
        return
    user = login.current_user
    n = user.cancel_window_hours if user.cancel_window_hours is not None else 3
    default_word = 'deshabilitado' if user.stop_autobook_in_cancel_window else 'habilitado'
    form.book_despite_cancel_window.label.text = (
        f'Forzar reserva aunque esté fuera de ventana de autoreserva ({n} horas). '
        f'Ignorará ventana de cancelación de autoreserva que está {default_word}.'
    )
    if obj is not None:
        form.book_despite_cancel_window.data = obj.book_despite_cancel_window is True


def _book_despite_cancel_window_from_form(form):
    return True if form.book_despite_cancel_window.data else None


class OffsetField(fields.IntegerField):
    """Custom field that sets default offset based on selected day of week"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.description = "Dejar en 0 para reservar el mismo día. Número de días antes de la clase cuando se abre la reserva."
    
    def process_formdata(self, valuelist):
        super().process_formdata(valuelist)
        # If no value provided, use default based on day of week
        if not self.data and hasattr(self, '_dow_field'):
            self.data = DEFAULT_OFFSETS_BY_DAY.get(self._dow_field.data, 0)
    
    def set_dow_field(self, dow_field):
        """Set reference to day of week field for dynamic default calculation"""
        self._dow_field = dow_field


class BookingForm(form.Form):

    dow = fields.SelectField('Día de la semana a reservar', choices=[(0, 'Lunes'), (1, 'Martes'), (
        2, 'Miércoles'), (3, 'Jueves'), (4, 'Viernes'), (5, 'Sábado'), (6, 'Domingo')], coerce=int)
    time = TimeField('Hora a reservar', validators=[validators.DataRequired()])
    url = fields.StringField('URL de WodBuster (ej: https://YOUR_BOX.wodbuster.com)', validators=[validators.DataRequired()])
    booking_open_day = fields.SelectField(
        'Día de apertura de reservas',
        choices=[(0, 'Lunes'), (1, 'Martes'), (2, 'Miércoles'), (3, 'Jueves'), (4, 'Viernes'), (5, 'Sábado'), (6, 'Domingo')],
        default=5,
        coerce=int
    )
    available_at = TimeField('Hora de apertura de reservas', validators=[validators.DataRequired()])
    type_class = fields.SelectField('Tipo de clase a reservar (wod, openbox)', choices=[(0, 'wod'), (1, 'openbox')], 
                                    validators=[validators.DataRequired()], 
                                    description="Algunos días puede haber simultáneamente wod y openbox. Selecciona aquí el tipo de clase que deseas reservar.")
    book_despite_cancel_window = fields.BooleanField('')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Calculate offset based on dow and booking_open_day
        try:
            class_day = int(self.dow.data) if self.dow.data is not None else 5
            open_day = int(self.booking_open_day.data) if self.booking_open_day.data is not None else 5
            offset = (class_day - open_day) % 7
            if offset == 0:
                offset = 7
            self.offset = offset
        except Exception:
            self.offset = 7  # fallback


    @staticmethod
    def calculate_booking_open_day(dow, offset):
        """
        Calculate the booking open day from the class day of week and offset
        :param dow: Day of week (0=Monday, 6=Sunday)
        :param offset: Offset in days
        :return: Booking open day (0=Monday, 6=Sunday)
        """
        if offset == 0:
            return dow
        return (dow - offset) % 7

    def validate_dow(self, field):
        if db.session.query(Booking).filter(
            and_(
                Booking.dow==field.data,
                Booking.user==login.current_user,
                Booking.url==self.url.data,
                Booking.time==self.time.data,
                Booking.id!=request.args.get('id'))).first():
            raise validators.ValidationError("Ya existe una reserva para ese día de la semana, hora y box")

class BookingAdmin(sqla.ModelView):
    form = BookingForm

    list_template = 'admin/booking/list.html'
    edit_template = 'admin/booking/edit.html'
    create_template = 'admin/booking/create.html'

    column_formatters = dict(
        dow=lambda v, c, m, p: DAYS_OF_WEEK[m.dow],
        time=lambda v, c, m, p: m.time.strftime('%H:%M'),
        available_at=lambda v, c, m, p: m.available_at.strftime('%H:%M:%S'),
        last_book_date=lambda v, c, m, p: m.last_book_date.strftime('%d/%m/%Y') if m.last_book_date else "",
        offset=lambda v, c, m, p: DAYS_OF_WEEK[m.dow - m.offset],
    )

    column_extra_row_actions = [  # Add a new action button
        TemplateLinkRowAction("row_actions.swtich_active", "Swtich Active"),
    ]

    @expose("/sync-wodbuster-bookings", methods=("POST",))
    def sync_wodbuster_bookings_endpoint(self):
        """Manual sync endpoint for WodBuster bookings"""
        if not login.current_user.is_authenticated:
            return redirect(
                url_for(
                    'admin.login_view',
                    sync_status='error',
                    sync_message='Debes iniciar sesión para sincronizar reservas',
                )
            )
        
        try:
            result = sync_wodbuster_all(login.current_user)
            if result['success']:
                return redirect(
                    url_for(
                        'booking.index_view',
                        sync_status='success',
                        sync_message="Sincronización completada",
                    )
                )
            else:
                error_msg = "; ".join(result['errors'])
                return redirect(
                    url_for(
                        'booking.index_view',
                        sync_status='error',
                        sync_message=f"Error en la sincronización: {error_msg}",
                    )
                )
        except Exception as e:
            logging.exception("Error in sync endpoint")
            return redirect(
                url_for(
                    'booking.index_view',
                    sync_status='error',
                    sync_message=f"Error al sincronizar: {str(e)}",
                )
            )

    @expose("/active", methods=("POST",))
    def switch_active(self):
        """
        Switch active status of a booking
        """
        row_id = request.form.get("row_id")
        if row_id is not None:
            model = self.get_one(row_id)
            if model is not None:
                model.is_active = not model.is_active
                if model.is_active:
                    # Reset last_book_date when re-enabling to allow re-booking
                    # of cancelled classes from the current/next occurrence
                    model.last_book_date = None
                db.session.commit()
                if model.is_active:
                    start_booking_loop(model)
                    flash("Reserva activada con éxito", "success")
                else:
                    stop_booking_loop(model, True)
                    flash("Reserva desactivada con éxito", "success")
        
        return redirect(url_for('booking.index_view'))

    @expose("/cancel-wodbuster-booking", methods=("POST",))
    def cancel_wodbuster_booking(self):
        """Endpoint to cancel a WodBuster booking."""
        if not login.current_user.is_authenticated:
            flash("Debes iniciar sesión para cancelar reservas", "error")
            return redirect(url_for('admin.login_view'))

        booking_id = request.form.get("booking_id")
        if not booking_id:
            flash("ID de reserva no proporcionado", "error")
            return redirect(url_for('booking.index_view'))

        wb_booking = db.session.query(WodBusterBooking).filter_by(id=booking_id, user_id=login.current_user.id).first()

        if not wb_booking:
            flash("Reserva no encontrada o no autorizada", "error")
            return redirect(url_for('booking.index_view'))

        try:
            scraper = get_scraper(login.current_user.email, login.current_user.cookie)
            
            # Combine date and time for the cancellation call
            class_datetime = datetime.combine(wb_booking.class_date, wb_booking.class_time)

            success = scraper.cancel_booking(
                box_url=wb_booking.box_url,
                class_id=wb_booking.class_id,
                class_datetime=class_datetime,
                athlete_id=login.current_user.athlete_id
            )

            if success:
                # Mark as cancelled and sync
                wb_booking.is_cancelled = True
                db.session.commit()
                flash("Reserva cancelada con éxito.", "success")
                # Trigger a sync to refresh the state from WodBuster
                sync_wodbuster_all(login.current_user)
            else:
                flash("Error al cancelar la reserva en WodBuster.", "error")

        except Exception as e:
            logging.exception("Error cancelling WodBuster booking")
            flash(f"Error al cancelar la reserva: {str(e)}", "error")

        return redirect(url_for('booking.index_view'))

    def get_list(self, *args, **kwargs):
        count, data = super().get_list(*args, **kwargs)
        for obj in data:
            obj.is_thread_active = is_booking_running(obj)
            obj.last_events = self._get_last_events(obj.events)
        # Sort by weekday (dow) first, then by reservation time
        data = sorted(data, key=lambda x: (x.dow, x.time))
        
        # Calculate statistics for each weekday
        weekday_stats = defaultdict(lambda: {'successful': 0, 'waiting': 0, 'errors': 0})
        for obj in data:
            dow = obj.dow
            if obj.last_events:
                # Check the latest event(s) to determine status
                # last_events is a list of Event objects, each with an 'event' string attribute
                for event in obj.last_events:
                    event_str = event.event if isinstance(event.event, str) else str(event.event)
                    # Check for successful booking
                    if EventMessage.BOOKING_COMPLETED.value.split('%')[0] in event_str:
                        weekday_stats[dow]['successful'] += 1
                        break
                    # Check for class full (waiting)
                    elif EventMessage.CLASS_FULL.value.split('%')[0] in event_str:
                        weekday_stats[dow]['waiting'] += 1
                        break
                    # Check for errors
                    elif (EventMessage.BOOKING_ERROR.value.split('%')[0] in event_str or
                          EventMessage.CREDENTIALS_EXPIRED.value in event_str or
                          EventMessage.LOGIN_FAILED.value in event_str or
                          EventMessage.INVALID_BOX_URL.value in event_str or
                          EventMessage.TOO_MANY_ERRORS.value in event_str):
                        weekday_stats[dow]['errors'] += 1
                        break
        
        # Store statistics in a way that can be accessed in template
        self._weekday_stats = dict(weekday_stats)
        return count, data
    
    def render(self, template, **kwargs):
        # Pass DAYS_OF_WEEK and weekday statistics to template context
        kwargs['DAYS_OF_WEEK'] = DAYS_OF_WEEK
        kwargs['weekday_stats'] = getattr(self, '_weekday_stats', {})
        
        wodbuster_bookings = []
        madrid_today = None
        if login.current_user.is_authenticated:
            madrid_today = datetime.now(_MADRID_TZ).date()
            bookings = db.session.query(WodBusterBooking).filter(
                WodBusterBooking.user_id == login.current_user.id,
                WodBusterBooking.is_cancelled == False,
                WodBusterBooking.class_date >= madrid_today,
            ).order_by(WodBusterBooking.class_date, WodBusterBooking.class_time).all()
            for booking in bookings:
                friendly_name = _wodbuster_booking_display_name(booking)
                booking.badge_name = friendly_name
                booking.badge_color = _wodbuster_booking_badge_color(booking, friendly_name)
            wodbuster_bookings = bookings
        
        kwargs['wodbuster_bookings'] = wodbuster_bookings
        
        kwargs['training_display_date'] = None
        kwargs['training_descriptions'] = []
        if login.current_user.is_authenticated:
            user = login.current_user
            today = madrid_today or datetime.now(_MADRID_TZ).date()
            tomorrow = today + timedelta(days=1)

            training_desc_logger.info(
                "Fetching training descriptions for user %s (ID: %d) for today (%s) and tomorrow (%s)",
                user.email, user.id, today, tomorrow,
            )

            descriptions = db.session.query(ClassTrainingDescription).filter(
                ClassTrainingDescription.user_id == user.id,
                ClassTrainingDescription.class_date.in_([today, tomorrow]),
            ).order_by(
                ClassTrainingDescription.class_date,
                ClassTrainingDescription.training_name,
            ).all()

            training_desc_logger.info(
                "Found %d training descriptions in DB for user %s (today and tomorrow)",
                len(descriptions), user.email,
            )

            db_descriptions_by_date = {}
            db_by_pizarra = {today: {}, tomorrow: {}}
            for desc in descriptions:
                db_descriptions_by_date.setdefault(desc.class_date, []).append(desc)
                if desc.class_date in db_by_pizarra:
                    db_by_pizarra[desc.class_date][desc.id_pizarra] = desc

            tomorrow_descriptions = _fetch_training_descriptions_for_date(
                user, tomorrow, db_by_pizarra[tomorrow], db_descriptions_by_date.get(tomorrow),
            )

            display_date = _pick_training_display_date(today, tomorrow, tomorrow_descriptions)
            if display_date == tomorrow:
                training_list = tomorrow_descriptions
            else:
                training_list = _fetch_training_descriptions_for_date(
                    user, today, db_by_pizarra[today], db_descriptions_by_date.get(today),
                )

            _apply_formatted_descriptions(training_list)

            if training_list:
                _attach_reservation_hints(
                    training_list, display_date, today, wodbuster_bookings,
                )
                kwargs['training_display_date'] = display_date
                kwargs['training_descriptions'] = training_list
            training_desc_logger.info(
                "Display date %s: %d descriptions (%s)",
                display_date, len(training_list), [d.training_name for d in training_list],
            )
        else:
            training_desc_logger.warning("User not authenticated, skipping training descriptions fetch")

        kwargs['attendance_dashboard'] = None
        if login.current_user.is_authenticated and login.current_user.athlete_id:
            try:
                kwargs['attendance_dashboard'] = get_attendance_dashboard(login.current_user)
            except Exception:
                logging.exception('Failed to load attendance dashboard')
                from .attendance_stats import _empty_dashboard
                kwargs['attendance_dashboard'] = _empty_dashboard(
                    'No se pudieron cargar las estadísticas. ¿Migración v1.13.0 aplicada?',
                )

        return super().render(template, **kwargs)

    @staticmethod
    def _get_last_events(events):
        if events:
            events_values = list(map(lambda x: x.event, events))
            if events_values[-1] == EventMessage.PAUSED:
                return [events[-1]]
            if EventMessage.PAUSED in events_values:
                last_paused_index = len(events_values) - 1 - events_values[::-1].index(EventMessage.PAUSED)
                events = events[last_paused_index + 1:]
            events_by_date = defaultdict(list)
            for event in events:
                events_by_date[event.date.strftime("%Y%m%d%H:%M")].append(event)
            max_date = max(events_by_date.keys())
            return events_by_date[max_date]
        return events

    def get_query(self):
        query = super().get_query()
        query = query.filter_by(user_id=login.current_user.id)
        return query

    def get_count_query(self):
        return super().get_count_query().filter_by(user_id=login.current_user.id)

    def get_one(self, id):
        result = super().get_one(id)
        if result.user_id != login.current_user.id:
            return None
        return result

    def is_accessible(self):
        return login.current_user.is_authenticated

    def update_model(self, form, model):
        if login.current_user.is_authenticated and model.user_id != login.current_user.id:
            flash("No estás autorizado a editar este elemento", "warning")
            return False

        stop_booking_loop(model)
        model.offset = form.offset  # Set calculated offset
        returned_value = super().update_model(form, model)
        if returned_value:
            model.book_despite_cancel_window = _book_despite_cancel_window_from_form(form)
            db.session.commit()
        if model.is_active:
            start_booking_loop(model)
        return returned_value

    def delete_model(self, model):
        if login.current_user.is_authenticated and model.user_id != login.current_user.id:
            flash("No estás autorizado a borrar este elemento", "warning")
            return False
        stop_booking_loop(model)
        return super().delete_model(model)

    def inaccessible_callback(self, name, **kwargs):
        # redirect to login page if user doesn't have access
        return redirect(url_for('admin.login_view', next=request.url))

    def create_model(self, form):
        user_bookings = self.session.query(Booking).filter(Booking.user == login.current_user).count()
        if user_bookings >= _MAX_BOOKINGS_BY_USER:
            flash(f'Cada usuario puede crear como máximo {_MAX_BOOKINGS_BY_USER} reservas', 'error')
            return False

        booking = super().create_model(form)
        booking.user = login.current_user
        booking.offset = form.offset  # Set calculated offset
        booking.book_despite_cancel_window = _book_despite_cancel_window_from_form(form)
        db.session.flush()
        db.session.commit()
        if booking.is_active:
            start_booking_loop(booking)
        return booking

    def create_form(self, obj=None):
        form = super().create_form(obj)
        _configure_book_despite_cancel_window_field(form, obj)
        if not form.url.data:
            last_booking = db.session.query(Booking).filter_by(user=login.current_user).order_by(Booking.id.desc()).first()
            if last_booking:
                form.url.data = form.url.data or last_booking.url
                form.offset = last_booking.offset # Use the calculated offset
                form.available_at.data = form.available_at.data or last_booking.available_at
                form.type_class.data = form.type_class.data or last_booking.type_class
            else:
                try:
                    scraper = get_scraper(login.current_user.email, login.current_user.cookie)
                    form.url.data = form.url.data or scraper.get_box_url()
                except (PasswordRequired, LoginError, InvalidWodBusterResponse, RequestException) as e:
                    logging.warning("Exception while loading BOX URL %s", e)
        
        # Set default offset based on selected day of week if not already set
        if form.dow.data is not None and form.offset is None: # Use form.offset
            form.offset = DEFAULT_OFFSETS_BY_DAY.get(form.dow.data, 0)

        return form

    def edit_form(self, obj=None):
        form = super().edit_form(obj)
        _configure_book_despite_cancel_window_field(form, obj)
        if obj:
            # Calculate and set the booking_open_day from the stored dow and offset
            booking_open_day = BookingForm.calculate_booking_open_day(obj.dow, obj.offset)
            # Set the data after form creation to avoid validation issues
            form.booking_open_day.data = booking_open_day
        return form


class EventView(sqla.ModelView):
    column_labels = dict(booking='Reserva', date='Fecha y Hora', event='Mensaje')
    can_create = False
    can_delete = False
    can_edit = False

    column_searchable_list = ('booking_id',)

    list_template = 'admin/event/list.html'

    column_formatters = dict(
        booking=lambda v, c, m, p: DAYS_OF_WEEK[m.booking.dow] + " " + m.booking.time.strftime('%H:%M:%S'),
        date=lambda v, c, m, p: m.date.strftime('%d/%m/%Y %H:%M'),
    )

    def is_visible(self):
        return False

    def get_query(self):
        query = super().get_query()
        query = query.join(Booking).filter(Booking.user_id==login.current_user.id)
        return query

    def get_count_query(self):
        return super().get_count_query().join(Booking).filter(Booking.user_id==login.current_user.id)

    def get_one(self, id):
        result = super().get_one(id)
        if result.booking.user_id != login.current_user.id:
            return None
        return result

    def is_accessible(self):
        return login.current_user.is_authenticated

    def inaccessible_callback(self, name, **kwargs):
        # redirect to login page if user doesn't have access
        return redirect(url_for('admin.login_view', next=request.url))


class UserForm(FlaskForm):

    mail_permission_success = fields.BooleanField('Recibir notificaciones de reservas exitosas')
    mail_permission_failure = fields.BooleanField('Recibir notificaciones de fallos en reservas')
    
    # Push notification settings for booking status
    push_permission_success = fields.BooleanField('Recibir notificaciones de reservas exitosas')
    push_permission_failure = fields.BooleanField('Recibir notificaciones de fallos en reservas')
    
    # Push notification settings
    push_notifications_enabled = fields.BooleanField('Habilitar notificaciones push')
    push_reminder_1h = fields.BooleanField('Recordatorio 1 hora antes')
    push_reminder_30m = fields.BooleanField('Recordatorio 30 minutos antes')
    push_reminder_15m = fields.BooleanField('Recordatorio 15 minutos antes')
    wodbuster_autosync_enabled = fields.BooleanField('Sincronización automática al cargar la página')
    auto_sync_training_descriptions = fields.BooleanField('Sincronización automática cuando no hay datos del entreno del día')
    cancel_window_hours = fields.IntegerField(
        'Ventana para cancelar autoreserva',
        default=3,
        validators=[validators.DataRequired(), validators.NumberRange(min=1, max=24)],
        description='Horas antes de la clase en las que cancelar puede conllevar penalización en el box.',
    )
    stop_autobook_in_cancel_window = fields.BooleanField(
        'Detener autoreserva para las clases que se encuentren dentro de la ventana',
    )


class UserView(sqla.ModelView):

    form = UserForm

    can_create = False
    can_delete = False
    can_edit = True

    edit_template = 'admin/user/edit.html'
    list_template = 'admin/user/list.html'

    column_formatters = dict(
        cookie=lambda v, c, m, p: _get_cookie_expiration_date(m.cookie),
    )

    def is_visible(self):
        return True

    def get_query(self):
        query = super().get_query().filter(User.id==login.current_user.id)
        return query

    def get_count_query(self):
        return super().get_count_query().filter(User.id==login.current_user.id)

    def get_one(self, id):
        result = super().get_one(id)
        if result and result.id != login.current_user.id:
            return None
        return result

    def update_model(self, form, model):
        if login.current_user.is_authenticated and model.id != login.current_user.id:
            flash("No estás autorizado a editar este elemento", "warning")
            return False

        return super().update_model(form, model)

    def is_accessible(self):
        return login.current_user.is_authenticated

    def inaccessible_callback(self, name, **kwargs):
        # redirect to login page if user doesn't have access
        return redirect(url_for('admin.login_view', next=request.url))

    def get_list(self, *args, **kwargs):
        count, data = super().get_list(*args, **kwargs)
        for obj in data:
            obj.cookie_expiration_date = _get_cookie_expiration_date(obj.cookie)
        return count, data


_MADRID_TZ = pytz.timezone('Europe/Madrid')

_WODBUSTER_CLASS_COLOR_BY_NAME = {
    'GAP': '#ec4899',
    'ENDURANCE': '#0ea5e9',
}

_WODBUSTER_CLASS_COLOR_BY_ID = {
    1: '#059669',
    2: '#000000',
    7: '#000000',
    9: '#2563eb',
    10: '#be185d',
    14: '#64748b',
    17: '#eab308',
    18: '#0ea5e9',
    19: '#ec4899',
}


def _wodbuster_booking_display_name(booking) -> str:
    if booking.class_name:
        return booking.class_name
    if booking.class_type:
        if booking.class_type == 'wod':
            return 'Wod'
        if booking.class_type == 'openbox':
            return 'Open Box'
        if booking.class_type.startswith('type_'):
            try:
                id_e = int(booking.class_type.split('_')[1])
            except (ValueError, IndexError):
                return booking.class_type
            type_names = {
                17: 'Minimal',
                14: 'Adapted Training',
                10: 'Teens',
                9: 'Gymnastics',
                18: 'Endurance',
                19: 'GAP',
            }
            return type_names.get(id_e, f'Type {id_e}')
        return booking.class_type
    return 'N/A'


def _wodbuster_booking_badge_color(booking, friendly_name: str) -> str:
    friendly_name_upper = friendly_name.upper()
    if friendly_name_upper in _WODBUSTER_CLASS_COLOR_BY_NAME:
        return _WODBUSTER_CLASS_COLOR_BY_NAME[friendly_name_upper]
    id_e = None
    if booking.class_type:
        if booking.class_type == 'wod':
            id_e = 1
        elif booking.class_type == 'openbox':
            id_e = 2
        elif booking.class_type.startswith('type_'):
            try:
                id_e = int(booking.class_type.split('_')[1])
            except (ValueError, IndexError):
                pass
    if id_e and id_e in _WODBUSTER_CLASS_COLOR_BY_ID:
        return _WODBUSTER_CLASS_COLOR_BY_ID[id_e]
    return '#64748b'


def _normalize_training_label(label: str) -> str:
    return (label or '').strip().casefold()


def _format_reservation_times(times) -> str:
    return ' y '.join(t.strftime('%H:%M') for t in sorted(times))


def _attach_reservation_hints(training_list, display_date, madrid_today, bookings):
    tomorrow = madrid_today + timedelta(days=1)
    if display_date == madrid_today:
        day_word = 'hoy'
    elif display_date == tomorrow:
        day_word = 'mañana'
    else:
        day_word = None

    day_bookings = [
        b for b in bookings
        if b.class_date == display_date and not b.is_cancelled
    ]

    for desc in training_list:
        desc.reservation_hint = None
        if not day_word:
            continue
        label = _normalize_training_label(desc.display_title or desc.training_name)
        if not label:
            continue
        matching_times = []
        for booking in day_bookings:
            booking_label = _normalize_training_label(
                booking.badge_name or _wodbuster_booking_display_name(booking),
            )
            if booking_label == label:
                matching_times.append(booking.class_time)
        if matching_times:
            times_str = _format_reservation_times(matching_times)
            desc.reservation_hint = f'Tienes reserva {day_word} a las {times_str}'


class _TempTrainingDesc:
    """Stand-in for ClassTrainingDescription when only API data exists."""

    def __init__(self, training_name, description, id_pizarra, class_date):
        self.training_name = training_name
        self.description = description
        self.id_pizarra = id_pizarra
        self.formatted_description = None
        self.display_title = None
        self.reservation_hint = None
        self.class_date = class_date
        self.id = -id_pizarra if id_pizarra else None


def _overlay_api_on_db_desc(db_desc, api_training):
    """Prefer live API HTML over stale plain-text rows still in the DB."""
    api_description = (api_training.get('description') or '').strip()
    db_text = (db_desc.description or '').strip()
    if description_is_html(api_description) and not description_is_html(db_text):
        db_desc.description = api_description
        api_name = api_training.get('training_name', '')
        if api_name:
            db_desc.training_name = api_name
        training_desc_logger.info(
            "Using API HTML for id_pizarra %s (replacing legacy plain-text cache)",
            db_desc.id_pizarra,
        )
    return db_desc


def _get_user_box_url(user):
    last_booking = (
        db.session.query(Booking)
        .filter_by(user_id=user.id)
        .order_by(Booking.id.desc())
        .first()
    )
    if last_booking and last_booking.url:
        return last_booking.url
    return get_scraper(user.email, user.cookie).get_box_url()


def _merge_descriptions_for_date(target_date, db_by_pizarra, api_training_types):
    merged = []
    api_by_pizarra = {}
    for api_training in api_training_types:
        id_pizarra = api_training.get('id_pizarra')
        if not id_pizarra:
            continue
        api_by_pizarra[id_pizarra] = api_training
        if id_pizarra in db_by_pizarra:
            merged.append(_overlay_api_on_db_desc(db_by_pizarra[id_pizarra], api_training))
        else:
            merged.append(_TempTrainingDesc(
                api_training.get('training_name', ''),
                api_training.get('description', ''),
                id_pizarra,
                target_date,
            ))
            training_desc_logger.info(
                "Added API-only training type for %s: %s (id_pizarra: %s)",
                target_date, api_training.get('training_name', ''), id_pizarra,
            )
    for id_pizarra, desc in db_by_pizarra.items():
        if id_pizarra not in api_by_pizarra:
            merged.append(desc)
            training_desc_logger.info(
                "Added DB-only training type for %s: %s (id_pizarra: %s)",
                target_date, desc.training_name, id_pizarra,
            )
    return merged


def _description_needs_sync(desc) -> bool:
    text = desc.description
    if not text or not str(text).strip():
        return True
    return not description_is_html(str(text))


def _fetch_training_descriptions_for_date(user, target_date, db_by_pizarra, db_list):
    if not user.athlete_id:
        return list(db_list or [])

    try:
        box_url = _get_user_box_url(user)
    except Exception as e:
        logging.error("Error resolving box URL for training descriptions: %s", e, exc_info=True)
        return list(db_list or [])

    if not box_url:
        return list(db_list or [])

    try:
        scraper = get_scraper(user.email, user.cookie)
        api_training_types = scraper.get_training_descriptions(
            box_url, user.athlete_id, target_date,
        )
        training_desc_logger.info(
            "Fetched %d training types from API for %s",
            len(api_training_types), target_date,
        )
        merged = _merge_descriptions_for_date(target_date, db_by_pizarra, api_training_types)

        needs_sync = any(_description_needs_sync(desc) for desc in merged)
        if user.auto_sync_training_descriptions and needs_sync:
            training_desc_logger.info(
                "Auto-syncing training descriptions for %s (missing or legacy plain-text)",
                target_date,
            )
            try:
                sync_result = sync_training_descriptions_for_date(user, target_date, box_url)
                if sync_result['success']:
                    refreshed = db.session.query(ClassTrainingDescription).filter(
                        ClassTrainingDescription.user_id == user.id,
                        ClassTrainingDescription.class_date == target_date,
                    ).all()
                    db_by_pizarra = {d.id_pizarra: d for d in refreshed}
                    merged = _merge_descriptions_for_date(
                        target_date, db_by_pizarra, api_training_types,
                    )
                else:
                    logging.warning(
                        "Auto-sync failed for %s: %s", target_date, sync_result.get('errors', []),
                    )
            except Exception as e:
                logging.error("Error during auto-sync for %s: %s", target_date, e, exc_info=True)

        return merged
    except Exception as e:
        logging.error(
            "Error fetching training types from API for %s: %s", target_date, e, exc_info=True,
        )
        return list(db_list or [])


def _pick_training_display_date(today, tomorrow, tomorrow_descriptions):
    if any(
        desc.description and visible_text_len(desc.description) > 0
        for desc in tomorrow_descriptions
    ):
        return tomorrow
    return today


def _apply_formatted_descriptions(descriptions):
    for desc in descriptions:
        desc.formatted_description = (
            prepare_training_description(desc.description)
            if desc.description
            else None
        )
        desc.display_title = (
            extract_board_title(desc.description or '', '')
            or desc.training_name
        )
        desc.reservation_hint = None


def _get_cookie_expiration_date(cookie):
    session = cloudscraper.create_scraper()
    session.cookies.update(pickle.loads(cookie))
    try:
        expiration_timestamp = next(x for x in session.cookies if x.name == '.WBAuth').expires
        return datetime.fromtimestamp(expiration_timestamp).strftime('%d/%m/%Y a las %H:%M')
    except (StopIteration, TypeError):
        return None

