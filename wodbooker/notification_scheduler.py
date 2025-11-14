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
                
                # Only check classes in the next 24 hours to avoid processing too many
                min_class_time = now
                max_class_time = now + timedelta(hours=24)
                
                # Get all active bookings that need reminders
                # We need to check for classes at 1h, 30m, and 15m before
                reminder_times = [60, 30, 15]  # minutes before class
                
                for reminder_minutes in reminder_times:
                    # Calculate when the reminder should be sent for this reminder time
                    # Reminder time = class_datetime - reminder_minutes
                    # We check if now is within a 2-minute window around the reminder time
                    reminder_window_start = now - timedelta(minutes=1)
                    reminder_window_end = now + timedelta(minutes=1)
                    
                    logging.debug(
                        "Checking for %d-minute reminders: window is %s to %s",
                        reminder_minutes,
                        reminder_window_start.strftime('%Y-%m-%d %H:%M:%S'),
                        reminder_window_end.strftime('%Y-%m-%d %H:%M:%S')
                    )
                    
                    # Query bookings that:
                    # 1. Are not cancelled
                    # 2. Class is in the future (between now and 24 hours from now)
                    # 3. User has push notifications enabled
                    # 4. User has this specific reminder enabled
                    # 5. Notification hasn't been sent yet
                    
                    # Build the query with date filtering
                    query = db.session.query(WodBusterBooking).join(User).filter(
                        WodBusterBooking.is_cancelled == False,
                        User.push_notifications_enabled == True
                    )
                    
                    # Filter by user reminder preferences based on reminder_minutes
                    if reminder_minutes == 60:
                        query = query.filter(User.push_reminder_1h == True)
                    elif reminder_minutes == 30:
                        query = query.filter(User.push_reminder_30m == True)
                    elif reminder_minutes == 15:
                        query = query.filter(User.push_reminder_15m == True)
                    
                    bookings = query.all()
                    
                    logging.debug("Found %d bookings to check for %d-minute reminders", len(bookings), reminder_minutes)
                    
                    for booking in bookings:
                        # Create datetime for the class
                        class_datetime = _MADRID_TZ.localize(
                            datetime.combine(
                                booking.class_date,
                                booking.class_time
                            )
                        )
                        
                        # Skip if class is in the past or too far in the future
                        if class_datetime < min_class_time or class_datetime > max_class_time:
                            continue
                        
                        # Calculate when the reminder should be sent
                        reminder_time = class_datetime - timedelta(minutes=reminder_minutes)
                        
                        # Check if now is within the reminder time window
                        if reminder_window_start <= reminder_time <= reminder_window_end:
                            logging.debug(
                                "Reminder time match! Class at %s, reminder should be at %s, now is %s",
                                class_datetime.strftime('%Y-%m-%d %H:%M:%S'),
                                reminder_time.strftime('%Y-%m-%d %H:%M:%S'),
                                now.strftime('%Y-%m-%d %H:%M:%S')
                            )
                            
                            # Check if notification has already been sent
                            existing_notification = db.session.query(NotificationSent).filter_by(
                                wodbuster_booking_id=booking.id,
                                reminder_minutes=reminder_minutes
                            ).first()
                            
                            if not existing_notification:
                                logging.info(
                                    "Sending %d-minute reminder for booking %d (class at %s)",
                                    reminder_minutes,
                                    booking.id,
                                    class_datetime.strftime('%Y-%m-%d %H:%M:%S')
                                )
                                
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
                                        "Sent %d reminder(s) for booking %d (%d minutes before class at %s)",
                                        success_count,
                                        booking.id,
                                        reminder_minutes,
                                        class_datetime.strftime('%Y-%m-%d %H:%M:%S')
                                    )
                                else:
                                    logging.warning(
                                        "Failed to send reminder for booking %d (%d minutes before class at %s)",
                                        booking.id,
                                        reminder_minutes,
                                        class_datetime.strftime('%Y-%m-%d %H:%M:%S')
                                    )
                            else:
                                logging.debug(
                                    "Reminder already sent for booking %d (%d minutes before)",
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

