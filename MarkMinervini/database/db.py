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

-- Scan funnel detail table: which tickers cleared each pipeline stage in the
-- most-recent scan.  Replaces the previous data on every scan so the view is
-- always current.  Stage values: 'trend_template', 'fundamentals', 'developing_vcp'.
-- The 'watchlist' stage is already in the watchlist table.
CREATE TABLE IF NOT EXISTS scan_funnel_tickers (
    stage            TEXT NOT NULL,
    ticker           TEXT NOT NULL,
    scan_date        TEXT NOT NULL,
    rs_rating        REAL,
    tt_score         INTEGER,
    eps_growth       REAL,
    rev_growth       REAL,
    base_days        INTEGER,
    contractions     INTEGER,
    vcp_score        INTEGER,
    rejection_reason TEXT,
    PRIMARY KEY (stage, ticker)
);

-- Indexes for frequent queries
CREATE INDEX IF NOT EXISTS idx_signals_ticker_date ON signals(ticker, date);
CREATE INDEX IF NOT EXISTS idx_watchlist_vcp ON watchlist(vcp_score DESC);
CREATE INDEX IF NOT EXISTS idx_setups_ticker_date ON setups(ticker, date);
CREATE INDEX IF NOT EXISTS idx_setups_status ON setups(status);
CREATE INDEX IF NOT EXISTS idx_cache_key ON cache(key);
CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at);
CREATE INDEX IF NOT EXISTS idx_funnel_stage ON scan_funnel_tickers(stage);
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


# ---------------------------------------------------------------------------
# Forward-only column migrations
# ---------------------------------------------------------------------------
# SQLite does not support ALTER TABLE ADD COLUMN IF NOT EXISTS, so we query
# PRAGMA table_info() first and only issue ALTER TABLE for missing columns.
# Add new columns here whenever the schema grows; never remove old entries.
# ---------------------------------------------------------------------------
_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "scan_funnel_tickers": [
        ("rs_rating",        "REAL"),
        ("tt_score",         "INTEGER"),
        ("eps_growth",       "REAL"),
        ("rev_growth",       "REAL"),
        ("base_days",        "INTEGER"),
        ("contractions",     "INTEGER"),
        ("vcp_score",        "INTEGER"),
        ("rejection_reason", "TEXT"),
    ],
    "watchlist": [
        ("grade",               "TEXT"),
        ("entry_price",         "REAL"),
        ("stop_price",          "REAL"),
        ("stop_pct",            "REAL"),
        ("target_1",            "REAL"),
        ("target_2",            "REAL"),
        ("base_days",           "INTEGER"),
        ("rs_line_new_high",    "INTEGER DEFAULT 0"),
        ("eps_growth",          "REAL"),
        ("rev_growth",          "REAL"),
        ("fundamentals_score",  "INTEGER"),
        ("earnings_date",       "TEXT"),
        ("ai_notes",            "TEXT"),
        ("breakout_confirmed",  "INTEGER DEFAULT 0"),
    ],
    "signals": [
        ("ai_catalyst",         "TEXT"),
        ("ai_earnings_quality", "TEXT"),
        ("ai_sentiment",        "TEXT"),
        ("aggression_factor",   "REAL"),
    ],
}


def _ensure_columns() -> None:
    """
    Add any missing columns to existing tables (forward-only schema migration).

    Safe to run on every startup — reads PRAGMA table_info to determine which
    columns already exist and only issues ALTER TABLE for the absent ones.
    New tables created by _SCHEMA will already have all columns, so this is
    a no-op for fresh installs.
    """
    with db_session() as conn:
        for table, columns in _MIGRATIONS.items():
            existing = {
                row[1]
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for col_name, col_def in columns:
                if col_name not in existing:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"
                    )
                    logger.info("Migration: added column %s.%s (%s)", table, col_name, col_def)


def init_db() -> None:
    """Create all tables and indexes. Safe to call on every startup."""
    logger.info("Initialising database at %s", DB_PATH)
    with db_session() as conn:
        conn.executescript(_SCHEMA)
    _ensure_columns()
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


