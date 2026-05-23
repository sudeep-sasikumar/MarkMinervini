"""
Price and OHLCV data fetcher.
Primary: yfinance (no API key, free).
Fallback: Finnhub OHLCV endpoint (rate-limited token bucket, 55 calls/min).
"""

import logging
import time
import threading
from typing import Optional

import pandas as pd
import yfinance as yf

from config import settings
from data.cache import get as cache_get, set as cache_set, TTL_1D

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Finnhub rate-limit token bucket (55 tokens/60s)
# ---------------------------------------------------------------------------
class _TokenBucket:
    """Thread-safe token bucket for rate limiting."""

    def __init__(self, capacity: int, refill_period_s: float):
        self._capacity = capacity
        self._tokens = float(capacity)
        self._refill_period = refill_period_s
        self._refill_rate = capacity / refill_period_s  # tokens per second
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(
                    self._capacity,
                    self._tokens + elapsed * self._refill_rate,
                )
                self._last_refill = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return True
            time.sleep(0.5)
        return False


_finnhub_bucket = _TokenBucket(
    capacity=settings.FINNHUB_MAX_CALLS_PER_MIN,
    refill_period_s=60.0,
)


def _finnhub_request(endpoint: str, params: dict) -> Optional[dict]:
    """Make a rate-limited Finnhub API call."""
    if not settings.FINNHUB_API_KEY:
        return None
    if not _finnhub_bucket.acquire():
        logger.warning("Finnhub rate-limit bucket timed out for %s", endpoint)
        return None
    try:
        import requests

        url = f"https://finnhub.io/api/v1{endpoint}"
        params["token"] = settings.FINNHUB_API_KEY
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Finnhub request failed (%s): %s", endpoint, exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_ohlcv(ticker: str, period: str = "2y") -> Optional[pd.DataFrame]:
    """
    Fetch daily OHLCV for a ticker.
    Returns a DataFrame with columns [Open, High, Low, Close, Volume, Adj Close]
    indexed by date, sorted ascending. Returns None if insufficient data.

    Tries yfinance first; falls back to Finnhub candles on failure.
    Results are cached for 1 day.
    """
    cache_key = f"ohlcv:{ticker}:{period}"
    cached = cache_get(cache_key)
    if cached is not None:
        try:
            df = pd.read_json(cached, orient="split")
            df.index = pd.to_datetime(df.index)
            return df
        except Exception:
            pass  # stale/corrupt cache — refetch

    df = _fetch_yfinance(ticker, period)
    if df is None or len(df) < 200:
        logger.debug("yfinance insufficient for %s (rows=%s), trying Finnhub",
                     ticker, len(df) if df is not None else 0)
        df = _fetch_finnhub_ohlcv(ticker)

    if df is None or len(df) < 200:
        logger.warning("Skipping %s — fewer than 200 rows of history", ticker)
        return None

    # Store in cache as JSON
    try:
        cache_set(cache_key, df.to_json(orient="split", date_format="iso"), TTL_1D)
    except Exception as exc:
        logger.debug("Cache write failed for %s: %s", ticker, exc)

    return df


def fetch_ohlcv_batch(tickers: list[str], period: str = "2y") -> dict[str, pd.DataFrame]:
    """
    Fetch OHLCV for a list of tickers in a single yfinance bulk download.
    Much faster than calling fetch_ohlcv() in a loop for large universes.
    Falls back to per-ticker fetch for any tickers missing from the bulk download.
    Returns {ticker: DataFrame} for tickers with >= 200 rows.
    """
    logger.info("Bulk downloading OHLCV for %d tickers", len(tickers))
    result: dict[str, pd.DataFrame] = {}
    try:
        raw = yf.download(
            tickers,
            period=period,
            auto_adjust=True,
            progress=False,
            threads=True,
            group_by="ticker",
        )
    except Exception as exc:
        logger.error("Bulk yfinance download failed: %s", exc)
        return result

    for ticker in tickers:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                df = raw[ticker].dropna(how="all")
            else:
                # Single ticker returned as flat frame
                df = raw.dropna(how="all")
            if len(df) >= 200:
                result[ticker] = df.sort_index()
        except Exception as exc:
            logger.debug("Failed to extract %s from bulk download: %s", ticker, exc)

    logger.info("Bulk download complete: %d/%d tickers with sufficient history",
                len(result), len(tickers))

    # Fallback: retry missing tickers individually via single-ticker fetch
    missing = [t for t in tickers if t not in result]
    if missing:
        logger.info("Retrying %d missing tickers individually", len(missing))
        for ticker in missing:
            try:
                df = fetch_ohlcv(ticker, period)
                if df is not None and len(df) >= 200:
                    result[ticker] = df
            except Exception as exc:
                logger.debug("Single-ticker fallback failed for %s: %s", ticker, exc)

    return result


