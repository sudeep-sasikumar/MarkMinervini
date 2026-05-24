"""
Stop-loss, breakeven, and trailing stop logic.
Monitors open positions and suggests stop adjustments.
"""

import logging
from typing import Optional

from config import settings
from database.db import get_connection, db_session

logger = logging.getLogger(__name__)


def check_stop_breached(
    current_price: float,
    stop_price: float,
) -> bool:
    """Return True if price has fallen to or below the stop level."""
    return current_price <= stop_price


def compute_breakeven_stop(
    entry_price: float,
    commission_per_share: float = 0.0,
) -> float:
    """
    Return the breakeven stop price (entry + commissions).
    Trading 212 ISA: £0 commission, so breakeven = entry.
    """
    return round(entry_price + commission_per_share, 2)


def compute_trailing_stop(
    entry_price: float,
    highest_price_since_entry: float,
    initial_stop_pct: float,
    trail_method: str = "initial_risk",
) -> float:
    """
    Compute a trailing stop price.

    trail_method:
        "initial_risk" — trail by same % as initial stop (simplest, most robust)
        "atr"          — not implemented here; handled by caller with ATR data
    """
    if trail_method == "initial_risk":
        return round(highest_price_since_entry * (1 - initial_stop_pct / 100), 2)
    return round(entry_price * (1 - initial_stop_pct / 100), 2)


def update_open_position_stops() -> list[dict]:
    """
    Review all open positions and suggest stop adjustments.
    Returns a list of positions that need stop review.
    """
    from data.fetcher import fetch_latest_price

    conn = get_connection()
    try:
        positions = conn.execute(
            "SELECT id, ticker, entry_price, shares, stop_price FROM positions "
            "WHERE status='open'"
        ).fetchall()
    finally:
        conn.close()

    alerts = []
    for pos in positions:
        try:
            current_price = fetch_latest_price(pos["ticker"])
            if current_price is None:
                continue

            stop = pos["stop_price"]
            entry = pos["entry_price"]
            initial_stop_pct = (entry - stop) / entry * 100

            if check_stop_breached(current_price, stop):
                alerts.append({
                    "ticker": pos["ticker"],
                    "action": "STOP HIT",
                    "current_price": current_price,
                    "stop_price": stop,
                    "message": f"🛑 {pos['ticker']} stop hit at ${current_price:.2f} (stop ${stop:.2f})",
                })
            else:
                # Suggest trailing stop if price has moved significantly in our favour
                gain_pct = (current_price / entry - 1) * 100
                if gain_pct >= initial_stop_pct:
                    # Price has moved at least 1R in our favour — move to breakeven.
                    new_stop = compute_breakeven_stop(entry)
                    if new_stop > stop:
                        # Persist the updated stop to the DB immediately so the
                        # same suggestion doesn't fire again on every post-market run.
                        try:
                            with db_session() as conn:
                                conn.execute(
                                    "UPDATE positions SET stop_price=? WHERE id=?",
                                    (new_stop, pos["id"]),
                                )
                            logger.info(
                                "Stop updated for %s: %.2f → %.2f (breakeven)",
                                pos["ticker"], stop, new_stop,
                            )
                        except Exception as db_exc:
                            logger.warning(
                                "Failed to persist breakeven stop for %s: %s",
                                pos["ticker"], db_exc,
                            )
                        alerts.append({
                            "ticker": pos["ticker"],
                            "action": "MOVE TO BREAKEVEN",
                            "current_price": current_price,
                            "stop_price": stop,
                            "suggested_stop": new_stop,
                            "message": (
                                f"📈 {pos['ticker']} +{gain_pct:.1f}% — "
                                f"stop moved to breakeven ${new_stop:.2f}"
                            ),
                        })
        except Exception as exc:
            logger.warning("Stop check error for %s: %s", pos["ticker"], exc)

    return alerts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    trail = compute_trailing_stop(100.0, 115.0, 7.85)
    print(f"stop_manager.py: trailing stop = ${trail}")
    be = compute_breakeven_stop(100.0)
    print(f"  breakeven stop = ${be}")
