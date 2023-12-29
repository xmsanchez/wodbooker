import datetime
import pickle
import logging
import requests
from bs4 import BeautifulSoup
from .exceptions import LoginError, InvalidWodBusterAPIResponse, NotLoggedUser, BookingNotAvailable

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
}


class Scraper():
    """
    WodBuster scraper
    """

    def __init__(self, url, user):
        self.url = url
        self.logged = False
        self._session = None
        self.user = user

    def __enter__(self):
        if self.user.cookie:
            logging.info("Attempt to use cookies for user %s", self.user.email)
            self._session = requests.Session()
            self._session.cookies.update(pickle.loads(self.user.cookie))

            try:
                self.get_classes(datetime.datetime.now())
                self.logged = True
                logging.info("Cookies Valid!")
            except InvalidWodBusterAPIResponse:
                logging.info("Cookies Invalid!")
                self._login(self.user.email, self.user.password)
        else:
            logging.info("Cookies not set for user %s. Logging in...", self.user.email)
            self._login(self.user.email, self.user.password)

    def __exit__(self, exc_type, exc_value, traceback):
        self.user.cookie = pickle.dumps(self._session.cookies)

    def _login(self, username, password):
        """
        Login the user into WodBuster
        """
        self._session = requests.Session()
        box_name = self.url.split("/")[2].split(".")[0]
        login_url = f"https://wodbuster.com/account/login.aspx?cb={box_name}&ReturnUrl={self.url}%2fuser%2fdefault.aspx"
        initial_request = self._session.get(login_url, headers=_HEADERS)

        soup = BeautifulSoup(initial_request.content, 'lxml')
        viewstatec = soup.find(id='__VIEWSTATEC')['value']
        eventvalidation = soup.find(id='__EVENTVALIDATION')['value']
        csrftoken = soup.find(id='CSRFToken')['value']

        data_login = {'ctl00$ctl00$body$ctl00': 'ctl00$ctl00$body$ctl00|ctl00$ctl00$body$body$CtlLogin$CtlAceptar',
                'ctl00$ctl00$body$body$CtlLogin$IoTri': '',
                'ctl00$ctl00$body$body$CtlLogin$IoTrg': '',
                'ctl00$ctl00$body$body$CtlLogin$IoTra': '',
                'ctl00$ctl00$body$body$CtlLogin$IoEmail': username,
                'ctl00$ctl00$body$body$CtlLogin$IoPassword': password,
                'ctl00$ctl00$body$body$CtlLogin$cIoUid': '',
                'ctl00$ctl00$body$body$CtlLogin$CtlAceptar': 'Aceptar\n'}

        login_request = self._login_request(login_url, viewstatec, eventvalidation, csrftoken, data_login)

        if login_request.status_code != 200:
            raise LoginError('Invalid response status from login request')

        if 'class="Warning"' in login_request.text:
            raise LoginError('Invalid credentials')

        viewstatec_confirm = self._lookup_header_value(login_request.text, '__VIEWSTATEC')
        eventvalidation_confirm = self._lookup_header_value(login_request.text, '__EVENTVALIDATION')

        data_confirm = {
            'ctl00$ctl00$body$ctl00': 'ctl00$ctl00$body$ctl00|ctl00$ctl00$body$body$CtlConfiar$CtlSeguro',
            'ctl00$ctl00$body$body$CtlConfiar$CtlSeguro': 'Recordar\n'
        }

        confirm_login_request = self._login_request(login_url, viewstatec_confirm,
                                                    eventvalidation_confirm, csrftoken,
                                                    data_confirm)

        if confirm_login_request.status_code != 200:
            raise LoginError('Invalid response status from login request')

        self.logged = True


    def _login_request(self, url, viewstatec, eventvalidation, csrftoken, extra_fields):
        data = {
            'CSRFToken': csrftoken,
            '__EVENTTARGET': '',
            '__EVENTARGUMENT': '',
            '__VIEWSTATEC': viewstatec,
            '__VIEWSTATE': '',
            '__EVENTVALIDATION': eventvalidation,
            '__ASYNCPOST': 'true',
        }

        data = {**data, **extra_fields}

        return self._session.post(url, data=data, headers=_HEADERS)

    @staticmethod
    def _lookup_header_value(text, header_name):
        index = text.index(header_name)
        return text[index + len(header_name) + 1:].split("|")[0]

    def book(self, date):
        """ Book a date, return True if the action was successful otherwise False """
        if not self.logged:
            raise NotLoggedUser('This action requires to be logged')

        classes, epoch = self.get_classes(date)
        hour = date.strftime('%H:%M:%S')

        if not classes['Data']:
            avaiable_at = None
            if "PrimeraHoraPublicacion" in classes:
                avaiable_at = datetime.datetime.strptime(classes["PrimeraHoraPublicacion"], '%m/%d/%Y %H:%M:%S')
            raise BookingNotAvailable('No classes available', avaiable_at)

        for _class in classes['Data']:
            if _class['Hora'] == hour:
                _id = _class['Valores'][0]['Valor']['Id']
                book_result = self._book_request(f'{self.url}/athlete/handlers/Calendario_Inscribir.ashx?id={_id}&ticks={epoch}')
                return book_result['Res']['EsCorrecto']

        return False

    def get_classes(self, date):
        """ Get the classes for a given epoch """
        epoch = int(date.timestamp())
        return self._book_request(f'{self.url}/athlete/handlers/LoadClass.ashx?ticks={epoch}'), epoch

    def _book_request(self, url):
        try:
            request = self._session.get(url, headers=_HEADERS)

            if request.status_code != 200:
                raise InvalidWodBusterAPIResponse('Invalid response status from WodBuster')
 
            return request.json()
        except requests.exceptions.JSONDecodeError as e:
            raise InvalidWodBusterAPIResponse('WodBuster returned a non JSON response') from e
        except requests.exceptions.RequestException as e:
            raise InvalidWodBusterAPIResponse('WodBuster returned a non expected response') from e
