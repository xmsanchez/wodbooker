import logging
import json
import base64
from flask import current_app
from pywebpush import webpush, WebPushException
from .models import db, PushSubscription, User, WodBusterBooking
import pytz

_MADRID_TZ = pytz.timezone('Europe/Madrid')

# Try to import py_vapid for proper key handling
try:
    from py_vapid import Vapid01
    HAS_PY_VAPID = True
except ImportError:
    HAS_PY_VAPID = False


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
        vapid_private_key_str = current_app.config.get('VAPID_PRIVATE_KEY')
        vapid_claim_email = current_app.config.get('VAPID_CLAIM_EMAIL', 'mailto:admin@example.com')
        
        if not vapid_private_key_str:
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
        
        # Build vapid_claims dictionary (pywebpush expects this format)
        vapid_claims = {
            "sub": vapid_claim_email
        }
        
        # Try to convert to PEM format
        try:
            # Decode base64url to raw bytes
            private_key_b64 = vapid_private_key_str
            # Fix padding:
            private_key_b64 += '=' * (-len(private_key_b64) % 4)
            private_key_bytes = base64.urlsafe_b64decode(private_key_b64)
            
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.backends import default_backend
            
            private_value = int.from_bytes(private_key_bytes, 'big')
            curve = ec.SECP256R1()
            
            # Use cryptography library to create the key from private value.
            # ec.derive_private_key automatically computes the public key.
            private_key = ec.derive_private_key(private_value, curve, default_backend())
            
            # The py-vapid library expects a base64url-encoded DER key.
            # We serialize our key object to DER format...
            der_key = private_key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )

            # ...and then base64url encode it.
            vapid_private_key = base64.urlsafe_b64encode(der_key).decode('utf-8').rstrip("=")
            
            logging.info("Successfully converted VAPID key to base64url(DER) format.")

        except Exception as e:
            logging.warning("Could not convert VAPID key. Using raw string. Error: %s", e)
            vapid_private_key = vapid_private_key_str
        
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=vapid_private_key,
            vapid_claims=vapid_claims
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
    
    title = f"Wodbooker - Recordatorio de clase - {reminder_text}"
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


def send_booking_status_notification(user, booking, is_success, message):
    """
    Send a push notification for booking success or failure
    :param user: User object
    :param booking: Booking object
    :param is_success: True for success, False for failure
    :param message: The message to send in the notification
    :return: Number of successful notifications sent
    """
    # Check if user has push notifications enabled and the appropriate permission
    if not user.push_notifications_enabled:
        return 0
    
    if is_success and not user.push_permission_success:
        return 0
    
    if not is_success and not user.push_permission_failure:
        return 0
    
    # Get all active subscriptions for the user
    subscriptions = db.session.query(PushSubscription).filter_by(user_id=user.id).all()
    
    if not subscriptions:
        logging.debug("No push subscriptions found for user %s", user.email)
        return 0
    
    # Create notification message
    if is_success:
        title = "Wodbooker - Reserva exitosa"
    else:
        title = "Wodbooker - Error en la reserva"
    
    body = message
    
    # Format booking info for the notification
    from .constants import DAYS_OF_WEEK
    booking_day = DAYS_OF_WEEK[booking.dow] if booking.dow < len(DAYS_OF_WEEK) else f"Día {booking.dow}"
    booking_time = booking.time.strftime('%H:%M') if booking.time else 'N/A'
    
    data = {
        "booking_id": booking.id,
        "is_success": is_success,
        "booking_day": booking_day,
        "booking_time": booking_time
    }
    
    success_count = 0
    for subscription in subscriptions:
        if send_push_notification(subscription, title, body, data):
            success_count += 1
    
    return success_count

