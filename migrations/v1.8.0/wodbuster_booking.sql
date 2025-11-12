-- Create WodBusterBooking table
CREATE TABLE IF NOT EXISTS wodbuster_booking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    class_id INTEGER NOT NULL,
    class_date DATE NOT NULL,
    class_time TIME NOT NULL,
    class_name VARCHAR(128),
    class_type VARCHAR(32),
    box_url VARCHAR(128) NOT NULL,
    fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_cancelled BOOLEAN DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES user(id),
    UNIQUE(user_id, class_id, class_date)
);

-- Create indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_wodbuster_booking_user_id ON wodbuster_booking(user_id);
CREATE INDEX IF NOT EXISTS idx_wodbuster_booking_class_date ON wodbuster_booking(class_date);

