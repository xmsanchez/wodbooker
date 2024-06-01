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
