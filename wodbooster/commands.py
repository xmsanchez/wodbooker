import click
from flask.cli import with_appcontext
import datetime
from sqlalchemy import or_, and_

from .models import Booking, db
from .scraper import Scraper


@click.command()
@click.argument('offset')
@click.argument('url')
@with_appcontext
def book(offset, url='https://contact.wodbuster.com'):
    today = datetime.date.today()
    dow = today.weekday()
    dows = [(dow + i) % 7 for i in list(range(int(offset) + 1))]
    dow_map = dict(zip(dows, list(range(int(offset) + 1))))
    bookings = list(db.session.query(Booking).filter(and_(or_(Booking.booked_at < (
        today - datetime.timedelta(days=int(offset))), Booking.booked_at == None), Booking.dow.in_(dows))).all())
    scrappers = {}

    for booking in bookings:
        if booking.user.email not in scrappers:
            scraper = Scraper(url)
            scraper.login(booking.user.email, booking.user.password)
            scrappers[booking.user.email] = scraper
        else:
            scraper = scrappers[booking.user.email]

        day = today + datetime.timedelta(days=dow_map[booking.dow])
        result = scraper.book(datetime.datetime(
            day.year, day.month, day.day, booking.time.hour, booking.time.minute, 0))
        if result:
            booking.booked_at = today
            db.session.commit()
            print('Item %s Booked successfully for %s' %
                  (booking.id, booking.user.email))

    if len(bookings) == 0:
        print('All set')
