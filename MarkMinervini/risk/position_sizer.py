"""
Position sizing engine (Section 10).
Fixed-fraction risk model with portfolio drawdown circuit breakers.

GBP/USD handling:
  - Account equity is in GBP (Trading 212 ISA)
  - US stock prices are in USD
  - Risk budget is converted to USD using live GBPUSD before share calculation
  - Output shows both USD position value and GBP equivalent

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
    gbpusd_rate: Optional[float] = None,
) -> dict:
    """
    Compute recommended position size with GBP/USD FX conversion.

    The account is in GBP; US stocks are priced in USD.
    Risk is budgeted in GBP, converted to USD for share calculation,
    then position value is shown in both USD and GBP.

    Formula:
        risk_gbp        = account_equity_GBP × RISK_PCT × aggression × earnings_factor
        risk_usd        = risk_gbp × gbpusd_rate
        risk_per_share  = entry_price_USD - stop_price_USD
        shares          = floor(risk_usd / risk_per_share)
        position_usd    = shares × entry_price_USD
        position_gbp    = position_usd / gbpusd_rate
        position_pct    = position_gbp / account_equity_GBP × 100

    Args:
        entry_price:         Planned entry price (USD)
        stop_price:          Stop-loss price (USD)
        account_equity:      Total account value in GBP
        aggression_factor:   From regime detector (0.0 – 1.0)
        earnings_size_factor: From earnings safety (0.5 or 1.0)
        gbpusd_rate:         GBP/USD exchange rate (fetched if None)

    Returns:
        {
          "shares": int,
          "position_value_usd": float,
          "position_value_gbp": float,
          "position_pct": float,        # % of GBP account
          "risk_gbp": float,            # budgeted risk in GBP
          "risk_usd": float,            # budgeted risk in USD
          "risk_pct": float,
          "stop_pct": float,
          "gbpusd_rate": float,
          "valid": bool,
          "note": str,
          # Legacy alias
          "position_value": float,      # = position_value_gbp
          "risk_dollars": float,        # legacy alias = risk_usd
        }
    """
    fx_warning = False
    # Fetch live GBPUSD if not supplied.
    # Use fetch_gbpusd_with_source() so we can reliably detect whether the
    # returned rate is live or a hardcoded fallback — fetch_gbpusd() swallows
    # its own exception and returns 1.27 silently, so we would never know.
    if gbpusd_rate is None:
        from data.fetcher import fetch_gbpusd_with_source
        fx = fetch_gbpusd_with_source()
        gbpusd_rate = fx["rate"]
        if fx["source"] == "fallback":
            fx_warning = True
            logger.warning("GBPUSD fetch used fallback rate 1.27 — verify position size manually")

    result = {
        "shares": 0,
        "position_value_usd": 0.0,
        "position_value_gbp": 0.0,
        "position_value": 0.0,       # legacy alias = position_value_gbp
        "position_pct": 0.0,
        "risk_gbp": 0.0,
        "risk_usd": 0.0,
        "risk_dollars": 0.0,          # legacy alias = risk_usd
        "risk_pct": 0.0,
        "stop_pct": 0.0,
        "gbpusd_rate": gbpusd_rate,
        "fx_rate_source": "fallback" if fx_warning else "live",
        "fx_warning": fx_warning,
        "valid": False,
        "note": "",
    }

    stop_pct = (entry_price - stop_price) / entry_price * 100

    # Hard gate: stop too wide
    if stop_pct > settings.MAX_STOP_PCT * 100:
        result["note"] = f"Stop {stop_pct:.1f}% > max {settings.MAX_STOP_PCT*100:.0f}% — trade rejected"
        result["stop_pct"] = round(stop_pct, 2)
        return result

    # Risk budget in GBP, converted to USD
    risk_gbp = (
        account_equity
        * settings.RISK_PER_TRADE_PCT
        * aggression_factor
        * earnings_size_factor
    )
    risk_usd = risk_gbp * gbpusd_rate

    risk_per_share = entry_price - stop_price
    if risk_per_share <= 0:
        result["note"] = "Entry <= stop — invalid setup"
        return result

    shares = math.floor(risk_usd / risk_per_share)
    if shares <= 0:
        result["note"] = "Position too small to trade (< 1 share)"
        return result

    position_value_usd = shares * entry_price
    position_value_gbp = position_value_usd / gbpusd_rate
    position_pct = position_value_gbp / account_equity * 100

    # Cap at MAX_POSITION_PCT (based on GBP account)
    notes = []
    if position_pct > settings.MAX_POSITION_PCT * 100:
        max_gbp = account_equity * settings.MAX_POSITION_PCT
        max_usd = max_gbp * gbpusd_rate
        capped_shares = math.floor(max_usd / entry_price)
        shares = capped_shares
        position_value_usd = shares * entry_price
        position_value_gbp = position_value_usd / gbpusd_rate
        position_pct = position_value_gbp / account_equity * 100
        notes.append(f"Capped at {settings.MAX_POSITION_PCT*100:.0f}% max position")

    if position_pct < settings.MIN_POSITION_PCT * 100:
        notes.append(f"Position small ({position_pct:.1f}%) — consider skipping")

    actual_risk_usd = shares * risk_per_share
    actual_risk_gbp = actual_risk_usd / gbpusd_rate
    actual_risk_pct = actual_risk_gbp / account_equity * 100

    if fx_warning:
        notes.append(f"⚠️ FX rate fallback (1.27) — verify position size manually")

    result.update({
        "shares": shares,
        "position_value_usd": round(position_value_usd, 2),
        "position_value_gbp": round(position_value_gbp, 2),
        "position_value": round(position_value_gbp, 2),      # legacy alias = GBP value
        "position_pct": round(position_pct, 2),
        "risk_gbp": round(actual_risk_gbp, 2),
        "risk_usd": round(actual_risk_usd, 2),
        "risk_dollars": round(actual_risk_usd, 2),           # alias = USD (matches "dollars")
        "risk_pct": round(actual_risk_pct, 2),
        "stop_pct": round(stop_pct, 2),
        "gbpusd_rate": gbpusd_rate,
        "fx_rate_source": "fallback" if fx_warning else "live",
        "fx_warning": fx_warning,
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
        entry_price=143.80,    # USD
        stop_price=132.50,     # USD
        account_equity=50_000, # GBP
        aggression_factor=1.0,
    )
    rate = result["gbpusd_rate"]
    print(f"position_sizer.py: shares={result['shares']}, "
          f"position=${result['position_value_usd']:,.0f} USD / "
          f"£{result['position_value_gbp']:,.0f} GBP ({result['position_pct']:.1f}%), "
          f"max loss £{result['risk_gbp']:,.0f} | GBPUSD={rate:.4f}")
