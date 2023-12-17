import requests
import datetime
from bs4 import BeautifulSoup


class Scraper():
    session = None

    def __init__(self, url):
        self.url = url
        self.logged = False

    @staticmethod
    def lookup_header_value(text, header_name):
        index = text.index(header_name)
        return text[index + len(header_name) + 1:].split("|")[0]

    def login(self, username, password):
        self.session = requests.Session()
        self.headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36 Edg/118.0.2088.46"}
        box_name = self.url.split("/")[2].split(".")[0]
        login_url = f"https://wodbuster.com/account/login.aspx?cb={box_name}&ReturnUrl={self.url}%2fuser%2fdefault.aspx"
        request = self.session.get(login_url, headers=self.headers)

        soup = BeautifulSoup(request.content, 'lxml')
        viewstatec = soup.find(id='__VIEWSTATEC')['value']
        eventvalidation = soup.find(id='__EVENTVALIDATION')['value']
        csrftoken = soup.find(id='CSRFToken')['value']

        data = {'ctl00$ctl00$body$ctl00': 'ctl00%24ctl00%24body%24ctl00%7Cctl00%24ctl00%24body%24body%24CtlLogin%24CtlAceptar',
                'CSRFToken': csrftoken,
                '__EVENTTARGET': '',
                '__EVENTARGUMENT': '',
                '__VIEWSTATEC': viewstatec,
                '__VIEWSTATE': '',
                '__EVENTVALIDATION': eventvalidation,
                'ctl00$ctl00$body$body$CtlLogin$IoTri': '',
                'ctl00$ctl00$body$body$CtlLogin$IoTrg': '',
                'ctl00$ctl00$body$body$CtlLogin$IoTra': '',
                'ctl00$ctl00$body$body$CtlLogin$IoEmail': username,
                'ctl00$ctl00$body$body$CtlLogin$IoPassword': password,
                'ctl00$ctl00$body$body$CtlLogin$cIoUid': '',
                '__ASYNCPOST': 'true',
                'ctl00$ctl00$body$body$CtlLogin$CtlAceptar': 'Aceptar\n'}

        request = self.session.post(login_url, data=data, headers=self.headers)

        if request.status_code != 200:
            raise Exception('Something went wrong during login')

        viewstatec_confirm = self.lookup_header_value(request.text, '__VIEWSTATEC')
        eventvalidation_confirm = self.lookup_header_value(request.text, '__EVENTVALIDATION')

        data_confirm = {
            'ctl00$ctl00$body$ctl00': 'ctl00%24ctl00%24body%24ctl00%7Cctl00%24ctl00%24body%24body%24CtlConfiar%24CtlSeguro',
            'CSRFToken': csrftoken,
            '__EVENTTARGET': '',
            '__EVENTARGUMENT': '',
            '__VIEWSTATEC': viewstatec_confirm,
            '__VIEWSTATE': '',
            '__EVENTVALIDATION': eventvalidation_confirm,
            '__ASYNCPOST': 'true',
            'ctl00$ctl00$body$body$CtlConfiar$CtlSeguro': 'Recordar\n'}

        request_confirm = self.session.post(login_url, data=data_confirm, headers=self.headers)

        if request_confirm.status_code != 200:
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
        response = self.session.get(booking_url % t, headers=self.headers)
        classes = response.json()['Data']
        for _class in classes:
            if _class['Hora'] == hour:
                # Let's book it!
                _id = _class['Valores'][0]['Valor']['Id']
                response = self.session.get(
                    self.url + '/athlete/handlers/Calendario_Inscribir.ashx?id=%s&ticks=%s' % (_id, t), headers=self.headers)
                if response.status_code == 200:
                    return response.json()['Res']['EsCorrecto']

        return False
