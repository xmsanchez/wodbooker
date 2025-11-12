import logging
import json
from flask import current_app
from pywebpush import webpush, WebPushException
from .models import db, PushSubscription, User, WodBusterBooking
import pytz

_MADRID_TZ = pytz.timezone('Europe/Madrid')


def send_push_notification(subscription, title, body, data=None):
    """
    Send a push notification to a subscription
    :param subscription: PushSubscription object
    :param title: Notification title
    :param body: Notification body
    :param data: Optional data payload (dict)
    :return: True if successful, False otherwise
    """
    try:
        vapid_private_key = current_app.config.get('VAPID_PRIVATE_KEY')
        vapid_claim_email = current_app.config.get('VAPID_CLAIM_EMAIL', 'mailto:admin@example.com')
        
        if not vapid_private_key:
            logging.error("VAPID private key not configured")
            return False
        
        subscription_info = {
            "endpoint": subscription.endpoint,
            "keys": {
                "p256dh": subscription.p256dh,
                "auth": subscription.auth
            }
        }
        
        payload = {
            "title": title,
            "body": body
        }
        
        if data:
            payload.update(data)
        
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=vapid_private_key,
            vapid_claim_email=vapid_claim_email
        )
        
        logging.info("Push notification sent successfully to subscription %s", subscription.id)
        return True
        
    except WebPushException as e:
        logging.warning("WebPushException sending notification: %s", str(e))
        # If subscription is invalid, remove it
        if e.response and e.response.status_code in [410, 404]:
            logging.info("Removing invalid subscription %s", subscription.id)
            db.session.delete(subscription)
            db.session.commit()
        return False
    except Exception as e:
        logging.exception("Error sending push notification: %s", str(e))
        return False


def send_class_reminder(user, booking, reminder_minutes):
    """
    Send a class reminder notification to all user's subscriptions
    :param user: User object
    :param booking: WodBusterBooking object
    :param reminder_minutes: Reminder time in minutes (60, 30, or 15)
    :return: Number of successful notifications sent
    """
    if not user.push_notifications_enabled:
        return 0
    
    # Check if user has this reminder enabled
    if reminder_minutes == 60 and not user.push_reminder_1h:
        return 0
    if reminder_minutes == 30 and not user.push_reminder_30m:
        return 0
    if reminder_minutes == 15 and not user.push_reminder_15m:
        return 0
    
    # Get all active subscriptions for the user
    subscriptions = db.session.query(PushSubscription).filter_by(user_id=user.id).all()
    
    if not subscriptions:
        logging.debug("No push subscriptions found for user %s", user.email)
        return 0
    
    # Format class time (not used in this function but kept for consistency)
    from datetime import datetime as dt
    class_datetime = _MADRID_TZ.localize(
        dt.combine(
            booking.class_date,
            booking.class_time
        )
    )
    
    # Create notification message
    reminder_text = ""
    if reminder_minutes == 60:
        reminder_text = "1 hora"
    elif reminder_minutes == 30:
        reminder_text = "30 minutos"
    elif reminder_minutes == 15:
        reminder_text = "15 minutos"
    
    title = f"Recordatorio de clase - {reminder_text}"
    body = f"{booking.class_name or 'Clase'} a las {booking.class_time.strftime('%H:%M')}"
    
    data = {
        "booking_id": booking.id,
        "class_date": booking.class_date.isoformat(),
        "class_time": booking.class_time.isoformat(),
        "reminder_minutes": reminder_minutes
    }
    
    success_count = 0
    for subscription in subscriptions:
        if send_push_notification(subscription, title, body, data):
            success_count += 1
    
    return success_count

