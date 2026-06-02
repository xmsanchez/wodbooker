CREATE TABLE IF NOT EXISTS athlete_monthly_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    booked INTEGER DEFAULT 0,
    attended INTEGER DEFAULT 0,
    no_show INTEGER DEFAULT 0,
    cancelled INTEGER DEFAULT 0,
    billing_period_cancelled INTEGER,
    tariff_name VARCHAR(128),
    quota_used INTEGER,
    quota_total INTEGER,
    period_from DATE,
    period_to DATE,
    fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    source VARCHAR(32),
    FOREIGN KEY (user_id) REFERENCES user(id),
    UNIQUE(user_id, year, month)
);

CREATE INDEX IF NOT EXISTS idx_athlete_monthly_stats_user_id ON athlete_monthly_stats(user_id);
CREATE INDEX IF NOT EXISTS idx_athlete_monthly_stats_year_month ON athlete_monthly_stats(user_id, year, month);
