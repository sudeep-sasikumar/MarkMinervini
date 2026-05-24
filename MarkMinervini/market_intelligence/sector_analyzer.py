"""
Sector leadership and rotation analysis (Section 7).
Checks each sector ETF against the Trend Template.
Only stocks in Stage 2 sectors pass the sector gate.
"""

import logging
from typing import Optional

import pandas as pd

from config import settings
from data.fetcher import fetch_ohlcv
from data.cache import get as cache_get, set as cache_set, TTL_6H
from screening.trend_template import check_trend_template
from market_intelligence.ai_analyst import analyse_sector_leadership

logger = logging.getLogger(__name__)


def fetch_sector_performance() -> dict[str, dict]:
    """
    Compute 1-month and 3-month % performance for each sector ETF.
    Returns: {sector_name: {"etf": str, "1m_pct": float, "3m_pct": float, "stage2": bool}}
    """
    cache_key = "sector:performance"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    result = {}
    for sector, etf in settings.SECTOR_ETF_MAP.items():
        try:
            # Use 2y so the 200-SMA and 52-week range checks in check_trend_template
            # have enough history.  1y is borderline and fails intermittently.
            df = fetch_ohlcv(etf, period="2y")
            if df is None or len(df) < 66:
                continue
            close = df["Close"]
            price_now = float(close.iloc[-1])
            price_1m = float(close.iloc[-21]) if len(close) >= 21 else price_now
            price_3m = float(close.iloc[-63]) if len(close) >= 63 else price_now

            one_month_pct = (price_now / price_1m - 1) * 100
            three_month_pct = (price_now / price_3m - 1) * 100

            # Stage 2 check for this sector ETF.
            # Pass rs_rating=100.0 so the RS criterion (c8) does not block sector ETFs —
            # we don't compute a cross-ETF RS percentile; the test should be structural only.
            # Previously rs_rating=50.0 caused c8 (50 < RS_MINIMUM=70) to always fail,
            # making every sector appear as "Not Stage 2" and blocking all signals.
            tt = check_trend_template(etf, df, rs_rating=100.0)
            stage2 = tt["passes"]

            result[sector] = {
                "etf": etf,
                "1m_pct": round(one_month_pct, 2),
                "3m_pct": round(three_month_pct, 2),
                "stage2": stage2,
                "tt_score": tt["score"],
            }
        except Exception as exc:
            logger.warning("Sector performance error for %s (%s): %s", sector, etf, exc)

    cache_set(cache_key, result, ttl_seconds=TTL_6H)
    return result


def normalise_sector(sector: str) -> str:
    """
    Normalise a yfinance sector name to the canonical key used in SECTOR_ETF_MAP.
    e.g. "Healthcare" → "Health Care", "Consumer Cyclical" → "Consumer Discretionary"
    Falls back to the original value if no alias exists.
    """
    return settings.SECTOR_NAME_ALIASES.get(sector, sector)


def get_sector_stage2_status(sector: str) -> bool:
    """
    Return True if the sector ETF is in Stage 2 (passes all 8 Trend Template criteria).
    Used as an anti-false-positive gate before generating alerts.
    Normalises yfinance sector names before lookup to prevent false mismatches.
    """
    canonical = normalise_sector(sector)
    perf = fetch_sector_performance()
    sector_data = perf.get(canonical)
    if sector_data is None:
        logger.warning(
            "No sector ETF data for '%s' (canonical: '%s') — blocking signal (conservative)",
            sector, canonical
        )
        # Conservative: unknown sector → block. Prevents bad signals from unmapped sectors.
        return False
    return sector_data.get("stage2", False)


def get_leading_sectors(top_n: int = 3) -> list[dict]:
    """Return top N sectors ranked by 3-month performance."""
    perf = fetch_sector_performance()
    ranked = sorted(perf.items(), key=lambda x: x[1].get("3m_pct", -99), reverse=True)
    return [
        {"sector": s, **data}
        for s, data in ranked[:top_n]
    ]


def get_ai_sector_analysis() -> dict:
    """Run AI sector leadership analysis using current sector performance data."""
    perf = fetch_sector_performance()
    # Simplify to just the numbers for AI prompt
    simplified = {
        sector: {"1m_pct": d["1m_pct"], "3m_pct": d["3m_pct"]}
        for sector, d in perf.items()
    }
    return analyse_sector_leadership(simplified)


if __name__ == "__main__":
    import logging as _logging
    from database.db import init_db

    _logging.basicConfig(level=logging.INFO)
    init_db()

    perf = fetch_sector_performance()
    leaders = get_leading_sectors(3)
    print("sector_analyzer.py: Top 3 sectors:")
    for s in leaders:
        stage = "✅ Stage 2" if s["stage2"] else "❌ Not Stage 2"
        print(f"  {s['sector']} ({s['etf']}): 1m={s['1m_pct']:+.1f}% | "
              f"3m={s['3m_pct']:+.1f}% | {stage}")
