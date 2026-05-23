"""
Relative Strength (RS) rank calculator — Minervini-style, NOT RSI.

RS = percentile rank of 12-month price performance across the entire universe.
rs_raw  = (adjusted_close_today / adjusted_close_252_days_ago) - 1
rs_rating = (rank / total) * 100   (higher = stronger)

RS Comparative Line = stock_close / SPY_close
Flags "RS LINE NEW HIGH" when the RS line hits a 52-week high while price has not.

Vectorised pandas operations — no per-ticker loops.
Target: < 30 seconds for 1,500 tickers.
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

from data.fetcher import fetch_ohlcv_batch, fetch_spy_ohlcv

logger = logging.getLogger(__name__)


def compute_rs_ratings(
    price_data: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """
    Compute RS ratings for all tickers in price_data.

    Args:
        price_data: {ticker: OHLCV DataFrame} from fetch_ohlcv_batch

    Returns:
        DataFrame with columns [ticker, rs_raw, rs_rating]
        sorted descending by rs_rating.
    """
    records = []
    for ticker, df in price_data.items():
        if len(df) < 252:
            continue
        try:
            close = df["Close"]
            price_now = float(close.iloc[-1])
            price_252 = float(close.iloc[-252])
            if price_252 == 0:
                continue
            rs_raw = (price_now / price_252) - 1.0
            records.append({"ticker": ticker, "rs_raw": rs_raw})
        except Exception as exc:
            logger.debug("RS calc failed for %s: %s", ticker, exc)

    if not records:
        return pd.DataFrame(columns=["ticker", "rs_raw", "rs_rating"])

    df_rs = pd.DataFrame(records)
    df_rs.sort_values("rs_raw", ascending=False, inplace=True)
    df_rs.reset_index(drop=True, inplace=True)
    total = len(df_rs)
    # Rank: position 0 = highest rs_raw = rs_rating 100
    df_rs["rs_rating"] = ((total - df_rs.index) / total * 100).round(1)

    logger.info("RS computed for %d tickers (threshold ≥%d: %d stocks)",
                total,
                70,
                (df_rs["rs_rating"] >= 70).sum())
    return df_rs


def compute_rs_line(
    stock_df: pd.DataFrame,
    spy_df: pd.DataFrame,
) -> pd.Series:
    """
    Return the RS comparative line: stock_close / spy_close.
    Both DataFrames must have a 'Close' column.
    Index is aligned by date (inner join).
    """
    stock_close = stock_df["Close"].rename("stock")
    spy_close = spy_df["Close"].rename("spy")
    aligned = pd.concat([stock_close, spy_close], axis=1).dropna()
    rs_line = aligned["stock"] / aligned["spy"]
    return rs_line


def check_rs_line_new_high(
    stock_df: pd.DataFrame,
    spy_df: pd.DataFrame,
    lookback_days: int = 252,
    recent_days: int = 5,
) -> bool:
    """
    Return True if the RS line made a new 52-week high in the last `recent_days`
    while the stock price itself did NOT make a new 52-week high.
    This is Minervini's most powerful early signal.
    """
    try:
        rs_line = compute_rs_line(stock_df, spy_df)
        if len(rs_line) < lookback_days + recent_days:
            return False

        rs_window = rs_line.iloc[-lookback_days:]
        rs_recent = rs_line.iloc[-recent_days:]
        rs_line_new_high = float(rs_recent.max()) >= float(rs_window.max())

        price_window = stock_df["Close"].iloc[-lookback_days:]
        price_recent = stock_df["Close"].iloc[-recent_days:]
        price_new_high = float(price_recent.max()) >= float(price_window.max())

        return rs_line_new_high and not price_new_high
    except Exception as exc:
        logger.debug("RS line new high check failed: %s", exc)
        return False


def get_rs_rating_for_ticker(
    ticker: str,
    all_rs_df: pd.DataFrame,
) -> Optional[float]:
    """Look up the RS rating for a single ticker from the precomputed universe table."""
    row = all_rs_df[all_rs_df["ticker"] == ticker]
    if row.empty:
        return None
    return float(row["rs_rating"].iloc[0])


if __name__ == "__main__":
    import logging as _logging
    from database.db import init_db
    from screening.universe import get_sp500_tickers

    _logging.basicConfig(level=logging.INFO)
    init_db()

    # Test on a small sample of 50 tickers
    sample = get_sp500_tickers()[:50]
    price_data = fetch_ohlcv_batch(sample, period="2y")
    rs_df = compute_rs_ratings(price_data)
    print(f"rs_calculator.py: computed RS for {len(rs_df)} tickers")
    print(rs_df.head(10).to_string(index=False))
