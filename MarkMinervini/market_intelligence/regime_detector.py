"""
Market regime detection (Section 7).
Determines BULL / NEUTRAL / BEAR and the aggression_factor applied to all position sizes.
If regime is BEAR: all buy signals suppressed.
"""

import logging
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from config import settings
from data.fetcher import fetch_ohlcv, fetch_vix
from data.cache import get as cache_get, set as cache_set, TTL_1H

logger = logging.getLogger(__name__)


def detect_regime(
    spy_df: Optional[pd.DataFrame] = None,
    qqq_df: Optional[pd.DataFrame] = None,
    breadth_pct: Optional[float] = None,
) -> dict:
    """
    Full market regime assessment.

    Args:
        spy_df:       SPY OHLCV DataFrame (fetched if None)
        qqq_df:       QQQ OHLCV DataFrame (fetched if None)
        breadth_pct:  % of S&P 500 stocks above 200-SMA (from breadth_monitor).
                      If None, read from cache so breadth always contributes.

    Returns regime dict including diagnostic fields for full audit trail.
    """
    cache_key = "regime:latest"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    if spy_df is None:
        spy_df = fetch_ohlcv("SPY", "2y")
    if qqq_df is None:
        qqq_df = fetch_ohlcv("QQQ", "2y")

    # If breadth was not supplied by the caller, read the most-recent cached value
    # computed by compute_breadth() in job_data_refresh.  This ensures the breadth
    # gate never silently fires on a None reading.
    if breadth_pct is None:
        cached_breadth = cache_get("breadth:sp500_above_200sma")
        if cached_breadth is not None:
            breadth_pct = float(cached_breadth)
            logger.debug("Regime: using cached breadth %.1f%%", breadth_pct)

    vix = fetch_vix() or 20.0
    result = _assess_regime(spy_df, qqq_df, vix, breadth_pct)

    cache_set(cache_key, result, ttl_seconds=TTL_1H)
    return result


def _assess_regime(
    spy_df: Optional[pd.DataFrame],
    qqq_df: Optional[pd.DataFrame],
    vix: float,
    breadth_pct: Optional[float],
) -> dict:
    regime = "NEUTRAL"
    signals_allowed = True
    aggression = 1.0
    dist_days = 0
    ftd_confirmed = False
    high_impact_event = False
    issues = []

    # ---- Capture all SPY diagnostics in one pass ----
    spy_close: Optional[float] = None
    spy_sma50: Optional[float] = None
    spy_sma150: Optional[float] = None
    spy_sma200: Optional[float] = None
    spy_above_sma200: Optional[bool] = None
    spy_ma_stack_ok: Optional[bool] = None
    spy_last_date: Optional[str] = None
    bear_gate = False

    if spy_df is not None and len(spy_df) >= 200:
        close = spy_df["Close"]
        spy_sma50  = float(close.rolling(50).mean().iloc[-1])
        spy_sma150 = float(close.rolling(150).mean().iloc[-1])
        spy_sma200 = float(close.rolling(200).mean().iloc[-1])
        spy_close  = float(close.iloc[-1])
        spy_last_date = str(spy_df.index[-1].date())
        spy_above_sma200 = spy_close > spy_sma200
        spy_ma_stack_ok  = (spy_sma50 > spy_sma150 > spy_sma200)

        # Bear gate: price below SMA200
        bear_gate = not spy_above_sma200
        if bear_gate:
            regime = "BEAR"
            signals_allowed = False
            aggression = 0.0
            issues.append("SPY below SMA200")
        elif not spy_ma_stack_ok:
            issues.append("SPY MA stack broken")
            aggression = min(aggression, 0.75)

    # ---- Distribution day counter ----
    # Uses IBD/Minervini definition: close down >= 0.2% on HIGHER volume.
    # Without the 0.2% minimum, any tiny down-tick on higher volume is counted,
    # causing severe over-counting in bull markets (root cause of false BEAR alert).
    if spy_df is not None and len(spy_df) > settings.DISTRIBUTION_LOOKBACK:
        dist_days = _count_distribution_days(
            spy_df.tail(settings.DISTRIBUTION_LOOKBACK + 1)
        )
        if dist_days >= settings.DISTRIBUTION_DAYS_DANGER:
            signals_allowed = False
            aggression = 0.0
            issues.append(f"Distribution days {dist_days} >= danger threshold {settings.DISTRIBUTION_DAYS_DANGER}")
        elif dist_days >= settings.DISTRIBUTION_DAYS_CAUTION:
            aggression = min(aggression, 0.5)
            issues.append(f"Distribution days {dist_days} — caution")

    # ---- Follow-through day tracking ----
    if spy_df is not None and not bear_gate:
        ftd_confirmed = _check_ftd(spy_df)
        if not ftd_confirmed:
            aggression = min(aggression, 0.5)

    # ---- VIX assessment ----
    if vix >= settings.VIX_DANGER:
        signals_allowed = False
        aggression = 0.0
        issues.append(f"VIX {vix:.1f} >= danger {settings.VIX_DANGER}")
    elif vix >= settings.VIX_CAUTION:
        aggression = min(aggression, 0.5)
        issues.append(f"VIX {vix:.1f} — caution")

    # ---- Market breadth ----
    if breadth_pct is not None:
        if breadth_pct < settings.BREADTH_BEAR:
            signals_allowed = False
            aggression = 0.0
            issues.append(f"Breadth {breadth_pct:.1f}% — bear territory")
        elif breadth_pct < settings.BREADTH_WEAK:
            aggression = min(aggression, 0.75)
            issues.append(f"Breadth {breadth_pct:.1f}% — mixed/weak")

    # ---- High-impact economic event gate ----
    # Per master prompt: reduce position sizes to 50% if high-impact event
    # (Fed, CPI, NFP, PCE, GDP) is due within next 2 trading days.
    try:
        from data.economic_calendar import is_high_impact_window, get_high_impact_events
        high_impact_event = is_high_impact_window()
        if high_impact_event:
            event_names = [e["event"] for e in get_high_impact_events(days_ahead=2)]
            aggression = min(aggression, 0.5)
            issues.append(
                f"High-impact macro event imminent: {', '.join(event_names[:2])}"
                f" — sizing reduced to 50%"
            )
    except Exception as _eco_exc:
        logger.debug("Economic calendar check failed: %s", _eco_exc)
        high_impact_event = False

    # ---- Derive final regime label ----
    if not bear_gate:
        if aggression >= 0.9 and not issues:
            regime = "BULL"
        elif aggression <= 0.0 or not signals_allowed:
            regime = "BEAR"
        else:
            regime = "NEUTRAL"

    summary = "; ".join(issues) if issues else "All systems healthy"

    return {
        # --- Core regime fields ---
        "regime": regime,
        "aggression_factor": round(aggression, 2),
        "signals_allowed": signals_allowed,
        "vix_level": vix,
        "breadth_pct": breadth_pct,
        "distribution_days": dist_days,
        "ftd_confirmed": ftd_confirmed,
        "high_impact_event_imminent": high_impact_event,
        "regime_summary": summary,
        # --- Diagnostic / audit-trail fields ---
        "spy_close": round(spy_close, 2) if spy_close is not None else None,
        "spy_sma50": round(spy_sma50, 2) if spy_sma50 is not None else None,
        "spy_sma150": round(spy_sma150, 2) if spy_sma150 is not None else None,
        "spy_sma200": round(spy_sma200, 2) if spy_sma200 is not None else None,
        "spy_above_sma200": spy_above_sma200,
        "spy_ma_stack_ok": spy_ma_stack_ok,
        "spy_last_date": spy_last_date,
        "bear_gate": bear_gate,
    }


