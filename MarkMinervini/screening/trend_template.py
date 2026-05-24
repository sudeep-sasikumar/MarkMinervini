"""
Minervini 8-Point Trend Template (Section 5A).
Uses SIMPLE Moving Averages only — never EMA.
All 8 criteria must pass for a stock to qualify for VCP analysis.

Returns a detailed dict with per-criterion results and an overall score (0–8).
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

from config import settings

logger = logging.getLogger(__name__)


def check_trend_template(
    ticker: str,
    df: pd.DataFrame,
    rs_rating: float = 0.0,
) -> dict:
    """
    Evaluate the 8-point Minervini Trend Template for a single ticker.

    Args:
        ticker:    Stock symbol
        df:        OHLCV DataFrame (sorted ascending, >= 252 rows)
        rs_rating: Pre-computed RS percentile rank (0–100)

    Returns:
        {
          "ticker": str,
          "passes": bool,          # True only if ALL 8 pass
          "score": int,            # 0–8
          "details": {
            "c1_price_above_sma50": bool,
            "c2_price_above_sma150": bool,
            "c3_price_above_sma200": bool,
            "c4_sma200_rising": bool,
            "c4_sma200_rising_strong": bool,
            "c5_ma_stack": bool,
            "c6_near_52wk_high": bool,
            "c7_above_52wk_low": bool,
            "c8_rs_rating": bool,
            ...values...,
          }
        }
    """
    result = {
        "ticker": ticker,
        "passes": False,
        "score": 0,
        "details": {},
    }

    if df is None or len(df) < 252:
        result["details"]["error"] = "Insufficient history (< 252 rows)"
        return result

    try:
        close = df["Close"]
        high = df["High"]
        low = df["Low"]

        # --- Compute SMAs ---
        sma50 = close.rolling(50).mean()
        sma150 = close.rolling(150).mean()
        sma200 = close.rolling(200).mean()

        price = float(close.iloc[-1])
        s50   = float(sma50.iloc[-1])
        s150  = float(sma150.iloc[-1])
        s200  = float(sma200.iloc[-1])

        # Guard: any NaN means Close has gaps that broke the rolling window.
        # Return a clean rejection rather than propagating NaN through criterion checks.
        if any(pd.isna(v) for v in (price, s50, s150, s200)):
            result["details"]["error"] = (
                f"NaN in SMA computation for {ticker} — likely data gap in Close series"
            )
            return result

        # --- Criterion 1: price > SMA50 ---
        c1 = price > s50

        # --- Criterion 2: price > SMA150 ---
        c2 = price > s150

        # --- Criterion 3: price > SMA200 ---
        c3 = price > s200

        # --- Criterion 4: SMA200 rising >= 20 trading days ---
        # SMA200 only becomes valid after 200 rows, so iloc[-221] can be NaN.
        # Use pandas .dropna() to safely access the historical SMA200 value.
        sma200_valid = sma200.dropna()
        sma200_now = float(sma200_valid.iloc[-1])

        # Rising over last 20 trading days (required criterion)
        sma200_20d_ago = (
            float(sma200_valid.iloc[-settings.SMA200_RISING_DAYS - 1])
            if len(sma200_valid) >= settings.SMA200_RISING_DAYS + 1
            else float("nan")
        )
        c4 = (not pd.isna(sma200_20d_ago)) and (sma200_now > sma200_20d_ago)

        # Strong flag: rising over last 100 trading days (4–5 months)
        # Requires 200 + 100 = 300 rows of history to be valid
        sma200_strong_ago = (
            float(sma200_valid.iloc[-settings.SMA200_RISING_STRONG_DAYS - 1])
            if len(sma200_valid) >= settings.SMA200_RISING_STRONG_DAYS + 1
            else float("nan")
        )
        c4_strong = (not pd.isna(sma200_strong_ago)) and (sma200_now > sma200_strong_ago)

        # --- Criterion 5: SMA50 > SMA150 > SMA200 (proper stack) ---
        c5 = (s50 > s150) and (s150 > s200)

        # --- Criterion 6: price >= 52-week high * 0.75 (within 25% of high) ---
        high_252 = float(high.rolling(252).max().iloc[-1])
        c6 = price >= high_252 * settings.HIGH_PROXIMITY_THRESHOLD

        # --- Criterion 7: price >= 52-week low * 1.30 (30% above low) ---
        low_252 = float(low.rolling(252).min().iloc[-1])
        c7 = price >= low_252 * settings.LOW_DISTANCE_THRESHOLD

        # --- Criterion 8: RS rating >= 70 ---
        c8 = rs_rating >= settings.RS_MINIMUM

        criteria = [c1, c2, c3, c4, c5, c6, c7, c8]
        score = sum(criteria)
        passes = all(criteria)

        result["passes"] = passes
        result["score"] = score
        result["details"] = {
            "c1_price_above_sma50": c1,
            "c2_price_above_sma150": c2,
            "c3_price_above_sma200": c3,
            "c4_sma200_rising": c4,
            "c4_sma200_rising_strong": c4_strong,
            "c5_ma_stack": c5,
            "c6_near_52wk_high": c6,
            "c7_above_52wk_low": c7,
            "c8_rs_rating": c8,
            # --- Raw values for dashboard display ---
            "price": price,
            "sma50": round(s50, 2),
            "sma150": round(s150, 2),
            "sma200": round(s200, 2),
            "sma200_20d_ago": round(sma200_20d_ago, 2) if not pd.isna(sma200_20d_ago) else None,
            "high_52wk": round(high_252, 2),
            "low_52wk": round(low_252, 2),
            "pct_from_52wk_high": round((price / high_252 - 1) * 100, 1),
            "pct_above_52wk_low": round((price / low_252 - 1) * 100, 1),
            "rs_rating": rs_rating,
        }

    except Exception as exc:
        logger.warning("Trend template error for %s: %s", ticker, exc)
        result["details"]["error"] = str(exc)

    return result


def batch_check_trend_template(
    price_data: dict[str, pd.DataFrame],
    rs_ratings: dict[str, float],
) -> list[dict]:
    """
    Run the Trend Template on every ticker in price_data.
    Returns a list of result dicts, filtered to only passing stocks.
    """
    results = []
    for ticker, df in price_data.items():
        rs = rs_ratings.get(ticker, 0.0)
        res = check_trend_template(ticker, df, rs)
        results.append(res)

    passing = [r for r in results if r["passes"]]
    logger.info(
        "Trend Template: %d/%d passed all 8 criteria",
        len(passing), len(results),
    )
    return passing


if __name__ == "__main__":
    import logging as _logging
    from database.db import init_db
    from data.fetcher import fetch_ohlcv

    _logging.basicConfig(level=logging.INFO)
    init_db()

    df = fetch_ohlcv("AAPL", "2y")
    result = check_trend_template("AAPL", df, rs_rating=85.0)
    print(f"trend_template.py: AAPL passes={result['passes']}, score={result['score']}/8")
    for k, v in result["details"].items():
        print(f"  {k}: {v}")
