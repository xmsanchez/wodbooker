-- Rollback script for class_training_description migration
-- This script reverses the changes made in class_training_description.sql

-- Drop indexes first (they depend on the table)
DROP INDEX IF EXISTS idx_class_training_description_class_date;
DROP INDEX IF EXISTS idx_class_training_description_user_id;

-- Drop the table
DROP TABLE IF EXISTS class_training_description;

