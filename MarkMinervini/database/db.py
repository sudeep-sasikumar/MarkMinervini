"""
SQLite database layer. Manages schema creation and provides a connection factory.
All tables are created on first import — safe to call multiple times (CREATE IF NOT EXISTS).
"""

import sqlite3
import logging
import os
from contextlib import contextmanager
from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              TEXT NOT NULL,
    date                TEXT NOT NULL,
    signal_type         TEXT,
    vcp_score           INTEGER,
    pivot_price         REAL,
    entry_price         REAL,
    stop_price          REAL,
    stop_pct            REAL,
    target_1            REAL,
    target_2            REAL,
    rs_rating           REAL,
    eps_growth          REAL,
    rev_growth          REAL,
    sector              TEXT,
    regime              TEXT,
    aggression_factor   REAL,
    ai_catalyst         TEXT,
    ai_earnings_quality TEXT,
    ai_sentiment        TEXT,
    telegram_sent       INTEGER DEFAULT 0,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS positions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker                  TEXT NOT NULL,
    entry_date              TEXT,
    entry_price             REAL,
    shares                  INTEGER,
    stop_price              REAL,
    account_equity_at_entry REAL,
    status                  TEXT DEFAULT 'open',
    exit_date               TEXT,
    exit_price              REAL,
    pnl_pct                 REAL,
    pnl_gbp                 REAL,
    notes                   TEXT
);

CREATE TABLE IF NOT EXISTS watchlist (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              TEXT UNIQUE NOT NULL,
    company_name        TEXT,
    sector              TEXT,
    added_date          TEXT,
    vcp_score           INTEGER,
    grade               TEXT,
    pivot_price         REAL,
    entry_price         REAL,
    stop_price          REAL,
    stop_pct            REAL,
    target_1            REAL,
    target_2            REAL,
    base_days           INTEGER,
    rs_rating           REAL,
    rs_line_new_high    INTEGER DEFAULT 0,
    eps_growth          REAL,
    rev_growth          REAL,
    fundamentals_score  INTEGER,
    earnings_date       TEXT,
    ai_notes            TEXT,
    breakout_confirmed  INTEGER DEFAULT 0,
    last_updated        DATETIME
);

-- Setups table: richer snapshot of each VCP setup for dashboard and intraday alerting.
-- Grows over time; watchlist is the "current active" view; setups is the full audit trail.
CREATE TABLE IF NOT EXISTS setups (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              TEXT NOT NULL,
    date                TEXT NOT NULL,
    vcp_score           INTEGER,
    grade               TEXT,
    pivot_price         REAL,
    entry_price         REAL,
    stop_price          REAL,
    stop_pct            REAL,
    target_1            REAL,
    target_2            REAL,
    rs_rating           REAL,
    rs_line_new_high    INTEGER DEFAULT 0,
    base_days           INTEGER,
    contractions_json   TEXT,    -- JSON: [{depth_pct, vol_avg, start, end}]
    vcp_steps_json      TEXT,    -- JSON: full steps dict from VCP detector
    fundamentals_json   TEXT,    -- JSON: fundamentals result dict
    trend_json          TEXT,    -- JSON: trend template result dict
    sector              TEXT,
    status              TEXT DEFAULT 'watchlist',  -- 'watchlist' | 'alerted' | 'intraday_alert'
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS system_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    level     TEXT,
    module    TEXT,
    message   TEXT
);

CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    expires_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date    TEXT,
    period      TEXT,
    metrics     TEXT,   -- JSON blob
    equity_curve TEXT,  -- JSON blob
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS system_status (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for frequent queries
CREATE INDEX IF NOT EXISTS idx_signals_ticker_date ON signals(ticker, date);
CREATE INDEX IF NOT EXISTS idx_watchlist_vcp ON watchlist(vcp_score DESC);
CREATE INDEX IF NOT EXISTS idx_setups_ticker_date ON setups(ticker, date);
CREATE INDEX IF NOT EXISTS idx_setups_status ON setups(status);
CREATE INDEX IF NOT EXISTS idx_cache_key ON cache(key);
CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at);
"""


def _resolve_db_path() -> str:
    """Return DB path, falling back to local dev path if /app/data doesn't exist."""
    path = settings.DB_PATH
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        # Running locally outside Docker — use project-relative path
        local_path = os.path.join(os.path.dirname(__file__), "..", "data", "sepa.db")
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        return os.path.normpath(local_path)
    return path


DB_PATH = _resolve_db_path()


def get_connection() -> sqlite3.Connection:
    """Return a new SQLite connection with row_factory set to Row."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # better concurrent read performance
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_session():
    """Context manager that yields a connection and commits/rolls back automatically."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create all tables and indexes. Safe to call on every startup."""
    logger.info("Initialising database at %s", DB_PATH)
    with db_session() as conn:
        conn.executescript(_SCHEMA)
    logger.info("Database ready")


def upsert_watchlist(ticker: str, data: dict) -> None:
    """Insert or update a watchlist entry (expanded schema)."""
    cols = [
        "ticker", "company_name", "sector", "added_date",
        "vcp_score", "grade", "pivot_price", "entry_price", "stop_price",
        "stop_pct", "target_1", "target_2", "base_days",
        "rs_rating", "rs_line_new_high", "eps_growth", "rev_growth",
        "fundamentals_score", "earnings_date", "ai_notes",
        "breakout_confirmed", "last_updated",
    ]
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "ticker")
    sql = f"""
        INSERT INTO watchlist ({', '.join(cols)}) VALUES ({placeholders})
        ON CONFLICT(ticker) DO UPDATE SET {updates}
    """
    values = [data.get(c) for c in cols]
    with db_session() as conn:
        conn.execute(sql, values)


def insert_setup(data: dict) -> int:
    """Insert a new setup snapshot into the setups table. Returns the row id."""
    cols = [
        "ticker", "date", "vcp_score", "grade", "pivot_price", "entry_price",
        "stop_price", "stop_pct", "target_1", "target_2", "rs_rating",
        "rs_line_new_high", "base_days", "contractions_json", "vcp_steps_json",
        "fundamentals_json", "trend_json", "sector", "status",
    ]
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO setups ({', '.join(cols)}) VALUES ({placeholders})"
    values = [data.get(c) for c in cols]
    with db_session() as conn:
        cursor = conn.execute(sql, values)
        return cursor.lastrowid


def insert_signal(data: dict) -> int:
    """Insert a new signal row; returns the new row id."""
    cols = ["ticker", "date", "signal_type", "vcp_score", "pivot_price",
            "entry_price", "stop_price", "stop_pct", "target_1", "target_2",
            "rs_rating", "eps_growth", "rev_growth", "sector", "regime",
            "aggression_factor", "ai_catalyst", "ai_earnings_quality", "ai_sentiment"]
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO signals ({', '.join(cols)}) VALUES ({placeholders})"
    values = [data.get(c) for c in cols]
    with db_session() as conn:
        cursor = conn.execute(sql, values)
        return cursor.lastrowid


def mark_telegram_sent(signal_id: int) -> None:
    with db_session() as conn:
        conn.execute("UPDATE signals SET telegram_sent=1 WHERE id=?", (signal_id,))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("Database initialised successfully.")