def cleanup_stale_watchlist(max_age_days: int = 14) -> int:
    """
    Remove watchlist entries that have not been refreshed in max_age_days.
    Returns the number of rows deleted.

    A stock staying on the watchlist indefinitely is misleading — it implies an active
    setup when the scanner may no longer be confirming it (VCP may have failed, or the
    stock may have broken down).
    """
    with db_session() as conn:
        result = conn.execute(
            "DELETE FROM watchlist WHERE last_updated < date('now', ?)",
            (f"-{max_age_days} days",),
        )
        deleted = result.rowcount
    if deleted > 0:
        logger.info("Watchlist cleanup: removed %d stale entries (age > %d days)", deleted, max_age_days)
    return deleted


def remove_watchlist_ticker(ticker: str) -> None:
    """Remove a specific ticker from the watchlist (e.g., after an earnings miss)."""
    with db_session() as conn:
        conn.execute("DELETE FROM watchlist WHERE ticker=?", (ticker,))


def set_scan_trigger() -> None:
    """
    Signal the scanner process to run a full scan immediately.
    The scanner's 60-second heartbeat loop checks for this flag and fires
    run_full_scan() when found.  Safe to call from the dashboard process.
    """
    with db_session() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO system_status(key, value, updated_at) "
            "VALUES('scan_trigger', 'requested', CURRENT_TIMESTAMP)"
        )


def check_and_clear_scan_trigger() -> bool:
    """
    Atomically check for a pending scan trigger and claim it (mark as running).
    Returns True if a trigger was found and claimed; False if none is pending.
    Call clear_scan_trigger() when the triggered scan completes.
    """
    with db_session() as conn:
        row = conn.execute(
            "SELECT value FROM system_status WHERE key='scan_trigger'"
        ).fetchone()
        if row and row["value"] == "requested":
            conn.execute(
                "INSERT OR REPLACE INTO system_status(key, value, updated_at) "
                "VALUES('scan_trigger', 'running', CURRENT_TIMESTAMP)"
            )
            return True
    return False


def clear_scan_trigger() -> None:
    """Mark a dashboard-triggered scan as done so it is not re-triggered."""
    with db_session() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO system_status(key, value, updated_at) "
            "VALUES('scan_trigger', 'done', CURRENT_TIMESTAMP)"
        )


def save_scan_funnel_stage(stage: str, rows: list[dict]) -> None:
    """
    Replace all rows for one scan-pipeline stage with fresh data from the latest
    scan.  The full previous set for this stage is deleted first so stale tickers
    (stocks that dropped out of this stage since the last run) are not shown.

    Args:
        stage: 'trend_template', 'fundamentals', or 'developing_vcp'
        rows:  List of dicts with keys: ticker, scan_date, rs_rating, tt_score,
               eps_growth, rev_growth, base_days, contractions, vcp_score,
               rejection_reason  (all optional except ticker + scan_date)
    """
    with db_session() as conn:
        conn.execute("DELETE FROM scan_funnel_tickers WHERE stage=?", (stage,))
        for r in rows:
            conn.execute(
                "INSERT INTO scan_funnel_tickers "
                "(stage, ticker, scan_date, rs_rating, tt_score, eps_growth, "
                "rev_growth, base_days, contractions, vcp_score, rejection_reason) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    stage,
                    r["ticker"],
                    r.get("scan_date", ""),
                    r.get("rs_rating"),
                    r.get("tt_score"),
                    r.get("eps_growth"),
                    r.get("rev_growth"),
                    r.get("base_days"),
                    r.get("contractions"),
                    r.get("vcp_score"),
                    r.get("rejection_reason"),
                ),
            )


def get_scan_funnel_stage(stage: str) -> list[dict]:
    """Return all tickers for a given scan stage, ordered by RS rating descending."""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM scan_funnel_tickers WHERE stage=? ORDER BY rs_rating DESC",
            (stage,),
        ).fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("Database initialised successfully.")
