# # Use the official Python image as the base image
# FROM python:3.12.8-bullseye

# # Install necessary packages for Selenium and Chrome
# RUN apt-get install -y wget git gnupg

# # Set the Chrome repo.
# RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
#     && echo "deb http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list

# # Install Chrome.
# RUN apt-get update && apt-get -y install google-chrome-stable

# # Create app directory
# RUN mkdir /app

# # Set the working directory
# WORKDIR /app

# # Copy requirements file
# COPY requirements.txt /app

# # Upgrade pip
# RUN python -m pip install --upgrade pip

# # Install the required Python packages
# RUN pip install -r requirements.txt

# # Copy the rest of the application code
# COPY . /app

# # Expose the application port
# EXPOSE 5000

# # Set the command to run your application
# CMD ["python", "app.py"]

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