CREATE TABLE IF NOT EXISTS wodbuster_class_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    box_url VARCHAR(128) NOT NULL,
    class_date DATE NOT NULL,
    class_time TIME NOT NULL,
    class_name VARCHAR(128) NOT NULL,
    class_type INTEGER DEFAULT 0 NOT NULL,
    class_type_id INTEGER,
    wodbuster_class_id INTEGER,
    fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES user(id) ON DELETE CASCADE,
    CONSTRAINT _user_date_wb_class_id_uc UNIQUE (user_id, class_date, wodbuster_class_id)
);

CREATE INDEX IF NOT EXISTS ix_wodbuster_class_schedule_user_id ON wodbuster_class_schedule (user_id);
CREATE INDEX IF NOT EXISTS ix_wodbuster_class_schedule_class_date ON wodbuster_class_schedule (class_date);
CREATE INDEX IF NOT EXISTS ix_wodbuster_class_schedule_lookup ON wodbuster_class_schedule (user_id, class_date, class_time, class_type);
