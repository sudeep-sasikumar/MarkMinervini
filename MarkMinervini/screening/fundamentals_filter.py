"""
Fundamental filter (Section 5C).
Hard gates: EPS growth ≥20% YoY, Revenue growth ≥15% YoY, expanding gross margin.
Scored additions: EPS acceleration, annual EPS ≥25%, ROE ≥17%, positive surprise,
                  institutional ownership 30–70%, EPS revisions ≥+5%.

Wraps data/fundamentals.py — all caching handled there.
"""

import logging
from typing import Optional

from config import settings
from data.fundamentals import fetch_fundamentals

logger = logging.getLogger(__name__)


def apply_fundamentals_filter(ticker: str) -> dict:
    """
    Fetch fundamentals and apply the Minervini fundamental filter.

    Returns:
        {
          "ticker": str,
          "passes": bool,               # True = all hard gates cleared
          "fundamentals_score": int,    # 0–10 scored additions
          "status": str,                # "ok" | "partial" | "unknown"
          "eps_growth_yoy": float | None,
          "rev_growth_yoy": float | None,
          "gross_margin_current": float | None,
          "gross_margin_prior": float | None,
          "roe": float | None,
          "details": dict,              # full fundamentals payload
          "rejection_reason": str | None,
        }
    """
    data = fetch_fundamentals(ticker)

    result: dict = {
        "ticker": ticker,
        "passes": False,
        "fundamentals_score": data.get("fundamentals_score", 0),
        "status": data.get("status", "unknown"),
        "eps_growth_yoy": data.get("eps_growth_yoy"),
        "rev_growth_yoy": data.get("rev_growth_yoy"),
        "gross_margin_current": data.get("gross_margin_current"),
        "gross_margin_prior": data.get("gross_margin_prior"),
        "roe": data.get("roe"),
        "details": data,
        "rejection_reason": None,
    }

    # --- Status gate ---
    if data["status"] == "unknown":
        result["rejection_reason"] = "fundamentals_unknown — no data from any source"
        return result

    # --- Hard gate 1: EPS growth ≥ 20% YoY ---
    eps = data.get("eps_growth_yoy")
    if eps is None:
        result["rejection_reason"] = "EPS growth data unavailable"
        return result
    if eps < settings.EPS_GROWTH_MIN:
        result["rejection_reason"] = (
            f"EPS growth {eps:.1f}% < minimum {settings.EPS_GROWTH_MIN}%"
        )
        return result

    # --- Hard gate 2: Revenue growth ≥ 15% YoY ---
    rev = data.get("rev_growth_yoy")
    if rev is None:
        result["rejection_reason"] = "Revenue growth data unavailable"
        return result
    if rev < settings.REVENUE_GROWTH_MIN:
        result["rejection_reason"] = (
            f"Revenue growth {rev:.1f}% < minimum {settings.REVENUE_GROWTH_MIN}%"
        )
        return result

    # --- Hard gate 3: Expanding gross margin ---
    # Missing margin data is treated as a FAILURE (conservative gate).
    # A stock should not pass simply because we have no margin data.
    gm_now = data.get("gross_margin_current")
    gm_prior = data.get("gross_margin_prior")
    if gm_now is None or gm_prior is None:
        result["rejection_reason"] = "Gross margin data unavailable — cannot verify non-contraction"
        return result
    if gm_now < gm_prior:
        result["rejection_reason"] = (
            f"Gross margin contracting: {gm_now:.1f}% vs {gm_prior:.1f}% prior year"
        )
        return result

    result["passes"] = True
    return result


def batch_fundamentals_filter(tickers: list[str]) -> list[dict]:
    """
    Run fundamentals filter on a list of tickers.
    Returns only passing tickers.
    """
    passing = []
    for ticker in tickers:
        try:
            result = apply_fundamentals_filter(ticker)
            if result["passes"]:
                passing.append(result)
            else:
                logger.debug(
                    "Fundamentals reject %s: %s",
                    ticker, result.get("rejection_reason", "unknown")
                )
        except Exception as exc:
            logger.warning("Fundamentals filter error for %s: %s", ticker, exc)

    logger.info(
        "Fundamentals filter: %d/%d passed",
        len(passing), len(tickers),
    )
    return passing


if __name__ == "__main__":
    import logging as _logging
    from database.db import init_db

    _logging.basicConfig(level=logging.INFO)
    init_db()

    result = apply_fundamentals_filter("NVDA")
    print(f"fundamentals_filter.py: NVDA passes={result['passes']}")
    print(f"  EPS growth: {result['eps_growth_yoy']}")
    print(f"  Rev growth: {result['rev_growth_yoy']}")
    print(f"  Score: {result['fundamentals_score']}/10")
    if result["rejection_reason"]:
        print(f"  Rejected: {result['rejection_reason']}")
