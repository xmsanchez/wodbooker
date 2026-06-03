-- Migration v1.14.0: Cancel window settings for autobook

ALTER TABLE user ADD COLUMN cancel_window_hours INTEGER NOT NULL DEFAULT 3;
ALTER TABLE user ADD COLUMN stop_autobook_in_cancel_window BOOLEAN NOT NULL DEFAULT 0;

ALTER TABLE booking ADD COLUMN book_despite_cancel_window BOOLEAN;
