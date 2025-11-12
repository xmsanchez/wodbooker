import logging
from datetime import datetime, timedelta
import time
import pytz
from .models import db, User, WodBusterBooking, NotificationSent
from .push_notifications import send_class_reminder

_MADRID_TZ = pytz.timezone('Europe/Madrid')


def _notification_scheduler_loop(app_context):
    """
    Background thread to check for upcoming classes and send push notifications
    """
    app_context.push()
    with app_context:
        while True:
            try:
                now = datetime.now(_MADRID_TZ)
                
                # Check for classes in the next 2 hours
                time_window_start = now
                time_window_end = now + timedelta(hours=2)
                
                # Get all active bookings that need reminders
                # We need to check for classes at 1h, 30m, and 15m before
                reminder_times = [60, 30, 15]  # minutes before class
                
                for reminder_minutes in reminder_times:
                    # Calculate the target time window for this reminder
                    target_time_start = now + timedelta(minutes=reminder_minutes - 1)
                    target_time_end = now + timedelta(minutes=reminder_minutes + 1)
                    
                    # Query bookings that:
                    # 1. Are not cancelled
                    # 2. Have class datetime within target window
                    # 3. User has push notifications enabled
                    # 4. User has this specific reminder enabled
                    # 5. Notification hasn't been sent yet
                    
                    bookings = db.session.query(WodBusterBooking).join(User).filter(
                        WodBusterBooking.is_cancelled == False,
                        User.push_notifications_enabled == True
                    ).all()
                    
                    for booking in bookings:
                        # Check if user has this reminder enabled
                        if reminder_minutes == 60 and not booking.user.push_reminder_1h:
                            continue
                        if reminder_minutes == 30 and not booking.user.push_reminder_30m:
                            continue
                        if reminder_minutes == 15 and not booking.user.push_reminder_15m:
                            continue
                        
                        # Create datetime for the class
                        class_datetime = _MADRID_TZ.localize(
                            datetime.combine(
                                booking.class_date,
                                booking.class_time
                            )
                        )
                        
                        # Check if class is in the target time window
                        if target_time_start <= class_datetime <= target_time_end:
                            # Check if notification has already been sent
                            existing_notification = db.session.query(NotificationSent).filter_by(
                                wodbuster_booking_id=booking.id,
                                reminder_minutes=reminder_minutes
                            ).first()
                            
                            if not existing_notification:
                                # Send notification
                                success_count = send_class_reminder(
                                    booking.user,
                                    booking,
                                    reminder_minutes
                                )
                                
                                if success_count > 0:
                                    # Record that notification was sent
                                    notification_sent = NotificationSent(
                                        wodbuster_booking_id=booking.id,
                                        reminder_minutes=reminder_minutes
                                    )
                                    db.session.add(notification_sent)
                                    db.session.commit()
                                    logging.info(
                                        "Sent %d reminder(s) for booking %d (%d minutes before)",
                                        success_count,
                                        booking.id,
                                        reminder_minutes
                                    )
                                else:
                                    logging.warning(
                                        "Failed to send reminder for booking %d (%d minutes before)",
                                        booking.id,
                                        reminder_minutes
                                    )
                
                # Clean up old notification records (older than 7 days)
                cutoff_date = now - timedelta(days=7)
                old_notifications = db.session.query(NotificationSent).join(
                    WodBusterBooking
                ).filter(
                    NotificationSent.sent_at < cutoff_date
                ).all()
                
                for old_notification in old_notifications:
                    db.session.delete(old_notification)
                
                if old_notifications:
                    db.session.commit()
                    logging.info("Cleaned up %d old notification records", len(old_notifications))
                
            except Exception as e:
                logging.exception("Error in notification scheduler loop: %s", str(e))
            
            # Sleep for 1 minute before checking again
            time.sleep(60)

