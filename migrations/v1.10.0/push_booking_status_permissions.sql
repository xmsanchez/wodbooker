-- Add push notification permissions for booking status to user table
-- SQLite doesn't support IF NOT EXISTS for ALTER TABLE, so we check first
-- Note: This migration script should be run via migrate.py which handles errors gracefully
ALTER TABLE user ADD COLUMN push_permission_success BOOLEAN DEFAULT 1;
ALTER TABLE user ADD COLUMN push_permission_failure BOOLEAN DEFAULT 1;

