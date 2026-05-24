"""
Pocket pivot early entry detection.
A pocket pivot occurs when:
  - The stock closes up on the day
  - Volume is >= the highest DOWN-day volume in the prior 10 sessions
  - Price is above the 10-day SMA
  - Stock is in a Stage 2 uptrend (Trend Template passes)

This provides an earlier entry opportunity before the full VCP breakout.
"""

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def detect_pocket_pivot(
    ticker: str,
    df: pd.DataFrame,
    trend_template_passes: bool,
) -> dict:
    """
    Detect a pocket pivot in the most recent session.

    Returns:
        {
          "ticker": str,
          "pocket_pivot": bool,
          "volume": float,
          "max_down_vol_10d": float,
          "volume_ratio": float,
          "price": float,
          "sma10": float,
        }
    """
    result = {
        "ticker": ticker,
        "pocket_pivot": False,
        "volume": 0.0,
        "max_down_vol_10d": 0.0,
        "volume_ratio": 0.0,
        "price": 0.0,
        "sma10": 0.0,
    }

    if not trend_template_passes:
        return result

    if df is None or len(df) < 15:
        return result

    try:
        close = df["Close"]
        volume = df["Volume"]

        # Today's session
        price_today = float(close.iloc[-1])
        price_prev = float(close.iloc[-2])
        vol_today = float(volume.iloc[-1])

        # Must close up on the day
        if price_today <= price_prev:
            return result

        # Find the highest DOWN-day volume in the prior 10 sessions (excluding today).
        # Use 11 rows so that shift(1) has a valid predecessor for every row we evaluate;
        # without this, the first row after shift produces NaN and that session is
        # never classified as a down day, quietly reducing the window to 9 sessions.
        prior_10 = df.iloc[-12:-1]
        down_days = prior_10[prior_10["Close"] < prior_10["Close"].shift(1)]
        if down_days.empty:
            # No down days — rare, but treat as bullish
            max_down_vol = float(prior_10["Volume"].min())
        else:
            max_down_vol = float(down_days["Volume"].max())

        # Volume today must exceed the highest prior down-day volume
        if vol_today < max_down_vol:
            return result

        # Price must be above 10-day SMA
        sma10 = float(close.rolling(10).mean().iloc[-1])
        if price_today < sma10:
            return result

        result.update({
            "pocket_pivot": True,
            "volume": vol_today,
            "max_down_vol_10d": max_down_vol,
            "volume_ratio": round(vol_today / max_down_vol, 2) if max_down_vol > 0 else 0.0,
            "price": round(price_today, 2),
            "sma10": round(sma10, 2),
        })

    except Exception as exc:
        logger.debug("Pocket pivot error for %s: %s", ticker, exc)

    return result


if __name__ == "__main__":
    from data.fetcher import fetch_ohlcv
    from database.db import init_db
    import logging as _logging

    _logging.basicConfig(level=logging.INFO)
    init_db()

    df = fetch_ohlcv("NVDA", "1y")
    result = detect_pocket_pivot("NVDA", df, trend_template_passes=True)
    print(f"pocket_pivot.py: NVDA pocket_pivot={result['pocket_pivot']}, "
          f"vol_ratio={result['volume_ratio']}")
