"""
Fundamental data fetcher.
Primary: Finnhub /stock/metric and /stock/financials-reported (free tier).
Fallback: Alpha Vantage (25 calls/day limit — used sparingly).
All results cached 7 days in SQLite.
"""

import json
import logging
import os
import threading
import time
from collections import deque
from typing import Optional

import requests

from config import settings
from data.cache import get as cache_get, set as cache_set, TTL_7D

logger = logging.getLogger(__name__)

_AV_CALLS_TODAY = 0  # rough in-process counter (resets on restart)

# ---------------------------------------------------------------------------
# Finnhub rate limiter — 55 calls / 60 s (free tier ceiling)
# Uses a sliding-window deque; thread-safe via lock.
# Previously fundamentals.py bypassed the shared rate limiter entirely,
# causing 429 errors on batch runs.
# ---------------------------------------------------------------------------
_fh_lock = threading.Lock()
_fh_call_times: deque = deque()


def _finnhub_get(endpoint: str, params: dict) -> Optional[dict]:
    if not settings.FINNHUB_API_KEY:
        return None

    with _fh_lock:
        now = time.time()
        # Drop calls older than the 60-second window
        while _fh_call_times and now - _fh_call_times[0] > 60.0:
            _fh_call_times.popleft()
        # If at the per-minute ceiling, sleep until the oldest call ages out
        if len(_fh_call_times) >= settings.FINNHUB_MAX_CALLS_PER_MIN:
            wait = 60.0 - (now - _fh_call_times[0]) + 0.1
            if wait > 0:
                logger.debug("Finnhub rate limit: sleeping %.1fs", wait)
                time.sleep(wait)
            now = time.time()
            while _fh_call_times and now - _fh_call_times[0] > 60.0:
                _fh_call_times.popleft()
        _fh_call_times.append(time.time())

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
        "eps_growth_prior_yoy": None,  # prior quarter's YoY growth (for acceleration check)
        "eps_accelerating": False,     # True if current quarter's growth > prior quarter's
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
    # /stock/metric returns pre-computed growth rates on the free tier.
    # We pull quarterly YoY growth directly from here (more reliable than
    # parsing financials-reported whose field label matching is fragile).
    metrics_data = _finnhub_get("/stock/metric", {"symbol": ticker, "metric": "all"})
    if metrics_data and "metric" in metrics_data:
        m = metrics_data["metric"]
        base["raw"] = m
        base["roe"] = m.get("roeTTM")
        base["gross_margin_current"] = m.get("grossMarginTTM")
        base["eps_growth_annual"] = m.get("epsGrowth5Y")  # approximation
        base["institutional_own_pct"] = m.get("institutionalOwnershipPercentage")
        base["status"] = "partial"

        # --- Primary source for quarterly growth: metric endpoint fields ---
        # Finnhub returns these as ratios (e.g. 0.25 = 25%).  Convert to %.
        # Field name variants observed across Finnhub API versions:
        #   epsGrowthQuarterlyYOY / epsGrowthQuarterlyYoy / epsGrowthTTMYoy
        for _eps_field in ("epsGrowthQuarterlyYOY", "epsGrowthQuarterlyYoy", "epsGrowthTTMYoy"):
            _v = m.get(_eps_field)
            if _v is not None:
                base["eps_growth_yoy"] = round(float(_v) * 100, 1)
                break

        for _rev_field in ("revenueGrowthQuarterlyYOY", "revenueGrowthQuarterlyYoy",
                           "revenueGrowthTTMYoy", "revenueGrowth3Y"):
            _v = m.get(_rev_field)
            if _v is not None:
                base["rev_growth_yoy"] = round(float(_v) * 100, 1)
                break

        # Prior-year gross margin for contraction check.
        # Prefer explicit annual comparison if available.
        for _gm_field in ("grossMarginAnnual", "grossMargin5Y"):
            _v = m.get(_gm_field)
            if _v is not None:
                base["gross_margin_prior"] = float(_v)
                break

        if base["eps_growth_yoy"] is not None:
            logger.debug(
                "Fundamentals %s: metric-endpoint EPS growth=%.1f%% rev_growth=%.1f%%",
                ticker,
                base["eps_growth_yoy"],
                base["rev_growth_yoy"] if base["rev_growth_yoy"] is not None else 0.0,
            )

    # --- Finnhub reported financials for quarterly EPS/rev growth ---
    # This provides more precise YoY comparisons but requires label matching.
    # Only overrides the metric-endpoint values if it produces non-None results.
    fins = _finnhub_get("/stock/financials-reported",
                        {"symbol": ticker, "freq": "quarterly"})
    if fins and fins.get("data"):
        _parse_quarterly(fins["data"], base)
        base["status"] = "ok"

    # --- Fallback to Alpha Vantage if Finnhub gave nothing useful ---
    if base["eps_growth_yoy"] is None and base["status"] != "ok":
        logger.debug(
            "Fundamentals %s: Finnhub gave no EPS growth — trying Alpha Vantage fallback",
            ticker,
        )
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

    # --- EPS acceleration: current quarter's YoY growth > prior quarter's YoY growth ---
    # Requires both eps_growth_yoy (q0 vs q4) and eps_growth_prior_yoy (q1 vs q5).
    if (base["eps_growth_yoy"] is not None and
            base["eps_growth_prior_yoy"] is not None and
            base["eps_growth_yoy"] > base["eps_growth_prior_yoy"]):
        base["eps_accelerating"] = True

    # --- Compute scored additions (0–10) ---
    score = 0
    # EPS acceleration: +2 (master prompt specifies this explicitly)
    if base["eps_accelerating"]:
        score += settings.EPS_ACCELERATION_SCORE
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

    logger.debug(
        "Fundamentals %s: status=%s eps_growth_yoy=%s rev_growth_yoy=%s "
        "passes_hard_gates=%s score=%d",
        ticker,
        base["status"],
        f"{base['eps_growth_yoy']:.1f}%" if base["eps_growth_yoy"] is not None else "None",
        f"{base['rev_growth_yoy']:.1f}%" if base["rev_growth_yoy"] is not None else "None",
        base["passes_hard_gates"],
        base["fundamentals_score"],
    )
    return base


