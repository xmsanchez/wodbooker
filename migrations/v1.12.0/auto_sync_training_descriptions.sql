-- Migration v1.12.0: Add auto_sync_training_descriptions field to user table
-- This field enables automatic synchronization of training descriptions when they are not available

ALTER TABLE user ADD COLUMN auto_sync_training_descriptions BOOLEAN DEFAULT 0;

