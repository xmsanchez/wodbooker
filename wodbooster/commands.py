import datetime
from collections import defaultdict
import click
from flask.cli import with_appcontext
from sqlalchemy import or_, and_

from .models import Booking, db
from .scraper import Scraper
from .exceptions import NotLoggedUser, InvalidWodBusterAPIResponse, LoginError


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
    
    bookings_by_user = defaultdict(list)
    for booking in bookings:
        bookings_by_user[booking.user].append(booking)

    for user in bookings_by_user:
        scraper = Scraper(url, user)

        try:
            with scraper:
                for booking in bookings_by_user[user]:
                    day = today + datetime.timedelta(days=dow_map[booking.dow])
                    book_day_str = day.strftime('%d/%m/%Y %H:%M')
                    try:
                        result = scraper.book(datetime.datetime(
                            day.year, day.month, day.day, booking.time.hour, booking.time.minute, 0))
                        if result:
                            booking.booked_at = today
                            print(f'Booking for user {user.email} at {book_day_str} completed successfully')
                    except NotLoggedUser:
                        print(f'Impossible to book classes for {user.email}. User is not logged')
                    except InvalidWodBusterAPIResponse:
                        print(f'Impossible to book classes for {user.email} for {book_day_str}. Invalid response from WodBuster')
        except LoginError:
            print(f'Impossible to book classes for {user.email}. Login failed')
        
    db.session.commit()
    
    if len(bookings) == 0:
        print('All set')
