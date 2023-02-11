import requests
import datetime
from bs4 import BeautifulSoup
import http.client
import re


class Scraper():
    session = None

    def __init__(self, url):
        self.url = url
        self.logged = False

    def login(self, username, password):
        self.session = requests.Session()
        login_path = '/account/login.aspx?ReturnUrl=%2Fuser%2F'
        login_url = self.url + login_path
        request = self.session.get(login_url)

        soup = BeautifulSoup(request.content, 'lxml')
        viewstatec = soup.find(id='__VIEWSTATEC')['value']

        data = {'__VIEWSTATEC': viewstatec,
                '__EVENTTARGET': '',
                '__EVENTARGUMENT': '',
                '__VIEWSTATE': '',
                'ctl00$ctl00$ctl00$ctl00$ctl00$body$body$body$body$body$IoMobileAppId': '',
                'ctl00$ctl00$ctl00$ctl00$ctl00$body$body$body$body$body$IoMobileIdFireBase': '',
                'ctl00$ctl00$ctl00$ctl00$body$body$body$body$IoEmail': username,
                'ctl00$ctl00$ctl00$ctl00$body$body$body$body$IoPassword': password,
                'ctl00$ctl00$ctl00$ctl00$body$body$body$body$CtlEntrar': 'Entrar'}

        request = self.session.post(login_url, data=data)

        if request.status_code != 200:
            raise Exception('Something went wrong during login')
        self.logged = True

    def book(self, date):
        """ Book a date, return True if the action was successful otherwise False """
        if not self.logged:
            raise Exception('This action requires to be logged')

        booking_url = self.url + '/athlete/handlers/LoadClass.ashx?ticks=%s'
        t_base = 63741340800
        first_day = datetime.datetime(2020, 11, 19)
        right_pad = '0000000'
        day = datetime.datetime(date.year, date.month, date.day)
        hour = date.strftime('%H:%M:%S')
        t = (str(t_base + int((day - first_day).total_seconds())) + right_pad)
        response = self.session.get(booking_url % t)
        classes = response.json()['Data']
        for _class in classes:
            if _class['Hora'] == hour:
                # Let's book it!
                _id = _class['Valores'][0]['Valor']['Id']
                response = self.session.get(
                    self.url + '/athlete/handlers/Calendario_Inscribir.ashx?id=%s&ticks=%s' % (_id, t))
                if response.status_code == 200:
                    return response.json()['Res']['EsCorrecto']

        return False
