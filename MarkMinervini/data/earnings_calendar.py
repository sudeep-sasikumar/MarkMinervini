"""
Earnings calendar fetcher via Finnhub /calendar/earnings.
Cached once per day. Provides days-to-earnings lookup and post-earnings assessment.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

import requests

from config import settings
from data.cache import get as cache_get, set as cache_set, TTL_1D

logger = logging.getLogger(__name__)


def _finnhub_earnings_calendar(from_date: str, to_date: str) -> list[dict]:
    """Fetch earnings calendar from Finnhub for a date range."""
    if not settings.FINNHUB_API_KEY:
        return []
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={"from": from_date, "to": to_date, "token": settings.FINNHUB_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("earningsCalendar", [])
    except Exception as exc:
        logger.warning("Earnings calendar fetch failed: %s", exc)
        return []


def get_earnings_calendar(days_ahead: int = 30) -> list[dict]:
    """
    Return earnings events for the next N days.
    Cached for 1 day. Each entry: {symbol, date, epsEstimate, revenueEstimate, ...}
    """
    cache_key = f"earnings_calendar:{days_ahead}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    today = date.today()
    to_date = today + timedelta(days=days_ahead)
    events = _finnhub_earnings_calendar(
        today.isoformat(), to_date.isoformat()
    )
    cache_set(cache_key, events, ttl_seconds=TTL_1D)
    return events


def days_to_earnings(ticker: str) -> Optional[int]:
    """
    Return the number of calendar days until the next earnings report.
    Returns None if earnings date is unknown.
    Returns a negative number if earnings were in the past N days.
    """
    events = get_earnings_calendar(days_ahead=90)
    today = date.today()
    upcoming = []
    for ev in events:
        if ev.get("symbol") != ticker:
            continue
        try:
            ev_date = date.fromisoformat(ev["date"])
            upcoming.append(ev_date)
        except (KeyError, ValueError):
            continue

    if not upcoming:
        return None

    # Return the nearest future (or most recent past) date
    closest = min(upcoming, key=lambda d: abs((d - today).days))
    return (closest - today).days


def earnings_safety_status(ticker: str) -> dict:
    """
    Classify the earnings risk for a ticker.
    Returns:
        {
          "action": "block" | "warn" | "allow" | "unknown",
          "days_to_earnings": int | None,
          "size_factor": float,   # 1.0 = full, 0.5 = halved
          "message": str,
        }
    """
    days = days_to_earnings(ticker)

    if days is None:
        return {
            "action": "allow",
            "days_to_earnings": None,
            "size_factor": 1.0,
            "message": "⚠️ Earnings date unverified — proceed with caution",
        }

    if days <= settings.EARNINGS_BLOCK_DAYS:
        return {
            "action": "block",
            "days_to_earnings": days,
            "size_factor": 0.0,
            "message": f"🚫 Earnings in {days}d — signal blocked",
        }

    if days <= settings.EARNINGS_WARNING_DAYS:
        return {
            "action": "warn",
            "days_to_earnings": days,
            "size_factor": 0.5,
            "message": f"⚠️ Earnings in {days}d — half-size only",
        }

    return {
        "action": "allow",
        "days_to_earnings": days,
        "size_factor": 1.0,
        "message": f"✅ Earnings in {days}d — clear",
    }


def assess_post_earnings(ticker: str) -> Optional[dict]:
    """
    Check if a ticker reported earnings in the last 2 days.
    Returns assessment dict or None if no recent earnings found.
    """
    # Look back over the recent window defined by EARNINGS_LOOKBACK_DAYS
    today = date.today()
    lookback_start = today - timedelta(days=settings.EARNINGS_LOOKBACK_DAYS)

    # Refetch past events (calendar endpoint typically covers recent dates too)
    recent = _finnhub_earnings_calendar(
        lookback_start.isoformat(), today.isoformat()
    )

    for ev in recent:
        if ev.get("symbol") != ticker:
            continue
        try:
            ev_date = date.fromisoformat(ev["date"])
        except (KeyError, ValueError):
            continue
        if lookback_start <= ev_date <= today:
            eps_act = ev.get("epsActual")
            eps_est = ev.get("epsEstimate")
            rev_act = ev.get("revenueActual")
            rev_est = ev.get("revenueEstimate")

            eps_beat = (eps_act is not None and eps_est is not None and
                        eps_act >= eps_est)
            rev_beat = (rev_act is not None and rev_est is not None and
                        rev_act >= rev_est)

            if eps_beat and rev_beat:
                verdict = "EARNINGS TAILWIND"
                note = "✅ Beat on EPS and Revenue — reassess for VCP"
            else:
                verdict = "EARNINGS MISS"
                note = "❌ Miss on EPS or Revenue — remove from watchlist"

            return {
                "ticker": ticker,
                "earnings_date": ev_date.isoformat(),
                "verdict": verdict,
                "note": note,
                "eps_actual": eps_act,
                "eps_estimate": eps_est,
                "rev_actual": rev_act,
                "rev_estimate": rev_est,
            }
    return None


if __name__ == "__main__":
    from database.db import init_db
    logging.basicConfig(level=logging.INFO)
    init_db()
    status = earnings_safety_status("AAPL")
    print(f"earnings_calendar.py: AAPL earnings status = {status}")
