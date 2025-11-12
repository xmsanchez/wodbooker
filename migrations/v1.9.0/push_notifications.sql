-- Add push notification and auto-sync settings to user table
-- SQLite doesn't support IF NOT EXISTS for ALTER TABLE, so we check first
-- Note: This migration script should be run via migrate.py which handles errors gracefully
ALTER TABLE user ADD COLUMN push_notifications_enabled BOOLEAN DEFAULT 0;
ALTER TABLE user ADD COLUMN push_reminder_1h BOOLEAN DEFAULT 0;
ALTER TABLE user ADD COLUMN push_reminder_30m BOOLEAN DEFAULT 0;
ALTER TABLE user ADD COLUMN push_reminder_15m BOOLEAN DEFAULT 0;
ALTER TABLE user ADD COLUMN wodbuster_autosync_enabled BOOLEAN DEFAULT 0;

-- Create PushSubscription table
CREATE TABLE IF NOT EXISTS push_subscription (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    endpoint VARCHAR(512) NOT NULL,
    p256dh VARCHAR(256) NOT NULL,
    auth VARCHAR(128) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES user(id),
    UNIQUE(user_id, endpoint)
);

-- Create index for efficient queries
CREATE INDEX IF NOT EXISTS idx_push_subscription_user_id ON push_subscription(user_id);

-- Create NotificationSent table
CREATE TABLE IF NOT EXISTS notification_sent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wodbuster_booking_id INTEGER NOT NULL,
    reminder_minutes INTEGER NOT NULL,
    sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (wodbuster_booking_id) REFERENCES wodbuster_booking(id),
    UNIQUE(wodbuster_booking_id, reminder_minutes)
);

-- Create index for efficient queries
CREATE INDEX IF NOT EXISTS idx_notification_sent_booking_id ON notification_sent(wodbuster_booking_id);

