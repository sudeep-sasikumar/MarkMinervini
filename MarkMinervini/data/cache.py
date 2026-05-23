"""
SQLite-backed cache layer for external API responses.
Prevents redundant calls and respects rate limits by storing results with TTL.
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Optional

from database.db import get_connection

logger = logging.getLogger(__name__)


def _now_str() -> str:
    return datetime.utcnow().isoformat()


def get(key: str) -> Optional[Any]:
    """
    Retrieve a cached value. Returns None if missing or expired.
    Expired entries are deleted on read to keep the table clean.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value, expires_at FROM cache WHERE key=?", (key,)
        ).fetchone()
        if row is None:
            return None
        if datetime.fromisoformat(row["expires_at"]) < datetime.utcnow():
            conn.execute("DELETE FROM cache WHERE key=?", (key,))
            conn.commit()
            return None
        return json.loads(row["value"])
    except Exception as exc:
        logger.warning("Cache read error for key=%s: %s", key, exc)
        return None
    finally:
        conn.close()


def set(key: str, value: Any, ttl_seconds: int = 3600) -> None:
    """
    Store a value with a TTL (seconds). Overwrites any existing entry.
    """
    expires_at = (datetime.utcnow() + timedelta(seconds=ttl_seconds)).isoformat()
    try:
        serialised = json.dumps(value, default=str)
    except (TypeError, ValueError) as exc:
        logger.warning("Cache serialisation failed for key=%s: %s", key, exc)
        return

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO cache(key, value, expires_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, expires_at=excluded.expires_at",
            (key, serialised, expires_at),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("Cache write error for key=%s: %s", key, exc)
    finally:
        conn.close()


def delete(key: str) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM cache WHERE key=?", (key,))
        conn.commit()
    finally:
        conn.close()


def purge_expired() -> int:
    """Delete all expired entries; returns count removed."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM cache WHERE expires_at < ?", (_now_str(),)
        )
        conn.commit()
        removed = cursor.rowcount
        if removed:
            logger.debug("Cache: purged %d expired entries", removed)
        return removed
    finally:
        conn.close()


# Convenience TTL constants (seconds)
TTL_1H = 3_600
TTL_6H = 21_600
TTL_1D = 86_400
TTL_7D = 604_800


if __name__ == "__main__":
    from database.db import init_db
    logging.basicConfig(level=logging.DEBUG)
    init_db()
    set("test_key", {"hello": "world"}, ttl_seconds=60)
    result = get("test_key")
    assert result == {"hello": "world"}, f"Cache round-trip failed: {result}"
    print("cache.py: self-test passed")
