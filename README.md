# WodBooker - The WodBuster AutoBooker

This Flask application allows WodBuster users to create booking requests which will be performed on their behalf as soon as possible.

Users has to authenticate with the app using their WodBuster credentials. The app will check this credentials with WodBuster and will allow users to sign in only when the credentials are valid.

Once logged in, users are allowed to create booking requests. To do so, they are required to introduce:

* Day of week: The day of the week the booking is intended to.
* Hour: The hour of the day the booking is intented to.
* WodBuster box URL: Every box has a different WodBuster URL. Users are required to introduce the one specific to their box. This is required for users with access to multiple boxes.
* Days in advance: The number of days in advance with which the class can be booked.
* Booking opening hour: The time of the day when the first booking attempt should be executed. 

Once the request is created, a thread will take care of it by:

1. Waiting for the booking is available taking into account "Days in advance" and "Booking opening hour" parameters
2. Attempting the booking when the booking is supposed to be ready
3. If the day has not been loaded, the thread will connect to the SSE (Server Side Events) server to listen until the classes have been loaded.
4. If the day has been loaded but booking is not available yet, the thread will wait unitl the class is available. 
5. Once the booking has been performed, the thread will execute the whole process again for the same day of the next week.

In order to avoid CloudFare restrictions it's highly recommended to create an entry in your `/etc/hosts` file pointing `wodbuster.com` to the final WodBuster server.

## Webapp
Run the app by just running:

```
python3 app.py
```

## GCP Cloud Run

```bash
gcloud builds submit --tag gcr.io/$GCLOUD_PROJECT/wodbooker . && \
gcloud run deploy wodbooker \
    --image gcr.io/$GCLOUD_PROJECT/wodbooker \
    --port 5000 \
    --region europe-west1 \
    --platform managed \
    --allow-unauthenticated \
    --quiet
```

## Running locally (Linux)

### Requirements

Install docker

### Setup

The easiest way to run the project is using docker compose. So go ahead:

```bash
docker compose up -d --build
```

Once the build finishes the containers should be up & running.

The application will be accessible at:

- 127.0.0.1:5100
- 127.0.0.1:80
- 127.0.0.1:443

To access from outside your local network you'll need to open two ports:

- Open the 443 port in your router and forward it to `127.0.0.1:443`.
- Open the 80 port in your router and forward it to `127.0.0.1:80`. This will be used for letsencrypt to renew the certificate.

For the letsencrypt configuration see the last section of this README.

### Setup (legacy)

Build images, create docker network

```bash
docker network create net
docker build -t nginx-wodbooker ./nginx/Dockerfile
docker build -t wodbooker .
```

Run containers:

```bash
docker run --rm -p 5001:5001 --network=net -e EMAIL_PASSWORD=${EMAIL_PASSWORD} -v $(pwd):/app --name wodbooker wodbooker
docker run --rm --name nginx-wodbooker  --network=net -p 80:80 nginx-wodbooker
```

## SSL certificate for nginx

The first time we run wodbooker nginx container we need to run this:

```bash
/usr/bin/docker exec -ti nginx-wodbooker certbot --nginx -d wodbooker.yourdomain.com
```

The above command will deploy the letsencrypt certificate for the first time.

From then on, the certificate only needs to be renewed every three months. This can be automated in crontab:

```bash
crontab -e
```

Add this line:

```bash
0 0 * * * /usr/bin/docker exec -ti nginx-wodbooker certbot renew --quiet
```
