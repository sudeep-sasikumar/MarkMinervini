"""
Economic calendar — Section 7 (Market Intelligence).
Fetches high-impact macro events from Finnhub /calendar/economic.
Flags events due in the next 2 trading days.

High-impact events (Fed, CPI, NFP, PCE, GDP) can trigger regime size reduction
even before markets move. The scheduler injects this data into morning briefings.

Cached once per day to avoid hammering the Finnhub API.
"""

import logging
from datetime import date, timedelta
from typing import Optional

from config import settings
from data.cache import get as cache_get, set as cache_set, TTL_1D

logger = logging.getLogger(__name__)

# Events classified as high-impact for US equities
HIGH_IMPACT_KEYWORDS = [
    "Federal Reserve", "FOMC", "Fed Rate", "Interest Rate Decision",
    "CPI", "Consumer Price Index",
    "PCE", "Personal Consumption",
    "Nonfarm Payrolls", "NFP", "Unemployment",
    "GDP", "Gross Domestic Product",
    "PPI", "Producer Price",
    "Retail Sales",
    "ISM Manufacturing",
    "Durable Goods",
]


def fetch_economic_events(days_ahead: int = 5) -> list[dict]:
    """
    Fetch upcoming economic events from Finnhub for the next `days_ahead` days.

    Returns a list of event dicts:
        [{
          "event": str,
          "date": str,       # YYYY-MM-DD
          "impact": str,     # "high" | "medium" | "low"
          "country": str,
          "estimate": float | None,
          "prev": float | None,
        }]

    Results are cached for 1 day.
    """
    cache_key = f"economic_calendar:{days_ahead}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    if not settings.FINNHUB_API_KEY:
        logger.debug("FINNHUB_API_KEY not set — economic calendar unavailable")
        return []

    today = date.today()
    from_date = today.isoformat()
    to_date = (today + timedelta(days=days_ahead)).isoformat()

    try:
        import requests
        url = "https://finnhub.io/api/v1/calendar/economic"
        resp = requests.get(
            url,
            params={
                "from": from_date,
                "to": to_date,
                "token": settings.FINNHUB_API_KEY,
            },
            timeout=10,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as exc:
        logger.warning("Economic calendar fetch failed: %s", exc)
        return []

    events = []
    for item in raw.get("economicCalendar", []):
        event_name = item.get("event", "")
        country = item.get("country", "")

        # Filter to US events only
        if country not in ("US", "United States", ""):
            continue

        impact = _classify_impact(event_name)
        events.append({
            "event": event_name,
            "date": item.get("time", "")[:10],  # YYYY-MM-DD
            "impact": impact,
            "country": country,
            "estimate": item.get("estimate"),
            "prev": item.get("prev"),
        })

    cache_set(cache_key, events, ttl_seconds=TTL_1D)
    logger.info("Economic calendar: %d US events in next %d days (%d high-impact)",
                len(events), days_ahead, sum(1 for e in events if e["impact"] == "high"))
    return events


def get_high_impact_events(days_ahead: int = 2) -> list[dict]:
    """
    Return only HIGH-impact US economic events within the next `days_ahead` trading days.
    Used to flag risky windows in regime detector and morning briefing.
    """
    all_events = fetch_economic_events(days_ahead=days_ahead + 2)  # fetch a bit extra
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)

    high_impact = []
    for event in all_events:
        if event["impact"] != "high":
            continue
        try:
            event_date = date.fromisoformat(event["date"])
            if today <= event_date <= cutoff:
                high_impact.append(event)
        except (ValueError, TypeError):
            continue

    return high_impact


def format_events_for_briefing(events: list[dict]) -> str:
    """
    Format a list of economic events into a human-readable Telegram string.
    Example output:
        ⚠️ HIGH-IMPACT EVENTS AHEAD:
        • 2026-05-25: FOMC Meeting (Fed Rate Decision)
        • 2026-05-27: Nonfarm Payrolls (NFP)
    """
    if not events:
        return ""
    lines = ["⚠️ HIGH-IMPACT MACRO EVENTS THIS WEEK:"]
    for ev in events:
        lines.append(f"  • {ev['date']}: {ev['event']}")
    return "\n".join(lines)


def is_high_impact_window() -> bool:
    """
    Return True if a high-impact event falls within the next 2 trading days.
    Used by regime detector to recommend position size reduction.
    """
    return len(get_high_impact_events(days_ahead=2)) > 0


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _classify_impact(event_name: str) -> str:
    """Classify an event as 'high', 'medium', or 'low' based on keywords."""
    name_upper = event_name.upper()
    for keyword in HIGH_IMPACT_KEYWORDS:
        if keyword.upper() in name_upper:
            return "high"
    # Medium-impact heuristics
    medium_keywords = ["PMI", "Housing", "Trade Balance", "Budget", "Sentiment"]
    for keyword in medium_keywords:
        if keyword.upper() in name_upper:
            return "medium"
    return "low"


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=logging.INFO)

    events = get_high_impact_events(days_ahead=5)
    print(f"economic_calendar.py: {len(events)} high-impact events in next 5 days")
    for ev in events:
        print(f"  {ev['date']}: {ev['event']} (impact={ev['impact']})")
    print(format_events_for_briefing(events))
