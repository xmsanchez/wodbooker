-- Create ClassTrainingDescription table
CREATE TABLE IF NOT EXISTS class_training_description (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    class_date DATE NOT NULL,
    training_name VARCHAR(128) NOT NULL,
    description TEXT,
    id_pizarra INTEGER,
    fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES user(id),
    UNIQUE(user_id, class_date, training_name)
);

-- Create indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_class_training_description_user_id ON class_training_description(user_id);
CREATE INDEX IF NOT EXISTS idx_class_training_description_class_date ON class_training_description(class_date);