def _parse_quarterly(reports: list, base: dict) -> None:
    """
    Extract YoY EPS/revenue growth from Finnhub reported financials.

    Quarters indexed (sorted descending by period):
        q0 = most recent quarter
        q1 = one quarter prior (for acceleration: compare q0 growth vs q1 growth)
        q4 = same quarter last year (vs q0)
        q5 = same quarter last year vs q1 (for prior-year comparison)
    """
    try:
        # Sort by period descending — most recent first
        reports.sort(key=lambda r: r.get("period", ""), reverse=True)
        if len(reports) < 5:
            return

        _EPS_LABELS = ["eps", "earnings per share", "diluted eps",
                       "basic eps", "earningspersharediluted"]
        _REV_LABELS = ["revenue", "revenues", "net revenue",
                       "total revenue", "salesrevenuenet"]
        _GP_LABELS  = ["gross profit", "grossprofit"]

        def _find(report, names):
            """Search income statement concepts by label."""
            for concept in report.get("report", {}).get("ic", []):
                if concept.get("label", "").lower() in [n.lower() for n in names]:
                    return concept.get("value")
            return None

        q0 = reports[0]  # most recent quarter
        q4 = reports[4]  # same quarter last year

        eps_now = _find(q0, _EPS_LABELS)
        eps_ly  = _find(q4, _EPS_LABELS)
        rev_now = _find(q0, _REV_LABELS)
        rev_ly  = _find(q4, _REV_LABELS)
        gp_now  = _find(q0, _GP_LABELS)
        gp_ly   = _find(q4, _GP_LABELS)

        if eps_now is not None and eps_ly is not None and eps_ly != 0:
            base["eps_growth_yoy"] = (eps_now - eps_ly) / abs(eps_ly) * 100
        else:
            q0_labels = [c.get("label", "") for c in q0.get("report", {}).get("ic", [])]
            logger.debug(
                "Fundamentals %s: EPS label not matched. "
                "eps_now=%s eps_ly=%s | q0 IC labels (first 15): %s",
                base.get("ticker"), eps_now, eps_ly, q0_labels[:15],
            )

        if rev_now is not None and rev_ly is not None and rev_ly != 0:
            base["rev_growth_yoy"] = (rev_now - rev_ly) / abs(rev_ly) * 100
        else:
            logger.debug(
                "Fundamentals %s: revenue label not matched. rev_now=%s rev_ly=%s",
                base.get("ticker"), rev_now, rev_ly,
            )

        if gp_now is not None and rev_now and rev_now != 0:
            base["gross_margin_current"] = gp_now / rev_now * 100
        if gp_ly is not None and rev_ly and rev_ly != 0:
            base["gross_margin_prior"] = gp_ly / rev_ly * 100

        # --- EPS acceleration: compare current quarter's growth vs prior quarter's growth ---
        # Requires q1 (one quarter back) and q5 (same quarter last year as q1).
        if len(reports) >= 6:
            q1 = reports[1]  # one quarter prior
            q5 = reports[5]  # same quarter last year (vs q1)
            eps_q1    = _find(q1, _EPS_LABELS)
            eps_q5    = _find(q5, _EPS_LABELS)
            if eps_q1 is not None and eps_q5 is not None and eps_q5 != 0:
                base["eps_growth_prior_yoy"] = (eps_q1 - eps_q5) / abs(eps_q5) * 100

    except Exception as exc:
        logger.debug("Quarterly parse error for %s: %s", base.get("ticker"), exc)


