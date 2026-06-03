"""
Relative Strength (RS) rank calculator — Minervini/IBD-style, NOT RSI.

RS uses the IBD-weighted 12-month formula (NOT a simple 12-month return):
    rs_raw = 0.40 × Q4_return + 0.20 × Q3_return + 0.20 × Q2_return + 0.20 × Q1_return

Where each quarter covers ~63 trading days:
    Q4 (most recent):  close[-1]   / close[-63]  - 1
    Q3:                close[-63]  / close[-126] - 1
    Q2:                close[-126] / close[-189] - 1
    Q1 (oldest):       close[-189] / close[-252] - 1

Weighting Q4 at 40% means stocks that surged recently rank higher than those
whose gains are all in the distant past — exactly what Minervini looks for.
A simple 12-month return is identical to Q1_through_Q4 equally weighted (25%
each), which understates the importance of recent momentum.

rs_rating = percentile rank in [0, 100] across the universe (100 = leader).

RS Comparative Line = stock_close / SPY_close
Flags "RS LINE NEW HIGH" when the RS line hits a 52-week high while price has not.

Vectorised via pd.concat — no per-ticker loops.
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
    Compute RS ratings for all tickers in price_data using vectorised operations.

    Uses the IBD/Minervini weighted quarterly formula:
        rs_raw = 0.40*Q4 + 0.20*Q3 + 0.20*Q2 + 0.20*Q1
    where Q4 is the most recent quarter (highest weight = recent momentum matters most).

    Requires at least 252 trading days of data per ticker.

    Args:
        price_data: {ticker: OHLCV DataFrame} from fetch_ohlcv_batch

    Returns:
        DataFrame with columns [ticker, rs_raw, rs_rating]
        sorted descending by rs_rating.
    """
    # Build a single wide DataFrame of adjusted closes
    eligible = {t: df["Close"] for t, df in price_data.items() if len(df) >= 252}
    if not eligible:
        logger.warning(
            "RS: 0/%d tickers have ≥252 rows — RS computation skipped. "
            "All tickers have insufficient history for IBD quarterly formula. "
            "Check that price_data was fetched with period≥'2y'.",
            len(price_data),
        )
        return pd.DataFrame(columns=["ticker", "rs_raw", "rs_rating"])
    elif len(eligible) < len(price_data) * 0.5:
        logger.warning(
            "RS: only %d/%d tickers have ≥252 rows — RS rankings may be skewed.",
            len(eligible), len(price_data),
        )

    closes = pd.concat(eligible, axis=1)

    # Drop rows that are entirely NaN across ALL columns.
    #
    # Root cause (confirmed in India scan logs 2026-06-03):
    #   pd.concat with axis=1 uses outer join — the UNION of dates from all
    #   tickers.  NSE stocks occasionally have phantom extra dates (trading
    #   suspensions, yfinance data artifacts) where only 1 stock has a row
    #   and all others are NaN.  These phantom rows scatter through the
    #   combined DataFrame.  When closes.iloc[-252] (integer-position anchor)
    #   lands on such a row, the valid_mask check fails for EVERY ticker,
    #   returning an empty RS DataFrame and silently breaking India TT.
    #
    # dropna(how='all') removes only rows that are entirely NaN, preserving
    # rows where at least one ticker has real data.  This ensures iloc[-252]
    # always lands on an actual trading day.
    closes = closes.dropna(how='all')

    if len(closes) < 252:
        logger.warning(
            "RS: combined price DataFrame has only %d rows after removing phantom "
            "all-NaN rows (need ≥252 for quarterly anchors). "
            "Reduce universe size or fetch more history.",
            len(closes),
        )
        return pd.DataFrame(columns=["ticker", "rs_raw", "rs_rating"])

    # Quarterly boundary prices (vectorised — one row access each)
    p0   = closes.iloc[-1]    # today
    p63  = closes.iloc[-63]   # ~3 months ago
    p126 = closes.iloc[-126]  # ~6 months ago
    p189 = closes.iloc[-189]  # ~9 months ago
    p252 = closes.iloc[-252]  # ~12 months ago

    # Require all five price points to be valid and > 0
    valid_mask = (
        p0.notna() & p63.notna() & p126.notna() & p189.notna() & p252.notna() &
        (p63 > 0) & (p126 > 0) & (p189 > 0) & (p252 > 0)
    )

    # Quarterly returns
    q4 = (p0[valid_mask]   / p63[valid_mask])  - 1.0   # most recent quarter
    q3 = (p63[valid_mask]  / p126[valid_mask]) - 1.0
    q2 = (p126[valid_mask] / p189[valid_mask]) - 1.0
    q1 = (p189[valid_mask] / p252[valid_mask]) - 1.0   # oldest quarter

    # IBD-weighted composite: Q4 gets double weight to reward recent momentum
    rs_raw = 0.40 * q4 + 0.20 * q3 + 0.20 * q2 + 0.20 * q1

    if rs_raw.empty:
        logger.warning(
            "RS: all %d eligible tickers failed the valid_mask check "
            "(NaN or zero price at one of the 5 quarterly anchor points). "
            "Check date alignment in price_data — possibly a timezone or "
            "holiday mismatch causing iloc[-252] to land on a NaN row.",
            len(eligible),
        )
        return pd.DataFrame(columns=["ticker", "rs_raw", "rs_rating"])

    df_rs = rs_raw.reset_index()
    df_rs.columns = ["ticker", "rs_raw"]
    df_rs.sort_values("rs_raw", ascending=False, inplace=True)
    df_rs.reset_index(drop=True, inplace=True)

    total = len(df_rs)
    # Rank: position 0 = strongest = rs_rating 100; last = weakest ≈ 0
    df_rs["rs_rating"] = ((total - df_rs.index) / total * 100).round(1)

    logger.info("RS computed for %d tickers (threshold ≥70: %d stocks)",
                total, (df_rs["rs_rating"] >= 70).sum())
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
    This is Minervini's most powerful early-entry signal.

    Fix: only requires len(rs_line) >= lookback_days (not lookback + recent).
    The recent_days window is already within the lookback_days window.
    """
    try:
        rs_line = compute_rs_line(stock_df, spy_df)
        # Require enough history for the 52-week lookback
        if len(rs_line) < lookback_days:
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
