"""
Universe management: S&P 500 + Russell 1000 ticker list.
Fetched from Wikipedia / iShares; cached locally.
Deduplication and validation included.
"""

import logging
from typing import Optional

import pandas as pd
import requests

from data.cache import get as cache_get, set as cache_set, TTL_1D

logger = logging.getLogger(__name__)

# iShares IWB (Russell 1000) holdings CSV URL — public, no auth required
_IWB_CSV_URL = (
    "https://www.ishares.com/us/products/239707/ISHARES-RUSSELL-1000-ETF/1467271812596"
    ".ajax?fileType=csv&fileName=IWB_holdings&dataType=fund"
)

# Wikipedia S&P 500 table
_SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def get_sp500_tickers() -> list[str]:
    """Scrape S&P 500 constituent tickers from Wikipedia."""
    cache_key = "universe:sp500"
    cached = cache_get(cache_key)
    if cached:
        return cached
    try:
        tables = pd.read_html(_SP500_WIKI_URL)
        df = tables[0]
        tickers = df["Symbol"].str.replace(".", "-", regex=False).tolist()
        tickers = [t.strip() for t in tickers if isinstance(t, str)]
        logger.info("S&P 500: %d tickers loaded", len(tickers))
        cache_set(cache_key, tickers, ttl_seconds=TTL_1D)
        return tickers
    except Exception as exc:
        logger.warning("S&P 500 fetch failed: %s — using fallback list", exc)
        return _sp500_fallback()


def get_russell1000_tickers() -> list[str]:
    """
    Attempt to fetch Russell 1000 from iShares holdings CSV.
    Falls back to S&P 500 only on failure.
    """
    cache_key = "universe:russell1000"
    cached = cache_get(cache_key)
    if cached:
        return cached
    try:
        resp = requests.get(_IWB_CSV_URL, timeout=15,
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        lines = resp.text.splitlines()
        # iShares CSV has metadata rows at top — skip until header row
        start_idx = 0
        for i, line in enumerate(lines):
            if "Ticker" in line or "Name" in line:
                start_idx = i
                break
        from io import StringIO
        df = pd.read_csv(StringIO("\n".join(lines[start_idx:])))
        col = next((c for c in df.columns if "ticker" in c.lower()), None)
        if col is None:
            raise ValueError("Ticker column not found in IWB CSV")
        tickers = df[col].dropna().str.strip().tolist()
        tickers = [t for t in tickers if t and t != "-" and len(t) <= 5]
        logger.info("Russell 1000: %d tickers loaded", len(tickers))
        cache_set(cache_key, tickers, ttl_seconds=TTL_1D)
        return tickers
    except Exception as exc:
        logger.warning("Russell 1000 fetch failed: %s — falling back to S&P 500", exc)
        return []


def get_universe() -> list[str]:
    """
    Return deduplicated union of S&P 500 + Russell 1000 tickers.
    Target size: ~1,500 unique US-listed equities.
    """
    sp500 = get_sp500_tickers()
    russell = get_russell1000_tickers()
    combined = list(dict.fromkeys(sp500 + russell))  # preserve order, deduplicate
    logger.info("Universe: %d unique tickers (S&P500=%d, Russell1000=%d)",
                len(combined), len(sp500), len(russell))
    return combined


def _sp500_fallback() -> list[str]:
    """Minimal hardcoded S&P 500 sample used only when Wikipedia is unreachable."""
    return [
        "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "BRK-B",
        "LLY", "AVGO", "JPM", "TSLA", "UNH", "V", "XOM", "MA", "PG", "HD",
        "COST", "JNJ", "ABBV", "MRK", "CVX", "CRM", "BAC", "NFLX", "AMD",
        "ACN", "WMT", "LIN", "TMO", "CSCO", "MCD", "ABT", "ADBE", "ORCL",
        "TXN", "DHR", "PM", "INTU", "GE", "CAT", "AMGN", "ISRG", "BKNG",
        "NOW", "AXP", "SPGI", "VRTX", "RTX", "HON", "GILD", "T", "PLD",
        "BLK", "LOW", "SYK", "PANW", "AMAT", "MDT", "CI", "ADI", "DE",
        "PH", "REGN", "ETN", "KLAC", "ELV", "SCHW", "MU", "LRCX", "ZTS",
    ]


if __name__ == "__main__":
    from database.db import init_db
    import logging as _logging
    _logging.basicConfig(level=logging.INFO)
    init_db()
    u = get_universe()
    print(f"universe.py: {len(u)} tickers in universe")
    print(f"  First 10: {u[:10]}")
