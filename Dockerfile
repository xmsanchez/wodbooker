FROM python:3.11.9-alpine3.20

ENV TZ=Europe/Madrid

RUN apk add --no-cache git tzdata

RUN mkdir /app

COPY requirements.txt /app

WORKDIR /app

RUN python -m pip install --upgrade pip

RUN pip install -r requirements.txt

COPY . /app

EXPOSE 5000

CMD ["python", "app.py"]