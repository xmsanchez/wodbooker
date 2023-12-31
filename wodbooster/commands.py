import datetime
import json
from collections import defaultdict
import click
from flask.cli import with_appcontext
from sqlalchemy import or_, and_
from iterators import TimeoutIterator

from .models import Booking, db, User
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
                    time = datetime.time(booking.time.hour, booking.time.minute, 0)
                    booking_time = datetime.datetime.combine(day, time)
                    booking_date_str = booking_time.strftime('%d/%m/%Y %H:%M')
                    try:
                        result = scraper.book(booking_time)
                        if result:
                            booking.booked_at = today
                            print(f'Booking for user {user.email} at {booking_date_str} completed successfully')
                    except NotLoggedUser:
                        print(f'Impossible to book classes for {user.email}. User is not logged')
                    except InvalidWodBusterAPIResponse:
                        print(f'Impossible to book classes for {user.email} for {booking_date_str}. Invalid response from WodBuster')
        except LoginError:
            print(f'Impossible to book classes for {user.email}. Login failed')
        
    db.session.commit()


@click.command()
@click.argument('date')
@click.argument('url')
@with_appcontext
def subscribe_to_events(date, url='https://contact.wodbuster.com'):
    user = db.session.query(User).first()
    scraper = Scraper(url, user)
    with scraper:
        while True:
            parsed_date = datetime.datetime.strptime(date, '%d/%m/%Y').date()
            client = scraper.get_subscription(parsed_date)

            for event in TimeoutIterator(client.events(), timeout=60, sentinel=None):

                if not event:
                    print("No event received after 60 seconds. Reseting connection")
                    client.close()
                    break

                # data = json.loads(event.data[:-1])
                event_time = datetime.datetime.now()
                print(f"{event_time.strftime('%d/%m/%Y %H:%M:%S')} - {event.data}")


