"""
Fundamental data fetcher.
Primary: Finnhub /stock/metric and /stock/financials-reported (free tier).
Fallback: Alpha Vantage (25 calls/day limit — used sparingly).
All results cached 7 days in SQLite.
"""

import json
import logging
import os
import time
from typing import Optional

import requests

from config import settings
from data.cache import get as cache_get, set as cache_set, TTL_7D

logger = logging.getLogger(__name__)

_AV_CALLS_TODAY = 0  # rough in-process counter (resets on restart)


def _finnhub_get(endpoint: str, params: dict) -> Optional[dict]:
    if not settings.FINNHUB_API_KEY:
        return None
    url = f"https://finnhub.io/api/v1{endpoint}"
    params["token"] = settings.FINNHUB_API_KEY
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Finnhub %s failed: %s", endpoint, exc)
        return None


def _alpha_vantage_get(function: str, params: dict) -> Optional[dict]:
    global _AV_CALLS_TODAY
    if not settings.ALPHA_VANTAGE_KEY:
        return None
    if _AV_CALLS_TODAY >= settings.ALPHA_VANTAGE_MAX_DAILY:
        logger.warning("Alpha Vantage daily call limit reached (%d)", _AV_CALLS_TODAY)
        return None
    params.update({"function": function, "apikey": settings.ALPHA_VANTAGE_KEY})
    try:
        resp = requests.get("https://www.alphavantage.co/query", params=params, timeout=15)
        resp.raise_for_status()
        _AV_CALLS_TODAY += 1
        return resp.json()
    except Exception as exc:
        logger.warning("Alpha Vantage %s failed: %s", function, exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_fundamentals(ticker: str) -> dict:
    """
    Return fundamental metrics for a ticker.
    Shape:
        {
          "ticker": str,
          "status": "ok" | "partial" | "unknown",
          "eps_growth_yoy": float | None,   # % quarterly EPS growth YoY
          "rev_growth_yoy": float | None,   # % quarterly revenue growth YoY
          "gross_margin_current": float | None,
          "gross_margin_prior": float | None,
          "eps_growth_annual": float | None,
          "roe": float | None,              # %
          "eps_surprise_pct": float | None, # % beat vs estimate
          "institutional_own_pct": float | None,
          "eps_revision_pct": float | None,
          "fundamentals_score": int,        # 0–10 scored additions
          "passes_hard_gates": bool,
          "raw": dict,                      # raw Finnhub metric response
        }
    """
    cache_key = f"fundamentals:{ticker}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    result = _build_fundamentals(ticker)
    cache_set(cache_key, result, ttl_seconds=TTL_7D)
    return result


def _build_fundamentals(ticker: str) -> dict:
    base: dict = {
        "ticker": ticker,
        "status": "unknown",
        "eps_growth_yoy": None,
        "rev_growth_yoy": None,
        "gross_margin_current": None,
        "gross_margin_prior": None,
        "eps_growth_annual": None,
        "roe": None,
        "eps_surprise_pct": None,
        "institutional_own_pct": None,
        "eps_revision_pct": None,
        "fundamentals_score": 0,
        "passes_hard_gates": False,
        "raw": {},
    }

    # --- Finnhub key metrics ---
    metrics_data = _finnhub_get("/stock/metric", {"symbol": ticker, "metric": "all"})
    if metrics_data and "metric" in metrics_data:
        m = metrics_data["metric"]
        base["raw"] = m
        base["roe"] = m.get("roeTTM")
        base["gross_margin_current"] = m.get("grossMarginTTM")
        base["eps_growth_annual"] = m.get("epsGrowth5Y")  # approximation
        base["institutional_own_pct"] = m.get("institutionalOwnershipPercentage")
        base["status"] = "partial"

    # --- Finnhub reported financials for quarterly EPS/rev growth ---
    fins = _finnhub_get("/stock/financials-reported",
                        {"symbol": ticker, "freq": "quarterly"})
    if fins and fins.get("data"):
        _parse_quarterly(fins["data"], base)
        base["status"] = "ok"

    # --- Fallback to Alpha Vantage if Finnhub gave nothing useful ---
    if base["eps_growth_yoy"] is None and base["status"] != "ok":
        _alpha_vantage_fallback(ticker, base)

    # --- Compute hard gates ---
    eps_ok = (base["eps_growth_yoy"] is not None and
              base["eps_growth_yoy"] >= settings.EPS_GROWTH_MIN)
    rev_ok = (base["rev_growth_yoy"] is not None and
              base["rev_growth_yoy"] >= settings.REVENUE_GROWTH_MIN)
    margin_ok = (
        base["gross_margin_current"] is not None and
        base["gross_margin_prior"] is not None and
        base["gross_margin_current"] >= base["gross_margin_prior"]
    )
    base["passes_hard_gates"] = eps_ok and rev_ok and margin_ok

    # --- Compute scored additions (0–10) ---
    score = 0
    if base["eps_growth_annual"] is not None and base["eps_growth_annual"] >= 25:
        score += 1
    if base["roe"] is not None and base["roe"] >= settings.ROE_MIN:
        score += 1
    if base["eps_surprise_pct"] is not None and base["eps_surprise_pct"] > 0:
        score += 1
    inst = base["institutional_own_pct"]
    if inst is not None and 30 <= inst <= 70:
        score += 1
    if base["eps_revision_pct"] is not None and base["eps_revision_pct"] >= 5:
        score += 2
    base["fundamentals_score"] = min(score, 10)

    return base


def _parse_quarterly(reports: list, base: dict) -> None:
    """Extract QoQ EPS/revenue growth from Finnhub reported financials."""
    try:
        # Sort by period descending — most recent first
        reports.sort(key=lambda r: r.get("period", ""), reverse=True)
        if len(reports) < 5:
            return

        def _find(report, names):
            """Search income statement concepts by label."""
            for concept in report.get("report", {}).get("ic", []):
                if concept.get("label", "").lower() in [n.lower() for n in names]:
                    return concept.get("value")
            return None

        q0 = reports[0]  # most recent quarter
        q4 = reports[4]  # same quarter last year

        eps_now = _find(q0, ["eps", "earnings per share", "diluted eps",
                              "basic eps", "earningspersharediluted"])
        eps_ly = _find(q4, ["eps", "earnings per share", "diluted eps",
                             "basic eps", "earningspersharediluted"])
        rev_now = _find(q0, ["revenue", "revenues", "net revenue",
                              "total revenue", "salesrevenuenet"])
        rev_ly = _find(q4, ["revenue", "revenues", "net revenue",
                             "total revenue", "salesrevenuenet"])
        gp_now = _find(q0, ["gross profit", "grossprofit"])
        gp_ly = _find(q4, ["gross profit", "grossprofit"])

        if eps_now is not None and eps_ly is not None and eps_ly != 0:
            base["eps_growth_yoy"] = (eps_now - eps_ly) / abs(eps_ly) * 100
        if rev_now is not None and rev_ly is not None and rev_ly != 0:
            base["rev_growth_yoy"] = (rev_now - rev_ly) / abs(rev_ly) * 100
        if gp_now is not None and rev_now and rev_now != 0:
            base["gross_margin_current"] = gp_now / rev_now * 100
        if gp_ly is not None and rev_ly and rev_ly != 0:
            base["gross_margin_prior"] = gp_ly / rev_ly * 100

    except Exception as exc:
        logger.debug("Quarterly parse error for %s: %s", base.get("ticker"), exc)


def _alpha_vantage_fallback(ticker: str, base: dict) -> None:
    """Use Alpha Vantage INCOME_STATEMENT as last resort."""
    data = _alpha_vantage_get("INCOME_STATEMENT", {"symbol": ticker})
    if not data or "quarterlyReports" not in data:
        return
    try:
        reports = data["quarterlyReports"]
        if len(reports) < 5:
            return
        q0 = reports[0]
        q4 = reports[4]

        def _float(d, k):
            v = d.get(k)
            if v and v != "None":
                try:
                    return float(v)
                except ValueError:
                    pass
            return None

        eps_now = _float(q0, "reportedEPS")
        eps_ly = _float(q4, "reportedEPS")
        rev_now = _float(q0, "totalRevenue")
        rev_ly = _float(q4, "totalRevenue")
        gp_now = _float(q0, "grossProfit")
        gp_ly = _float(q4, "grossProfit")

        if eps_now is not None and eps_ly and eps_ly != 0:
            base["eps_growth_yoy"] = (eps_now - eps_ly) / abs(eps_ly) * 100
        if rev_now is not None and rev_ly and rev_ly != 0:
            base["rev_growth_yoy"] = (rev_now - rev_ly) / abs(rev_ly) * 100
        if gp_now is not None and rev_now and rev_now != 0:
            base["gross_margin_current"] = gp_now / rev_now * 100
        if gp_ly is not None and rev_ly and rev_ly != 0:
            base["gross_margin_prior"] = gp_ly / rev_ly * 100
        base["status"] = "ok"
    except Exception as exc:
        logger.debug("Alpha Vantage parse error for %s: %s", ticker, exc)


if __name__ == "__main__":
    from database.db import init_db
    logging.basicConfig(level=logging.INFO)
    init_db()
    result = fetch_fundamentals("AAPL")
    print(f"fundamentals.py: status={result['status']}, passes={result['passes_hard_gates']}")
    print(f"  EPS growth YoY: {result['eps_growth_yoy']}")
    print(f"  Revenue growth: {result['rev_growth_yoy']}")
