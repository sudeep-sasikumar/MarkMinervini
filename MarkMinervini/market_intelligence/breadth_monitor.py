"""
Market breadth monitor (Section 7).
Computes the percentage of S&P 500 stocks trading above their own 200-day SMA.
Uses preloaded price data to avoid redundant downloads.
"""

import logging
from typing import Optional

import pandas as pd

from config import settings
from data.cache import get as cache_get, set as cache_set, TTL_6H
from data.fetcher import fetch_ohlcv_batch
from screening.universe import get_sp500_tickers

logger = logging.getLogger(__name__)


def compute_breadth(price_data: Optional[dict[str, pd.DataFrame]] = None) -> float:
    """
    Return % of S&P 500 stocks above their own 200-day SMA.
    If price_data is provided, uses that (faster, avoids re-download).
    Otherwise fetches S&P 500 tickers itself.
    Cached 6 hours.
    """
    cache_key = "breadth:sp500_above_200sma"
    cached = cache_get(cache_key)
    if cached is not None:
        return float(cached)

    if price_data is None:
        sp500 = get_sp500_tickers()
        price_data = fetch_ohlcv_batch(sp500, period="2y")

    above = 0
    total = 0
    for ticker, df in price_data.items():
        if df is None or len(df) < 200:
            continue
        try:
            close = df["Close"]
            sma200 = float(close.rolling(200).mean().iloc[-1])
            price = float(close.iloc[-1])
            total += 1
            if price > sma200:
                above += 1
        except Exception as exc:
            logger.debug("Breadth calc error for %s: %s", ticker, exc)

    if total == 0:
        return 50.0

    pct = round(above / total * 100, 1)
    logger.info("Market breadth: %d/%d stocks above 200-SMA = %.1f%%", above, total, pct)
    cache_set(cache_key, pct, ttl_seconds=TTL_6H)
    return pct


def breadth_label(pct: float) -> str:
    """Return a human-readable label for a breadth percentage."""
    if pct >= settings.BREADTH_BULL:
        return f"🟢 HEALTHY ({pct:.1f}%)"
    if pct >= settings.BREADTH_MIXED_LOW:
        return f"🟡 MIXED ({pct:.1f}%)"
    if pct >= 20:
        return f"🟠 WEAK ({pct:.1f}%)"
    return f"🔴 BEAR ({pct:.1f}%)"


if __name__ == "__main__":
    import logging as _logging
    from database.db import init_db

    _logging.basicConfig(level=logging.INFO)
    init_db()

    pct = compute_breadth()
    print(f"breadth_monitor.py: {breadth_label(pct)}")