def _alpha_vantage_fallback(ticker: str, base: dict) -> None:
    """
    Use Alpha Vantage as last-resort fallback (max 25 calls/day).

    Two separate endpoints are used because AV's INCOME_STATEMENT quarterly
    reports do NOT reliably contain reportedEPS — that field lives in the
    dedicated EARNINGS endpoint.  Mixing them caused EPS to always be None
    when Finnhub failed, silently poisoning the hard-gate check.

    Call order:
      1. EARNINGS  → EPS (reportedEPS), surprise pct
      2. INCOME_STATEMENT → revenue, gross profit (for margins)
    """

    def _float(d: dict, k: str) -> Optional[float]:
        v = d.get(k)
        if v and v not in ("None", ""):
            try:
                return float(v)
            except ValueError:
                pass
        return None

    # --- 1. EARNINGS endpoint: per-share EPS (quarterly) ---
    eps_data = _alpha_vantage_get("EARNINGS", {"symbol": ticker})
    if eps_data and "quarterlyEarnings" in eps_data:
        try:
            reports = eps_data["quarterlyEarnings"]
            if len(reports) >= 5:
                q0 = reports[0]   # most recent quarter
                q4 = reports[4]   # same quarter last year
                eps_now = _float(q0, "reportedEPS")
                eps_ly  = _float(q4, "reportedEPS")
                if eps_now is not None and eps_ly and eps_ly != 0:
                    base["eps_growth_yoy"] = (eps_now - eps_ly) / abs(eps_ly) * 100
                # Bonus: capture earnings surprise if available
                surprise = _float(q0, "surprisePercentage")
                if surprise is not None:
                    base["eps_surprise_pct"] = surprise
        except Exception as exc:
            logger.debug("Alpha Vantage EARNINGS parse error for %s: %s", ticker, exc)

    # --- 2. INCOME_STATEMENT endpoint: revenue + gross profit ---
    income_data = _alpha_vantage_get("INCOME_STATEMENT", {"symbol": ticker})
    if income_data and "quarterlyReports" in income_data:
        try:
            reports = income_data["quarterlyReports"]
            if len(reports) >= 5:
                q0 = reports[0]
                q4 = reports[4]
                rev_now = _float(q0, "totalRevenue")
                rev_ly  = _float(q4, "totalRevenue")
                gp_now  = _float(q0, "grossProfit")
                gp_ly   = _float(q4, "grossProfit")

                if rev_now is not None and rev_ly and rev_ly != 0:
                    base["rev_growth_yoy"] = (rev_now - rev_ly) / abs(rev_ly) * 100
                if gp_now is not None and rev_now and rev_now != 0:
                    base["gross_margin_current"] = gp_now / rev_now * 100
                if gp_ly is not None and rev_ly and rev_ly != 0:
                    base["gross_margin_prior"] = gp_ly / rev_ly * 100
        except Exception as exc:
            logger.debug("Alpha Vantage INCOME_STATEMENT parse error for %s: %s", ticker, exc)

    if base["eps_growth_yoy"] is not None or base["rev_growth_yoy"] is not None:
        base["status"] = "ok"


if __name__ == "__main__":
    from database.db import init_db
    logging.basicConfig(level=logging.INFO)
    init_db()
    result = fetch_fundamentals("AAPL")
    print(f"fundamentals.py: status={result['status']}, passes={result['passes_hard_gates']}")
    print(f"  EPS growth YoY: {result['eps_growth_yoy']}")
    print(f"  Revenue growth: {result['rev_growth_yoy']}")