def _count_distribution_days(df: pd.DataFrame) -> int:
    """
    Count distribution days in a window.

    IBD / Minervini definition:
        - Index closes DOWN by at least 0.2% from the prior close
        - On HIGHER volume than the prior session

    The 0.2% minimum is critical: without it any micro-tick lower on slightly
    higher volume is counted, producing extreme over-counts in bull markets.
    """
    count = 0
    for i in range(1, len(df)):
        close_today = df["Close"].iloc[i]
        close_prev  = df["Close"].iloc[i - 1]
        vol_today   = df["Volume"].iloc[i]
        vol_prev    = df["Volume"].iloc[i - 1]
        daily_change = close_today / close_prev - 1
        # Must drop >= 0.2% AND on higher volume (IBD/Minervini definition)
        if daily_change <= -settings.DISTRIBUTION_DAY_MIN_DROP and vol_today > vol_prev:
            count += 1
    return count


def _check_ftd(spy_df: pd.DataFrame) -> bool:
    """
    Check if a valid Follow-Through Day has occurred after the most recent correction.
    Returns True if FTD confirmed (or no correction detected — market is healthy).

    Fix: correction trough is now the LOWEST CLOSE during the in-correction window,
    not the LAST day that was flagged as in-correction (which is always a recovery day).
    """
    close = spy_df["Close"]
    volume = spy_df["Volume"]

    # Find most recent 5% correction
    rolling_high = close.rolling(20).max()
    drawdown = (close / rolling_high) - 1
    in_correction = (drawdown < -settings.SPY_DROP_CORRECTION)

    if not in_correction.iloc[-60:].any():
        # No recent correction — assume healthy market, FTD not needed
        return True

    recent_in_corr = in_correction.iloc[-60:]
    if not recent_in_corr.any():
        return True

    # Find the correction TROUGH: the date with the lowest close while in correction.
    # Previously used index[-1] which picks the last in-correction day (not the low).
    correction_dates = recent_in_corr[recent_in_corr].index
    correction_trough_date = close[correction_dates].idxmin()
    corr_idx = spy_df.index.get_loc(correction_trough_date)

    # Look for FTD: day 4+ from trough with gain >= 1.7% on higher volume
    sub = spy_df.iloc[corr_idx:]
    for i in range(3, len(sub)):
        daily_gain = (sub["Close"].iloc[i] / sub["Close"].iloc[i - 1]) - 1
        vol_higher = sub["Volume"].iloc[i] > sub["Volume"].iloc[i - 1]
        if daily_gain >= settings.FTD_MIN_GAIN and vol_higher:
            return True

    return False


if __name__ == "__main__":
    import logging as _logging
    from database.db import init_db

    _logging.basicConfig(level=logging.INFO)
    init_db()

    regime = detect_regime()
    print(f"regime_detector.py: regime={regime['regime']}, "
          f"aggression={regime['aggression_factor']}, vix={regime['vix_level']}")
    print(f"  summary: {regime['regime_summary']}")
    print(f"  SPY: close={regime['spy_close']}, SMA200={regime['spy_sma200']}, "
          f"above_200={regime['spy_above_sma200']}, date={regime['spy_last_date']}")
    print(f"  distribution_days={regime['distribution_days']}, "
          f"bear_gate={regime['bear_gate']}")
