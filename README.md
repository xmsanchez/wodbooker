# WodBooker - The WodBuster AutoBooker

This Flask application allows WodBuster users to create booking requests which will be performed on their behalf as soon as possible.

Users has to authenticate with the app using their WodBuster credentials. The app will check this credentials with WodBuster and will allow users to sign in only when the credentials are valid.

Once logged in, users are allowed to create booking requests. To do so, they are required to introduce:

* Day of week: The day of the week the booking is intended to.
* Hour: The hour of the day the booking is intented to.
* WodBuster box URL: Every box has a different WodBuster URL. Users are required to introduce the one specific to their box. This is required for users with access to multiple boxes.
* Days in advance: The number of days in advance with which the class can be booked. This field is now optional and has smart defaults based on the day of the week.
* Booking opening hour: The time of the day when the first booking attempt should be executed. 

## Days in Advance (Offset) Feature

The "Days in advance" field has been enhanced with smart defaults:

- **Saturday**: 0 days (same day booking)
- **Friday**: 1 day in advance
- **Thursday**: 2 days in advance
- **Wednesday**: 3 days in advance
- **Tuesday**: 4 days in advance
- **Monday**: 5 days in advance
- **Sunday**: 6 days in advance

The offset field is now optional and will automatically suggest the appropriate value based on the selected day of the week. Users can still customize this value if needed.

Once the request is created, a thread will take care of it by:

1. Waiting for the booking is available taking into account "Days in advance" and "Booking opening hour" parameters
2. Attempting the booking when the booking is supposed to be ready
3. If the day has not been loaded, the thread will connect to the SSE (Server Side Events) server to listen until the classes have been loaded.
4. If the day has been loaded but booking is not available yet, the thread will wait unitl the class is available. 
5. Once the booking has been performed, the thread will execute the whole process again for the same day of the next week.

In order to avoid CloudFare restrictions it's highly recommended to create an entry in your `/etc/hosts` file pointing `wodbuster.com` to the final WodBuster server.

## Push Notifications

WodBooker supports browser push notifications to remind users before their booked classes. Users can enable push notifications and choose to receive reminders at 1 hour, 30 minutes, and/or 15 minutes before a class.

### Features

- **Browser Push Notifications**: Uses the Web Push API to send notifications directly to users' browsers
- **Configurable Reminders**: Users can enable reminders at 1 hour, 30 minutes, and 15 minutes before classes
- **Automatic Sync**: Optional automatic synchronization of WodBuster bookings when the page loads
- **User Preferences**: All notification settings are configurable in the "Preferencias" menu

### Setup

#### 1. Generate VAPID Keys

VAPID (Voluntary Application Server Identification) keys are required for push notifications. Generate them using the provided script:

```bash
python generate_vapid_keys.py your-email@example.com
```

This will output three environment variables that you need to set:
- `VAPID_PUBLIC_KEY`: The public key (safe to share)
- `VAPID_PRIVATE_KEY`: The private key (keep secret!)
- `VAPID_CLAIM_EMAIL`: Contact email for the service (format: `mailto:your-email@example.com`)

**Note**: The email in `VAPID_CLAIM_EMAIL` is not used to send emails. It's just a contact identifier required by the VAPID protocol to identify who controls the push notification service.

#### 2. Set Environment Variables

Add the VAPID keys to your environment variables. If using Docker Compose, add them to your `.env` file:

```bash
VAPID_PUBLIC_KEY=your-generated-public-key
VAPID_PRIVATE_KEY=your-generated-private-key
VAPID_CLAIM_EMAIL=mailto:your-email@example.com
```

#### 3. Restart the Application

After setting the environment variables, restart your application:

```bash
docker compose restart wodbooker
```

### User Experience

Users can enable push notifications by:

1. Going to **Preferencias** in the header menu
2. Scrolling to the **Notificaciones Push** section
3. Clicking **"Activar Notificaciones Push"**
4. Granting permission when the browser prompts
5. Selecting which reminder times they want (1 hour, 30 minutes, 15 minutes)

The push notifications will automatically be sent based on the user's preferences and their confirmed, non-cancelled bookings.

### Technical Details

- **Service Worker**: A service worker (`/static/sw.js`) handles push events in the browser
- **Background Scheduler**: A background thread checks for upcoming classes every minute and sends notifications accordingly
- **Browser Support**: Works in all modern browsers that support the Web Push API (Chrome, Firefox, Edge, Safari, Brave, etc.)
- **HTTPS Required**: Push notifications require HTTPS (or localhost for development)

### Troubleshooting

If push notifications aren't working:

1. **Check VAPID Keys**: Ensure `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY` are set correctly
2. **Check Browser Console**: Open the browser console (F12) to see any errors
3. **Check Server Logs**: Look for errors in the application logs
4. **Verify HTTPS**: Push notifications require HTTPS (except on localhost)
5. **Check Browser Permissions**: Ensure the user has granted notification permissions

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

1. **Set up environment variables**: Create a `.env` file with the required variables (see [Push Notifications Setup](#push-notifications) for VAPID keys):

```bash
EMAIL_USER=your-email@example.com
EMAIL_PASSWORD=your-email-password
EMAIL_SENDER=your-email@example.com
EMAIL_HOST=smtp.example.com
VAPID_PUBLIC_KEY=your-vapid-public-key
VAPID_PRIVATE_KEY=your-vapid-private-key
VAPID_CLAIM_EMAIL=mailto:your-email@example.com
```

2. **Run with Docker Compose**: The easiest way to run the project is using docker compose:

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
docker run --rm -p 5100:5000 --network=net \
    -e EMAIL_PASSWORD=${EMAIL_PASSWORD} \
    -e EMAIL_USER=${EMAIL_USER} \
    -e EMAIL_SENDER=${EMAIL_SENDER} \
    -e VAPID_PUBLIC_KEY=${VAPID_PUBLIC_KEY} \
    -e VAPID_PRIVATE_KEY=${VAPID_PRIVATE_KEY} \
    -e VAPID_CLAIM_EMAIL=${VAPID_CLAIM_EMAIL} \
    -v $(pwd):/app --name wodbooker wodbooker
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
