"""
Volatility Contraction Pattern (VCP) detector — Section 6.
Produces a score 0–100. Only scores >= 80 AND confirmed breakout generate alerts.

Algorithm (12 steps):
  1.  Confirm Stage 2 uptrend (Trend Template must pass)
  2.  Confirm prior advance >= 30% (pre-base window, not overlapping base)
  3.  Find the base (60–120 trading days back from today)
  4.  Identify contractions using swing-point high/low detection
  5.  Validate contraction series (tightening, min 2 contractions)
  6.  Validate volume pattern (declining volume across contractions)
  7.  Evaluate pivot zone (last 5–15 days, ATR collapse)
  8.  Volume dry-up in final 5 days
  9.  No wide-and-loose bars in pivot zone (>3% daily range vs High)
  10. Compute entry, stop, targets
  11. Gap-up filter
  12. Breakout check (today's session) — REQUIRED for alert=True
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
          "vcp_score": int,           # 0–100
          "grade": str,
          "alert": bool,              # True if score >= 80 AND breakout confirmed
          "watchlist_candidate": bool,# True if score >= 70 (near-pivot setup)
          "risk_valid": bool,         # False if stop > 8%
          "contractions": list,       # [{depth_pct, vol_avg}, ...]
          "pivot_price": float,
          "entry_price": float,
          "stop_price": float,
          "stop_pct": float,
          "target_1": float,
          "target_2": float,
          "base_days": int,
          "breakout_confirmed": bool,
          "rejection_reason": str | None,
          "steps": dict,              # per-step details for dashboard
        }
    """
    base_result: dict = {
        "ticker": ticker,
        "vcp_score": 0,
        "grade": GRADE_NONE,
        "alert": False,
        "watchlist_candidate": False,
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
    # Correctly measures advance BEFORE the base started, not including
    # the base itself.
    # ------------------------------------------------------------------
    base_window = df.iloc[-settings.MAX_BASE_TRADING_DAYS:].copy()
    peak_pos = int(base_window["Close"].values.argmax())
    base_start_idx = len(df) - settings.MAX_BASE_TRADING_DAYS + peak_pos

    # Pre-base window: look back up to 6 months before the base peak
    pre_base_lookback = min(126, base_start_idx)
    pre_base_start = max(0, base_start_idx - pre_base_lookback)
    pre_base_df = df.iloc[pre_base_start:base_start_idx]

    if len(pre_base_df) < 20:
        base_result["rejection_reason"] = "Step 2: Insufficient pre-base history to confirm prior advance"
        return base_result

    pre_base_low = float(pre_base_df["Low"].min())
    base_peak = float(base_window["Close"].max())

    if pre_base_low == 0:
        base_result["rejection_reason"] = "Step 2: Price data error (zero low)"
        return base_result

    prior_advance_pct = (base_peak / pre_base_low - 1) * 100
    steps["prior_advance_pct"] = round(prior_advance_pct, 1)

    if prior_advance_pct < settings.MIN_PRIOR_ADVANCE:
        base_result["rejection_reason"] = (
            f"Step 2: Prior advance {prior_advance_pct:.1f}% < {settings.MIN_PRIOR_ADVANCE}%"
        )
        base_result["steps"] = steps
        return base_result

    # ------------------------------------------------------------------
    # Step 3 — Find the base (from peak close to today)
    # ------------------------------------------------------------------
    base_df = base_window.iloc[peak_pos:].copy()
    base_days = len(base_df)
    steps["base_days"] = base_days
    base_result["base_days"] = base_days

    # Minimum base length: 60 trading days (settings.MIN_BASE_TRADING_DAYS).
    # Previously used MIN_BASE_WEEKS * 5 = 15 days, which contradicted both the
    # docstring ("60–120 trading days") and the settings constant.
    if base_days < settings.MIN_BASE_TRADING_DAYS:
        base_result["rejection_reason"] = (
            f"Step 3: Base only {base_days} days "
            f"(minimum {settings.MIN_BASE_TRADING_DAYS} trading days)"
        )
        base_result["steps"] = steps
        return base_result

    # ------------------------------------------------------------------
    # Step 4 — Identify contractions using swing-point detection
    # ------------------------------------------------------------------
    contractions = _identify_contractions(base_df)
    steps["num_contractions"] = len(contractions)
    steps["contractions"] = contractions

    # ------------------------------------------------------------------
    # Step 5 — Validate contraction series (must tighten)
    # ------------------------------------------------------------------
    if len(contractions) < settings.MIN_CONTRACTIONS:
        base_result["rejection_reason"] = (
            f"Step 5: Only {len(contractions)} contraction(s); minimum {settings.MIN_CONTRACTIONS}"
        )
        base_result["steps"] = steps
        return base_result

    for i in range(1, len(contractions)):
        prev_depth = contractions[i - 1]["depth_pct"]
        curr_depth = contractions[i]["depth_pct"]

        # Hard fail 1: contraction is WIDER than the previous — not a VCP
        if curr_depth >= prev_depth:
            base_result["rejection_reason"] = (
                f"Step 5: Contraction {i+1} ({curr_depth:.1f}%) wider than "
                f"contraction {i} ({prev_depth:.1f}%) — widening, not a VCP"
            )
            base_result["steps"] = steps
            return base_result

        # Hard fail 2: not tight enough — must shrink by at least (1 - RATIO)
        # e.g. CONTRACTION_TIGHTENING_RATIO=0.85 → each must be < 85% of previous
        required_max = prev_depth * settings.CONTRACTION_TIGHTENING_RATIO
        if curr_depth > required_max:
            base_result["rejection_reason"] = (
                f"Step 5: Contraction {i+1} ({curr_depth:.1f}%) does not tighten enough "
                f"— must be < {required_max:.1f}% (85% of {prev_depth:.1f}%)"
            )
            base_result["steps"] = steps
            return base_result

    if len(contractions) >= 3:
        score += 25
    score += 10 if len(contractions) >= 4 else 0
    steps["step5_valid"] = True

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
    # Use High as denominator (correct Minervini definition)
    # ------------------------------------------------------------------
    last5 = base_df.iloc[-5:]
    daily_ranges = ((last5["High"] - last5["Low"]) / last5["High"]).values
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
    # REQUIRED for alert=True — price must close > pivot on >= 1.4× volume
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
    avg_dollar_vol = avg_vol_50 * float(df["Close"].iloc[-50:].mean())
    price = float(df["Close"].iloc[-1])

    if avg_vol_50 < settings.MIN_DAILY_VOLUME:
        base_result["rejection_reason"] = (
            f"Liquidity gate: avg volume {avg_vol_50:,.0f} < {settings.MIN_DAILY_VOLUME:,}"
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

    # Final score clamped to [0, 100]
    score = max(0, min(100, score))
    base_result["vcp_score"] = score
    base_result["grade"] = grade_vcp(score)
    base_result["contractions"] = contractions
    base_result["steps"] = steps

    # Watchlist candidate: score >= 70 and risk valid (setup forming, not yet broken out)
    base_result["watchlist_candidate"] = score >= 70 and risk_valid

    # ALERT requires BOTH score >= 80 AND confirmed breakout
    if score >= settings.VCP_SCORE_MIN and risk_valid and breakout:
        base_result["alert"] = True
    elif score >= settings.VCP_SCORE_MIN and risk_valid and not breakout:
        base_result["rejection_reason"] = (
            f"No confirmed breakout: close ${today_close:.2f} <= pivot ${pivot_price:.2f} "
            f"or volume {today_vol:,.0f} < {avg_vol_50 * settings.BREAKOUT_VOLUME_RATIO:,.0f} "
            f"(1.4× avg). Add to watchlist — await breakout."
        )
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
    Identify VCP contractions using swing-point high/low detection.

    Algorithm:
      1. Smooth prices with a rolling window to reduce noise
      2. Find pivot highs (local maxima) and pivot lows (local minima)
      3. Pair each pivot high with the NEXT pivot low → one contraction leg
      4. Deduplicate to avoid overlapping contractions
      5. Return sorted by time, max 5 contractions
    """
    n = len(df)
    if n < window * 4:
        return []

    smoothed_high = df["High"].rolling(window, min_periods=1).mean()
    smoothed_low = df["Low"].rolling(window, min_periods=1).mean()

    half = max(2, window // 2)

    # Find pivot highs: points that are local maxima within ±half bars
    pivot_highs: list[tuple[int, float]] = []
    for i in range(half, n - half):
        window_vals = smoothed_high.iloc[i - half:i + half + 1]
        if smoothed_high.iloc[i] >= float(window_vals.max()) - 1e-9:
            pivot_highs.append((i, float(smoothed_high.iloc[i])))

    # Find pivot lows: points that are local minima within ±half bars
    pivot_lows: list[tuple[int, float]] = []
    for i in range(half, n - half):
        window_vals = smoothed_low.iloc[i - half:i + half + 1]
        if smoothed_low.iloc[i] <= float(window_vals.min()) + 1e-9:
            pivot_lows.append((i, float(smoothed_low.iloc[i])))

    # Deduplicate: keep only pivots at least `window` bars apart
    pivot_highs = _deduplicate_pivots(pivot_highs, min_gap=window)
    pivot_lows = _deduplicate_pivots(pivot_lows, min_gap=window)

    if not pivot_highs or not pivot_lows:
        return []

    # Pair each pivot high with the NEXT pivot low
    contractions = []
    used_low_indices = set()
    for ph_idx, ph_price in pivot_highs:
        # Find the earliest pivot low after this high that hasn't been used
        next_lows = [(li, lp) for li, lp in pivot_lows if li > ph_idx and li not in used_low_indices]
        if not next_lows:
            continue
        pl_idx, pl_price = min(next_lows, key=lambda x: x[0])
        if ph_price == 0:
            continue
        depth_pct = (ph_price - pl_price) / ph_price * 100
        # Only meaningful contractions (> 2% depth to filter noise)
        if depth_pct < 2.0:
            continue
        seg_start = ph_idx
        seg_end = min(pl_idx + 1, n)
        avg_vol = float(df["Volume"].iloc[seg_start:seg_end].mean()) if seg_end > seg_start else float(df["Volume"].iloc[ph_idx])
        contractions.append({
            "depth_pct": round(depth_pct, 1),
            "vol_avg": round(avg_vol, 0),
            "start": ph_idx,
            "end": pl_idx,
        })
        used_low_indices.add(pl_idx)

    # Sort chronologically and keep at most 5
    contractions.sort(key=lambda x: x["start"])
    return contractions[:5]


def _deduplicate_pivots(pivots: list, min_gap: int) -> list:
    """Remove pivot points that are too close together (< min_gap bars apart)."""
    if not pivots:
        return []
    result = [pivots[0]]
    for pivot in pivots[1:]:
        if pivot[0] - result[-1][0] >= min_gap:
            result.append(pivot)
    return result


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
    print(f"  alert={result['alert']}, watchlist_candidate={result['watchlist_candidate']}")
    print(f"  risk_valid={result['risk_valid']}, breakout={result['breakout_confirmed']}")
    if result["pivot_price"]:
        print(f"  pivot=${result['pivot_price']}, entry=${result['entry_price']}, "
              f"stop=${result['stop_price']} ({result['stop_pct']:.1f}%)")
    print(f"  contractions={len(result['contractions'])}: "
          + ", ".join(f"{c['depth_pct']:.1f}%" for c in result["contractions"]))
    if result["rejection_reason"]:
        print(f"  info: {result['rejection_reason']}")
