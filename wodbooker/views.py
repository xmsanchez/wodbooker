import logging
from datetime import datetime
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
from .models import User, db, Booking
from .booker import start_booking_loop, stop_booking_loop, is_booking_running
from .scraper import refresh_scraper, get_scraper
from .exceptions import LoginError, InvalidWodBusterResponse, PasswordRequired
from .constants import EventMessage, DAYS_OF_WEEK, DEFAULT_OFFSETS_BY_DAY

_MAX_BOOKINGS_BY_USER = 10


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
            return existing_user
        else:
            user = User()
            user.email = self.email.data
            user.cookie = self._scraper.get_cookies()
            db.session.add(user)
            db.session.commit()
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
        2, 'Miércoles'), (3, 'Jueves'), (4, 'Viernes'), (5, 'Sábado'), (6, 'Domingo')])
    time = TimeField('Hora a reservar', validators=[validators.DataRequired()])
    url = fields.StringField('URL de WodBuster (ej: https://YOUR_BOX.wodbuster.com)', validators=[validators.DataRequired()])
    booking_open_day = fields.SelectField(
        'Día de apertura de reservas',
        choices=[(0, 'Lunes'), (1, 'Martes'), (2, 'Miércoles'), (3, 'Jueves'), (4, 'Viernes'), (5, 'Sábado'), (6, 'Domingo')],
        default=5
    )
    available_at = TimeField('Hora de apertura de reservas', validators=[validators.DataRequired()])
    type_class = fields.SelectField('Tipo de clase a reservar (wod, openbox)', choices=[(0, 'wod'), (1, 'openbox')], 
                                    validators=[validators.DataRequired()], 
                                    description="Algunos días puede haber simultáneamente wod y openbox. Selecciona aquí el tipo de clase que deseas reservar.")

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
                db.session.commit()
                if model.is_active:
                    start_booking_loop(model)
                    flash("Reserva activada con éxito", "success")
                else:
                    stop_booking_loop(model, True)
                    flash("Reserva desactivada con éxito", "success")
        
        return redirect(url_for('booking.index_view'))

    def get_list(self, *args, **kwargs):
        count, data = super().get_list(*args, **kwargs)
        for obj in data:
            obj.is_thread_active = is_booking_running(obj)
            obj.last_events = self._get_last_events(obj.events)
        return count, data

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
        db.session.flush()
        db.session.commit()
        if booking.is_active:
            start_booking_loop(booking)
        return booking

    def create_form(self, obj=None):
        form = super().create_form(obj)
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


class UserForm(form.Form):

    mail_permission_success = fields.BooleanField('Recibir notificaciones de reservas exitosas')
    mail_permission_failure = fields.BooleanField('Recibir notificaciones de fallos en reservas')


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
        return False

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


def _get_cookie_expiration_date(cookie):
    session = cloudscraper.create_scraper()
    session.cookies.update(pickle.loads(cookie))
    try:
        expiration_timestamp = next(x for x in session.cookies if x.name == '.WBAuth').expires
        return datetime.fromtimestamp(expiration_timestamp).strftime('%d/%m/%Y a las %H:%M')
    except (StopIteration, TypeError):
        return None