def fetch_latest_price(ticker: str) -> Optional[float]:
    """Return the most recent closing price for a ticker (no cache)."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as exc:
        logger.warning("fetch_latest_price failed for %s: %s", ticker, exc)
        return None


def fetch_spy_ohlcv(period: str = "2y") -> Optional[pd.DataFrame]:
    """Convenience: fetch SPY for regime and RS calculations."""
    return fetch_ohlcv("SPY", period)


def fetch_intraday_ohlcv(ticker: str, interval: str = "5m") -> Optional[pd.DataFrame]:
    """
    Fetch intraday OHLCV data for a ticker (today's session).
    Uses yfinance with a 1-day period and 5-minute intervals.
    Returns DataFrame or None. No caching — always fresh for intraday use.
    """
    try:
        t = yf.Ticker(ticker)
        df = t.history(period="1d", interval=interval, auto_adjust=True)
        if df.empty:
            return None
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.sort_index(inplace=True)
        return df
    except Exception as exc:
        logger.debug("fetch_intraday_ohlcv failed for %s: %s", ticker, exc)
        return None


def fetch_gbpusd() -> float:
    """
    Fetch current GBP/USD FX rate via yfinance.
    Returns the rate (e.g. 1.27 means £1 = $1.27).
    Falls back to 1.27 if unavailable (reasonable approximation).
    Cached for 1 hour.
    """
    cache_key = "fx:gbpusd"
    cached = cache_get(cache_key)
    if cached is not None:
        return float(cached)
    try:
        gbpusd = yf.Ticker("GBPUSD=X")
        hist = gbpusd.history(period="5d")
        if hist.empty:
            raise ValueError("Empty GBPUSD history")
        rate = float(hist["Close"].iloc[-1])
        cache_set(cache_key, rate, ttl_seconds=3600)
        return rate
    except Exception as exc:
        logger.warning("fetch_gbpusd failed (%s) — using fallback rate 1.27", exc)
        return 1.27  # reasonable fallback; avoids hard crash


def fetch_vix() -> Optional[float]:
    """Fetch latest VIX level via yfinance."""
    cache_key = "vix:latest"
    cached = cache_get(cache_key)
    if cached is not None:
        return float(cached)
    try:
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="5d")
        if hist.empty:
            return None
        level = float(hist["Close"].iloc[-1])
        cache_set(cache_key, level, ttl_seconds=3600)
        return level
    except Exception as exc:
        logger.warning("fetch_vix failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _fetch_yfinance(ticker: str, period: str) -> Optional[pd.DataFrame]:
    try:
        t = yf.Ticker(ticker)
        df = t.history(period=period, auto_adjust=True)
        if df.empty:
            return None
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.sort_index(inplace=True)
        return df
    except Exception as exc:
        logger.debug("yfinance failed for %s: %s", ticker, exc)
        return None


def _fetch_finnhub_ohlcv(ticker: str) -> Optional[pd.DataFrame]:
    """Fetch 2 years of daily candles from Finnhub as fallback."""
    import time as _time

    to_ts = int(_time.time())
    from_ts = to_ts - 2 * 365 * 24 * 3600

    data = _finnhub_request(
        "/stock/candle",
        {"symbol": ticker, "resolution": "D", "from": from_ts, "to": to_ts},
    )
    if data is None or data.get("s") != "ok":
        return None
    try:
        df = pd.DataFrame({
            "Open": data["o"],
            "High": data["h"],
            "Low": data["l"],
            "Close": data["c"],
            "Volume": data["v"],
        }, index=pd.to_datetime(data["t"], unit="s"))
        df.index = df.index.tz_localize(None)
        df.sort_index(inplace=True)
        return df
    except Exception as exc:
        logger.debug("Finnhub candle parse failed for %s: %s", ticker, exc)
        return None


if __name__ == "__main__":
    from database.db import init_db
    logging.basicConfig(level=logging.INFO)
    init_db()
    df = fetch_ohlcv("AAPL", "2y")
    if df is not None:
        print(f"fetcher.py: AAPL OK — {len(df)} rows")
        print(df.tail(3))
    else:
        print("fetcher.py: FAILED — check network / yfinance version")
