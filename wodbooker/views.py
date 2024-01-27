import logging
from datetime import timedelta
from flask import redirect, url_for, request, flash
from markupsafe import Markup
from wtforms import form, fields, validators
from flask_admin.form.fields import TimeField
import flask_login as login
from flask_admin import AdminIndexView, helpers, expose
from flask_admin.contrib import sqla
from flask_admin.form import SecureForm
from flask_admin.model.template import TemplateLinkRowAction
from requests.exceptions import RequestException
from sqlalchemy import and_
from flask_wtf import FlaskForm
from flask_wtf import Recaptcha
from flask_wtf.recaptcha import RecaptchaField
from .models import User, db, Booking
from .booker import start_booking_loop, stop_booking_loop
from .scraper import refresh_scraper, get_scraper
from .exceptions import LoginError, InvalidWodBusterResponse

_DAYS_OF_WEEK = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
_NO_EVENTS = "Aún no hay eventos registrados para esta reserva. Los eventos aparecerán aquí " + \
        "cuando la reserva esté activa según vayan ocurriendo."


class LoginForm(FlaskForm):
    email = fields.StringField(validators=[validators.DataRequired()])
    password = fields.PasswordField(validators=[validators.DataRequired()])
    recaptcha = RecaptchaField(validators=[Recaptcha("Verifica que no eres un robot")])

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
            login.login_user(user, remember=True, duration=timedelta(days=15))

        if login.current_user.is_authenticated:
            return redirect(url_for('booking.index_view'))
        self._template_args['form'] = form
        return super(MyAdminIndexView, self).index()

    @expose('/logout/')
    def logout_view(self):
        login.logout_user()
        return redirect(url_for('.index'))


class BookingForm(form.Form):

    dow = fields.SelectField('Día de la semana', choices=[(0, 'Lunes'), (1, 'Martes'), (
        2, 'Miércoles'), (3, 'Jueves'), (4, 'Viernes'), (5, 'Sábado'), (6, 'Domingo')])

    time = TimeField('Hora')
    url = fields.StringField('URL de WodBuster (ej: https://YOUR_BOX.wodbuster.com)')
    offset = fields.IntegerField('Días de antelación para reservar')
    available_at = TimeField('Hora de apertura de reservas')

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
        dow=lambda v, c, m, p: _DAYS_OF_WEEK[m.dow],
        time=lambda v, c, m, p: m.time.strftime('%H:%M'),
        available_at=lambda v, c, m, p: m.available_at.strftime('%H:%M'),
        last_book_date=lambda v, c, m, p: m.last_book_date.strftime('%d/%m/%Y') if m.last_book_date else "",
        offset=lambda v, c, m, p: _DAYS_OF_WEEK[m.dow - m.offset],
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
                    stop_booking_loop(model)
                    flash("Reserva desactivada con éxito", "success")
        
        return redirect(url_for('booking.index_view'))

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
        booking = super().create_model(form)
        booking.user = login.current_user
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
                form.offset.data = form.offset.data or last_booking.offset
                form.available_at.data = form.available_at.data or last_booking.available_at
            else:
                try:
                    scraper = get_scraper(login.current_user.email, login.current_user.cookie)
                    form.url.data = form.url.data or scraper.get_box_url()
                except (LoginError, InvalidWodBusterResponse, RequestException) as e:
                    logging.warning("Exception while loading BOX URL %s", e)

        return form


class EventView(sqla.ModelView):
    column_labels = dict(booking='Reserva', date='Fecha y Hora', event='Mensaje')
    can_create = False
    can_delete = False
    can_edit = False

    column_searchable_list = ('booking_id',)

    list_template = 'admin/event/list.html'

    column_formatters = dict(
        booking=lambda v, c, m, p: _DAYS_OF_WEEK[m.booking.dow] + " " + m.booking.time.strftime('%H:%M'),
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
