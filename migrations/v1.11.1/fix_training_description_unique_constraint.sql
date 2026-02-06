-- Fix unique constraint for class_training_description table
-- Change from (user_id, class_date, training_name) to (user_id, class_date, id_pizarra)
-- This is needed because multiple pizarras can have the same name but different IDs

-- SQLite doesn't support ALTER TABLE to drop/add constraints directly
-- We need to recreate the table with the new constraint

-- Step 1: Create new table with correct constraint
CREATE TABLE IF NOT EXISTS class_training_description_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    class_date DATE NOT NULL,
    training_name VARCHAR(128) NOT NULL,
    description TEXT,
    id_pizarra INTEGER NOT NULL,
    fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES user(id),
    UNIQUE(user_id, class_date, id_pizarra)
);

-- Step 2: Copy data from old table (only records with id_pizarra)
INSERT INTO class_training_description_new (id, user_id, class_date, training_name, description, id_pizarra, fetched_at)
SELECT id, user_id, class_date, training_name, description, id_pizarra, fetched_at
FROM class_training_description
WHERE id_pizarra IS NOT NULL;

-- Step 3: Drop old table
DROP TABLE IF EXISTS class_training_description;

-- Step 4: Rename new table
ALTER TABLE class_training_description_new RENAME TO class_training_description;

-- Step 5: Recreate indexes
CREATE INDEX IF NOT EXISTS idx_class_training_description_user_id ON class_training_description(user_id);
CREATE INDEX IF NOT EXISTS idx_class_training_description_class_date ON class_training_description(class_date);

