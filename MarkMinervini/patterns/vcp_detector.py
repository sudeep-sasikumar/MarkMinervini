"""
Volatility Contraction Pattern (VCP) detector — Section 6.
Produces a score 0–100. Only scores >= 80 generate alerts.
Every step is commented per the master prompt specification.

Algorithm (12 steps):
  1.  Confirm Stage 2 uptrend (Trend Template must pass)
  2.  Confirm prior advance >= 30%
  3.  Find the base (60–120 trading days back from today)
  4.  Identify contractions using a 5-day smoothing window
  5.  Validate contraction series (tightening, min 2 contractions)
  6.  Validate volume pattern (declining volume across contractions)
  7.  Evaluate pivot zone (last 5–15 days, ATR collapse)
  8.  Volume dry-up in final 5 days
  9.  No wide-and-loose bars in pivot zone
  10. Compute entry, stop, targets
  11. Gap-up filter
  12. Breakout check (today's session)
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

from config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# VCP grading
# ---------------------------------------------------------------------------
GRADE_ELITE = "ELITE VCP"
GRADE_HIGH = "HIGH QUALITY VCP"
GRADE_MODERATE = "MODERATE VCP"
GRADE_NONE = "NOT A VCP"


def grade_vcp(score: int) -> str:
    if score >= 90:
        return GRADE_ELITE
    if score >= 80:
        return GRADE_HIGH
    if score >= 70:
        return GRADE_MODERATE
    return GRADE_NONE


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def detect_vcp(
    ticker: str,
    df: pd.DataFrame,
    trend_template_passes: bool,
    rs_line_new_high: bool = False,
) -> dict:
    """
    Run the full VCP detection algorithm.

    Args:
        ticker:                  Stock symbol
        df:                      OHLCV DataFrame, sorted ascending, >= 60 rows
        trend_template_passes:   Must be True to proceed (Step 1)
        rs_line_new_high:        RS line recently hit 52-week high (bonus scoring)

    Returns:
        {
          "ticker": str,
          "vcp_score": int,          # 0–100
          "grade": str,
          "alert": bool,             # True if score >= 80 AND all gates pass
          "risk_valid": bool,        # False if stop > 8%
          "contractions": list,      # [{depth_pct, vol_avg}, ...]
          "pivot_price": float,
          "entry_price": float,
          "stop_price": float,
          "stop_pct": float,
          "target_1": float,
          "target_2": float,
          "base_days": int,
          "breakout_confirmed": bool,
          "rejection_reason": str | None,
          "steps": dict,             # per-step details for dashboard
        }
    """
    base_result: dict = {
        "ticker": ticker,
        "vcp_score": 0,
        "grade": GRADE_NONE,
        "alert": False,
        "risk_valid": False,
        "contractions": [],
        "pivot_price": None,
        "entry_price": None,
        "stop_price": None,
        "stop_pct": None,
        "target_1": None,
        "target_2": None,
        "base_days": 0,
        "breakout_confirmed": False,
        "rejection_reason": None,
        "steps": {},
    }

    if df is None or len(df) < 60:
        base_result["rejection_reason"] = "Insufficient history for VCP"
        return base_result

    # ------------------------------------------------------------------
    # Step 1 — Trend Template must pass
    # ------------------------------------------------------------------
    if not trend_template_passes:
        base_result["rejection_reason"] = "Step 1: Trend Template failed"
        return base_result

    score = 0
    steps = {}

    # ------------------------------------------------------------------
    # Step 2 — Prior advance >= 30%
    # ------------------------------------------------------------------
    lookback = min(settings.MAX_BASE_TRADING_DAYS, len(df) - 10)
    window = df.iloc[-(lookback + 60):].copy() if len(df) > lookback + 60 else df.copy()
    pre_base_low = float(window["Low"].iloc[:lookback // 2].min())
    peak_before_base = float(window["High"].iloc[:lookback].max())

    if pre_base_low == 0:
        base_result["rejection_reason"] = "Step 2: Price data error"
        return base_result

    prior_advance_pct = (peak_before_base / pre_base_low - 1) * 100
    steps["prior_advance_pct"] = round(prior_advance_pct, 1)

    if prior_advance_pct < settings.MIN_PRIOR_ADVANCE:
        base_result["rejection_reason"] = (
            f"Step 2: Prior advance {prior_advance_pct:.1f}% < {settings.MIN_PRIOR_ADVANCE}%"
        )
        base_result["steps"] = steps
        return base_result

    # ------------------------------------------------------------------
    # Step 3 — Find the base (highest close in last 60–120 days = peak)
    # ------------------------------------------------------------------
    base_window = df.iloc[-settings.MAX_BASE_TRADING_DAYS:].copy()
    peak_idx = int(base_window["Close"].idxmax().to_pydatetime().timestamp()
                   if hasattr(base_window["Close"].idxmax(), 'to_pydatetime')
                   else 0)
    # Use positional index instead of timestamp for robustness
    peak_pos = int(base_window["Close"].values.argmax())
    base_df = base_window.iloc[peak_pos:].copy()
    base_days = len(base_df)
    steps["base_days"] = base_days
    base_result["base_days"] = base_days

    if base_days < settings.MIN_BASE_WEEKS * 5:
        base_result["rejection_reason"] = (
            f"Step 3: Base only {base_days} days (minimum {settings.MIN_BASE_WEEKS * 5})"
        )
        base_result["steps"] = steps
        return base_result

    # ------------------------------------------------------------------
    # Step 4 — Identify contractions using 5-day smoothing
    # ------------------------------------------------------------------
    contractions = _identify_contractions(base_df)
    steps["num_contractions"] = len(contractions)
    steps["contractions"] = contractions

    # ------------------------------------------------------------------
    # Step 5 — Validate contraction series
    # ------------------------------------------------------------------
    if len(contractions) < settings.MIN_CONTRACTIONS:
        base_result["rejection_reason"] = (
            f"Step 5: Only {len(contractions)} contraction(s); minimum {settings.MIN_CONTRACTIONS}"
        )
        base_result["steps"] = steps
        return base_result

    # Each contraction must be < previous * 0.85
    valid_series = True
    for i in range(1, len(contractions)):
        if contractions[i]["depth_pct"] >= contractions[i - 1]["depth_pct"]:
            # Hard disqualifier: wider than previous
            base_result["rejection_reason"] = (
                f"Step 5: Contraction {i+1} wider than {i} — not a VCP"
            )
            base_result["steps"] = steps
            return base_result
        if contractions[i]["depth_pct"] > contractions[i - 1]["depth_pct"] * settings.CONTRACTION_TIGHTENING_RATIO:
            valid_series = False  # not tight enough, but not disqualifier

    if len(contractions) >= 3:
        score += 25
    score += 10 if len(contractions) >= 4 else 0
    steps["step5_valid"] = valid_series

    # ------------------------------------------------------------------
    # Step 6 — Volume decline across contractions
    # ------------------------------------------------------------------
    vol_declining = _check_volume_declining(contractions)
    if vol_declining == "all":
        score += 25
    elif vol_declining == "most":
        score += 10
    steps["volume_declining"] = vol_declining

    # ------------------------------------------------------------------
    # Step 7 — Pivot zone: ATR collapse
    # ------------------------------------------------------------------
    pivot_days = min(settings.PIVOT_ZONE_DAYS, len(base_df))
    pivot_zone = base_df.iloc[-pivot_days:]

    atr14 = _compute_atr(base_df, 14)
    atr50 = _compute_atr(base_df, min(50, len(base_df)))
    atr_pivot = _compute_atr(pivot_zone, min(14, len(pivot_zone)))

    atr_ratio = atr_pivot / atr50 if atr50 > 0 else 1.0
    steps["atr_ratio"] = round(atr_ratio, 3)

    if atr_ratio < settings.PIVOT_ATR_VERY_TIGHT_RATIO:
        score += 25 + 10  # tight + bonus
    elif atr_ratio < settings.PIVOT_ATR_TIGHT_RATIO:
        score += 25
    steps["step7_atr_tight"] = atr_ratio < settings.PIVOT_ATR_TIGHT_RATIO

    # ------------------------------------------------------------------
    # Step 8 — Volume dry-up in final 5 days
    # ------------------------------------------------------------------
    final5 = base_df.iloc[-settings.VOLUME_DRY_UP_DAYS:]
    avg_vol_50 = float(df["Volume"].iloc[-50:].mean())
    dry_up_days = int((final5["Volume"] < avg_vol_50).sum())
    steps["volume_dry_up_days"] = dry_up_days
    if dry_up_days < 3:
        score -= 10

    # ------------------------------------------------------------------
    # Step 9 — No wide-and-loose bars in pivot zone (last 5 days)
    # ------------------------------------------------------------------
    last5 = base_df.iloc[-5:]
    daily_ranges = ((last5["High"] - last5["Low"]) / last5["Low"]).values
    wide_bars = int((daily_ranges > settings.WIDE_LOOSE_BAR_PCT).sum())
    steps["wide_loose_bars"] = wide_bars
    if wide_bars > 0:
        score -= 15

    # RS line bonus
    if rs_line_new_high:
        score += settings.RS_LINE_HIGH_BONUS
        steps["rs_line_new_high"] = True

    score = max(0, score)
    steps["score_before_gates"] = score

    # ------------------------------------------------------------------
    # Step 10 — Entry, stop, targets
    # ------------------------------------------------------------------
    pivot_price = float(pivot_zone["High"].max())
    entry_price = pivot_price + settings.ENTRY_ABOVE_PIVOT
    stop_price = float(pivot_zone["Low"].min()) * 0.995
    stop_pct = (entry_price - stop_price) / entry_price * 100

    target_1 = entry_price * (1 + (stop_pct / 100) * 2)  # 2R
    target_2 = entry_price * (1 + (stop_pct / 100) * 3)  # 3R

    base_result.update({
        "pivot_price": round(pivot_price, 2),
        "entry_price": round(entry_price, 2),
        "stop_price": round(stop_price, 2),
        "stop_pct": round(stop_pct, 2),
        "target_1": round(target_1, 2),
        "target_2": round(target_2, 2),
    })
    steps["stop_pct"] = round(stop_pct, 2)

    risk_valid = stop_pct <= settings.MAX_STOP_PCT * 100
    base_result["risk_valid"] = risk_valid
    if not risk_valid:
        base_result["rejection_reason"] = (
            f"Step 10: Stop too wide at {stop_pct:.1f}% (max {settings.MAX_STOP_PCT*100:.0f}%)"
        )
        base_result["vcp_score"] = score
        base_result["grade"] = grade_vcp(score)
        base_result["contractions"] = contractions
        base_result["steps"] = steps
        return base_result

    # ------------------------------------------------------------------
    # Step 11 — Gap-up filter
    # ------------------------------------------------------------------
    today_open = float(df["Open"].iloc[-1])
    gap_up = today_open > pivot_price * (1 + settings.GAP_UP_MAX)
    steps["gap_up"] = gap_up
    if gap_up:
        base_result["rejection_reason"] = (
            "Step 11: Gap-up entry — risk/reward compromised (MISSED)"
        )
        base_result["vcp_score"] = score
        base_result["grade"] = grade_vcp(score)
        base_result["contractions"] = contractions
        base_result["steps"] = steps
        return base_result

    # ------------------------------------------------------------------
    # Step 12 — Breakout confirmation
    # ------------------------------------------------------------------
    today_close = float(df["Close"].iloc[-1])
    today_vol = float(df["Volume"].iloc[-1])
    breakout = (today_close > pivot_price) and (today_vol >= avg_vol_50 * settings.BREAKOUT_VOLUME_RATIO)
    if breakout:
        score += 5
        if today_vol >= avg_vol_50 * settings.BREAKOUT_STRONG_VOLUME:
            score += 5
    steps["breakout_confirmed"] = breakout
    base_result["breakout_confirmed"] = breakout

    # Anti-false-positive liquidity gates
    avg_vol_50d = avg_vol_50
    avg_dollar_vol = avg_vol_50d * float(df["Close"].iloc[-50:].mean())
    price = float(df["Close"].iloc[-1])

    if avg_vol_50d < settings.MIN_DAILY_VOLUME:
        base_result["rejection_reason"] = (
            f"Liquidity gate: avg volume {avg_vol_50d:,.0f} < {settings.MIN_DAILY_VOLUME:,}"
        )
        base_result["vcp_score"] = score
        base_result["grade"] = grade_vcp(score)
        base_result["contractions"] = contractions
        base_result["steps"] = steps
        return base_result

    if avg_dollar_vol < settings.MIN_DOLLAR_VOLUME:
        base_result["rejection_reason"] = (
            f"Liquidity gate: avg dollar volume ${avg_dollar_vol:,.0f} < ${settings.MIN_DOLLAR_VOLUME:,}"
        )
        base_result["vcp_score"] = score
        base_result["grade"] = grade_vcp(score)
        base_result["contractions"] = contractions
        base_result["steps"] = steps
        return base_result

    if price < settings.MIN_PRICE:
        base_result["rejection_reason"] = f"Price ${price:.2f} < ${settings.MIN_PRICE}"
        base_result["vcp_score"] = score
        base_result["grade"] = grade_vcp(score)
        base_result["contractions"] = contractions
        base_result["steps"] = steps
        return base_result

    # Final score and alert gate
    score = max(0, min(100, score))
    base_result["vcp_score"] = score
    base_result["grade"] = grade_vcp(score)
    base_result["contractions"] = contractions
    base_result["steps"] = steps

    if score >= settings.VCP_SCORE_MIN and risk_valid:
        base_result["alert"] = True
    elif score < settings.VCP_SCORE_MIN:
        base_result["rejection_reason"] = (
            f"VCP score {score} < minimum {settings.VCP_SCORE_MIN}"
        )

    return base_result


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _identify_contractions(df: pd.DataFrame, window: int = 5) -> list[dict]:
    """
    Identify price contractions in a base using a smoothing window.
    A contraction = local high to local low depth as a percentage.
    """
    # Smooth with a rolling window to reduce noise
    smoothed_high = df["High"].rolling(window, min_periods=1).max()
    smoothed_low = df["Low"].rolling(window, min_periods=1).min()

    contractions = []
    n = len(df)
    step = max(window, n // 8)  # segment the base into ~8 sections max

    for start in range(0, n - window, step):
        end = min(start + step * 2, n)
        seg_high = float(smoothed_high.iloc[start:end].max())
        seg_low = float(smoothed_low.iloc[start:end].min())
        if seg_high == 0:
            continue
        depth_pct = (seg_high - seg_low) / seg_high * 100
        avg_vol = float(df["Volume"].iloc[start:end].mean())
        contractions.append({
            "depth_pct": round(depth_pct, 1),
            "vol_avg": round(avg_vol, 0),
            "start": start,
            "end": end,
        })

    # Keep at most 5 contractions
    return contractions[:5]


def _check_volume_declining(contractions: list[dict]) -> str:
    """
    Check if volume declines across successive contractions.
    Returns: "all", "most", or "none"
    """
    if len(contractions) < 2:
        return "none"
    declining = 0
    for i in range(1, len(contractions)):
        if contractions[i]["vol_avg"] < contractions[i - 1]["vol_avg"]:
            declining += 1
    total_pairs = len(contractions) - 1
    if declining == total_pairs:
        return "all"
    if declining >= total_pairs * 0.6:
        return "most"
    return "none"


def _compute_atr(df: pd.DataFrame, period: int) -> float:
    """Compute Average True Range over the given period."""
    if len(df) < 2:
        return 0.0
    high = df["High"].values
    low = df["Low"].values
    prev_close = np.roll(df["Close"].values, 1)
    prev_close[0] = df["Close"].values[0]

    tr = np.maximum(
        high - low,
        np.maximum(np.abs(high - prev_close), np.abs(low - prev_close))
    )
    atr = float(np.mean(tr[-period:]))
    return atr


if __name__ == "__main__":
    import logging as _logging
    from database.db import init_db
    from data.fetcher import fetch_ohlcv

    _logging.basicConfig(level=logging.INFO)
    init_db()

    df = fetch_ohlcv("NVDA", "2y")
    result = detect_vcp("NVDA", df, trend_template_passes=True, rs_line_new_high=False)
    print(f"vcp_detector.py: NVDA score={result['vcp_score']}, grade={result['grade']}")
    print(f"  alert={result['alert']}, risk_valid={result['risk_valid']}")
    if result["pivot_price"]:
        print(f"  pivot=${result['pivot_price']}, entry=${result['entry_price']}, "
              f"stop=${result['stop_price']} ({result['stop_pct']:.1f}%)")
    if result["rejection_reason"]:
        print(f"  rejected: {result['rejection_reason']}")
