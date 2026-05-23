"""
Position sizing engine (Section 10).
Fixed-fraction risk model with portfolio drawdown circuit breakers.
All parameters configurable in config/settings.py.
"""

import logging
import math
from typing import Optional

from config import settings
from database.db import get_connection

logger = logging.getLogger(__name__)


def compute_position_size(
    entry_price: float,
    stop_price: float,
    account_equity: float,
    aggression_factor: float = 1.0,
    earnings_size_factor: float = 1.0,
) -> dict:
    """
    Compute recommended position size.

    Formula:
        risk_dollars    = account_equity × RISK_PER_TRADE_PCT × aggression_factor × earnings_factor
        risk_per_share  = entry_price - stop_price
        shares          = floor(risk_dollars / risk_per_share)
        position_value  = shares × entry_price

    Args:
        entry_price:         Planned entry price (GBP equivalent)
        stop_price:          Stop-loss price
        account_equity:      Total account value in GBP
        aggression_factor:   From regime detector (0.0 – 1.0)
        earnings_size_factor: From earnings safety (0.5 or 1.0)

    Returns:
        {
          "shares": int,
          "position_value": float,
          "position_pct": float,
          "risk_dollars": float,
          "risk_pct": float,
          "stop_pct": float,
          "valid": bool,
          "note": str,
        }
    """
    result = {
        "shares": 0,
        "position_value": 0.0,
        "position_pct": 0.0,
        "risk_dollars": 0.0,
        "risk_pct": 0.0,
        "stop_pct": 0.0,
        "valid": False,
        "note": "",
    }

    stop_pct = (entry_price - stop_price) / entry_price * 100

    # Hard gate: stop too wide
    if stop_pct > settings.MAX_STOP_PCT * 100:
        result["note"] = f"Stop {stop_pct:.1f}% > max {settings.MAX_STOP_PCT*100:.0f}% — trade rejected"
        return result

    risk_dollars = (
        account_equity
        * settings.RISK_PER_TRADE_PCT
        * aggression_factor
        * earnings_size_factor
    )
    risk_per_share = entry_price - stop_price

    if risk_per_share <= 0:
        result["note"] = "Entry <= stop — invalid setup"
        return result

    shares = math.floor(risk_dollars / risk_per_share)
    if shares <= 0:
        result["note"] = "Position too small to trade (< 1 share)"
        return result

    position_value = shares * entry_price
    position_pct = position_value / account_equity * 100

    # Cap at MAX_POSITION_PCT
    notes = []
    if position_pct > settings.MAX_POSITION_PCT * 100:
        capped_shares = math.floor(account_equity * settings.MAX_POSITION_PCT / entry_price)
        shares = capped_shares
        position_value = shares * entry_price
        position_pct = position_value / account_equity * 100
        notes.append(f"Capped at {settings.MAX_POSITION_PCT*100:.0f}% max position")

    if position_pct < settings.MIN_POSITION_PCT * 100:
        notes.append(f"Position small ({position_pct:.1f}%) — consider skipping")

    actual_risk = shares * risk_per_share
    actual_risk_pct = actual_risk / account_equity * 100

    result.update({
        "shares": shares,
        "position_value": round(position_value, 2),
        "position_pct": round(position_pct, 2),
        "risk_dollars": round(actual_risk, 2),
        "risk_pct": round(actual_risk_pct, 2),
        "stop_pct": round(stop_pct, 2),
        "valid": True,
        "note": "; ".join(notes) if notes else "OK",
    })
    return result


def get_portfolio_drawdown() -> float:
    """
    Compute current portfolio drawdown from peak equity, based on trade journal.
    Returns a fraction (0.0 = no drawdown, 0.30 = 30% drawdown).
    """
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT entry_price, shares, pnl_gbp FROM positions WHERE status='open'"
        ).fetchall()
        conn.close()

        if not rows:
            return 0.0

        total_pnl = sum(r["pnl_gbp"] or 0.0 for r in rows)
        equity = settings.ACCOUNT_EQUITY_GBP
        if equity <= 0:
            return 0.0

        # Drawdown only on negative P&L
        if total_pnl >= 0:
            return 0.0
        return min(abs(total_pnl) / equity, 1.0)

    except Exception as exc:
        logger.warning("Portfolio drawdown calculation failed: %s", exc)
        return 0.0


def get_aggression_from_drawdown(drawdown: float) -> tuple[float, Optional[str]]:
    """
    Apply portfolio drawdown circuit breakers.
    Returns (aggression_factor, warning_message_or_None).
    """
    if drawdown >= settings.PORTFOLIO_DRAWDOWN_STOP:
        return 0.0, (
            f"🚨 DRAWDOWN CIRCUIT BREAKER: {drawdown*100:.1f}% drawdown — "
            "NO NEW POSITIONS. Focus on capital preservation."
        )
    if drawdown >= settings.PORTFOLIO_DRAWDOWN_SEVERE:
        return 0.25, (
            f"⚠️ Severe drawdown {drawdown*100:.1f}% — sizing reduced to 25%"
        )
    if drawdown >= settings.PORTFOLIO_DRAWDOWN_CAUTION:
        return 0.50, (
            f"⚠️ Drawdown {drawdown*100:.1f}% — sizing reduced to 50%"
        )
    return 1.0, None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = compute_position_size(
        entry_price=143.80,
        stop_price=132.50,
        account_equity=50_000,
        aggression_factor=1.0,
    )
    print(f"position_sizer.py: shares={result['shares']}, "
          f"value=£{result['position_value']:,.0f} ({result['position_pct']:.1f}%), "
          f"risk=£{result['risk_dollars']:,.0f}")
