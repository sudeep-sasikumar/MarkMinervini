"""
Fundamental data fetcher.
Primary:   yfinance (free, no API key, no rate limits) — earningsGrowth, revenueGrowth, grossMargins
Fallback:  Finnhub /stock/metric + /stock/financials-reported (60 calls/min free tier)
Last resort: Alpha Vantage (25 calls/day)

Caching:
  - ok / partial result  → 7 days  (stable, no need to re-fetch)
  - unknown / no data    → 1 hour  (auto-retry; was 7 days before = stale trap)
  - Cache bypass:        → "unknown" entries from old 7-day cache are always
                            re-fetched so yfinance can succeed after a redeploy.

SECURITY: Finnhub API token is NEVER included in log messages (only status codes).
"""

import logging
import threading
import time
from collections import deque
from typing import Optional

import requests

from config import settings
from data.cache import get as cache_get, set as cache_set, TTL_7D, TTL_1H

logger = logging.getLogger(__name__)

_AV_CALLS_TODAY = 0

# ---------------------------------------------------------------------------
# Finnhub rate limiter — only used as fallback now that yfinance is primary.
# Per-second minimum gap added to prevent burst 429s (Finnhub free tier
# enforces ~1 call/second in addition to 60 calls/minute).
# ---------------------------------------------------------------------------
_fh_lock = threading.Lock()
_fh_call_times: deque = deque()
_fh_last_call: float = 0.0
_FH_MIN_INTERVAL = 1.2   # seconds between consecutive Finnhub calls


def _finnhub_get(endpoint: str, params: dict) -> Optional[dict]:
    """
    Rate-limited Finnhub API call.
    SECURITY: token is appended at request time and NEVER logged (status code only).
    """
    if not settings.FINNHUB_API_KEY:
        return None

    symbol = params.get("symbol", "?")

    with _fh_lock:
        # Per-second burst protection (free tier enforces ~1 RPS)
        global _fh_last_call
        now = time.time()
        gap = now - _fh_last_call
        if gap < _FH_MIN_INTERVAL:
            time.sleep(_FH_MIN_INTERVAL - gap)
        now = time.time()

        # Per-minute window rate limiting (sliding deque)
        while _fh_call_times and now - _fh_call_times[0] > 60.0:
            _fh_call_times.popleft()
        if len(_fh_call_times) >= settings.FINNHUB_MAX_CALLS_PER_MIN:
            wait = 60.0 - (now - _fh_call_times[0]) + 0.5
            if wait > 0:
                logger.debug("Finnhub rate limit: sleeping %.1fs", wait)
                time.sleep(wait)
            now = time.time()
            while _fh_call_times and now - _fh_call_times[0] > 60.0:
                _fh_call_times.popleft()

        _fh_call_times.append(time.time())
        _fh_last_call = time.time()

    url = f"https://finnhub.io/api/v1{endpoint}"
    call_params = dict(params)
    call_params["token"] = settings.FINNHUB_API_KEY  # appended here, never logged
    try:
        resp = requests.get(url, params=call_params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as http_err:
        # Log status code ONLY — never log URL (contains API token)
        status = http_err.response.status_code if http_err.response is not None else "?"
        logger.warning("Finnhub %s [%s] HTTP %s", endpoint, symbol, status)
        return None
    except Exception as exc:
        logger.warning("Finnhub %s [%s] error: %s", endpoint, symbol, type(exc).__name__)
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
        logger.warning("Alpha Vantage %s error: %s", function, type(exc).__name__)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_fundamentals(ticker: str) -> dict:
    """
    Return fundamental metrics for a ticker. Cache-backed.
    TTL: 7 days for good/partial data; 1 hour for unknown (auto-retry).
    Cache bypass: stale "unknown" entries from old 7-day cache are always
    refetched so a fresh deploy can immediately use yfinance.
    """
    cache_key = f"fundamentals:{ticker}"
    cached = cache_get(cache_key)
    if cached is not None:
        # Bypass stale "unknown" entries — yfinance may now succeed where
        # Finnhub previously failed. Good data (eps present) is always served.
        if cached.get("status") == "unknown" or cached.get("eps_growth_yoy") is None:
            pass  # fall through to rebuild
        else:
            return cached  # valid cached data

    result = _build_fundamentals(ticker)
    ttl = TTL_7D if result.get("status") in ("ok", "partial") else TTL_1H
    cache_set(cache_key, result, ttl_seconds=ttl)
    return result


def _build_fundamentals(ticker: str) -> dict:
    base: dict = {
        "ticker": ticker,
        "status": "unknown",
        "eps_growth_yoy": None,
        "eps_growth_prior_yoy": None,
        "eps_accelerating": False,
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

    # --- 1. Primary: yfinance (free, unlimited, no API key needed) ---
    _yfinance_fundamentals(ticker, base)

    # --- 2. Fallback: Finnhub (only if yfinance gave no EPS growth) ---
    if base["eps_growth_yoy"] is None:
        _finnhub_fundamentals(ticker, base)

    # --- 3. Last resort: Alpha Vantage (25 calls/day limit) ---
    if base["eps_growth_yoy"] is None and base["status"] != "ok":
        _alpha_vantage_fallback(ticker, base)

    # --- 4. Annual EPS as quarterly proxy (last resort) ---
    if base["eps_growth_yoy"] is None and base["eps_growth_annual"] is not None:
        logger.info(
            "Fundamentals %s: using 5Y annual EPS (%.1f%%) as quarterly proxy",
            ticker, base["eps_growth_annual"],
        )
        base["eps_growth_yoy"] = base["eps_growth_annual"]
        base["status"] = "partial"

    # --- Hard gates ---
    eps_ok = base["eps_growth_yoy"] is not None and base["eps_growth_yoy"] >= settings.EPS_GROWTH_MIN
    rev_ok = base["rev_growth_yoy"] is not None and base["rev_growth_yoy"] >= settings.REVENUE_GROWTH_MIN
    margin_ok = (
        base["gross_margin_current"] is not None and
        base["gross_margin_prior"] is not None and
        base["gross_margin_current"] >= base["gross_margin_prior"]
    )
    base["passes_hard_gates"] = eps_ok and rev_ok and margin_ok

    # --- EPS acceleration ---
    if (base["eps_growth_yoy"] is not None and
            base["eps_growth_prior_yoy"] is not None and
            base["eps_growth_yoy"] > base["eps_growth_prior_yoy"]):
        base["eps_accelerating"] = True

    # --- Scored additions (0–10) ---
    score = 0
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

    # Determine data source label for diagnostics
    _src = "none"
    if base.get("status") in ("ok", "partial"):
        if base.get("raw"):
            _src = "finnhub"
        else:
            _src = "yfinance"

    logger.debug(
        "Fundamentals %s [%s]: status=%s eps=%s rev=%s gm_now=%s gm_prior=%s "
        "roe=%s inst=%s passes=%s score=%d",
        ticker, _src, base["status"],
        f"{base['eps_growth_yoy']:+.1f}%" if base["eps_growth_yoy"] is not None else "None",
        f"{base['rev_growth_yoy']:+.1f}%" if base["rev_growth_yoy"] is not None else "None",
        f"{base['gross_margin_current']:.1f}%" if base["gross_margin_current"] is not None else "None",
        f"{base['gross_margin_prior']:.1f}%" if base["gross_margin_prior"] is not None else "None",
        f"{base['roe']:.1f}%" if base["roe"] is not None else "None",
        f"{base['institutional_own_pct']:.1f}%" if base["institutional_own_pct"] is not None else "None",
        base["passes_hard_gates"], base["fundamentals_score"],
    )
    if base["status"] == "unknown":
        logger.warning(
            "Fundamentals %s: ALL sources failed (yfinance + finnhub + alpha_vantage) "
            "— stock will be rejected at fundamentals gate",
            ticker,
        )
    return base


# ---------------------------------------------------------------------------
# Source 1: yfinance (PRIMARY)
# ---------------------------------------------------------------------------

def _yfinance_fundamentals(ticker: str, base: dict) -> None:
    """
    Populate fundamentals from Yahoo Finance via yfinance.
    Free, no API key, no rate limits. Provides earningsGrowth, revenueGrowth,
    and grossMargins which map directly to our quarterly YoY growth requirements.

    earningsGrowth  → quarterly EPS YoY (decimal, e.g. 0.25 = +25%)
    revenueGrowth   → quarterly revenue YoY (decimal)
    grossMargins    → TTM gross margin (decimal)
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}

        # EPS growth (quarterly YoY)
        for field in ("earningsGrowth", "earningsQuarterlyGrowth"):
            v = info.get(field)
            if v is not None and isinstance(v, (int, float)):
                base["eps_growth_yoy"] = round(float(v) * 100, 1)
                base["eps_growth_annual"] = round(float(v) * 100, 1)  # proxy
                break

        # Revenue growth (quarterly YoY)
        rg = info.get("revenueGrowth")
        if rg is not None and isinstance(rg, (int, float)):
            base["rev_growth_yoy"] = round(float(rg) * 100, 1)

        # Gross margins (TTM, decimal → %)
        gm = info.get("grossMargins")
        if gm is not None and isinstance(gm, (int, float)):
            base["gross_margin_current"] = round(float(gm) * 100, 1)

        # ROE (decimal → %)
        roe = info.get("returnOnEquity")
        if roe is not None and isinstance(roe, (int, float)):
            base["roe"] = round(float(roe) * 100, 1)

        # Institutional ownership (decimal → %)
        inst = info.get("heldPercentInstitutions")
        if inst is not None and isinstance(inst, (int, float)):
            base["institutional_own_pct"] = round(float(inst) * 100, 1)

        # If .info didn't provide EPS/revenue growth, parse quarterly financials directly.
        # Yahoo Finance's .info rarely has earningsGrowth populated for most S&P 500 stocks.
        if base["eps_growth_yoy"] is None or base["rev_growth_yoy"] is None:
            _yfinance_quarterly_growth(t, base)

        if base["eps_growth_yoy"] is not None or base["rev_growth_yoy"] is not None:
            base["status"] = "ok"
            logger.info(
                "Fundamentals %s [yfinance]: EPS=%s rev=%s GM=%s",
                ticker,
                f"{base['eps_growth_yoy']:+.1f}%" if base["eps_growth_yoy"] is not None else "n/a",
                f"{base['rev_growth_yoy']:+.1f}%" if base["rev_growth_yoy"] is not None else "n/a",
                f"{base['gross_margin_current']:.1f}%" if base["gross_margin_current"] is not None else "n/a",
            )
        else:
            logger.debug("Fundamentals %s: yfinance .info and quarterly stmt both returned no growth data", ticker)

    except Exception as exc:
        logger.debug("yfinance fundamentals error for %s: %s", ticker, exc)


def _yfinance_quarterly_growth(t, base: dict) -> None:
    """
    Parse quarterly_income_stmt (yfinance 0.2.x+) for EPS and revenue YoY growth.
    Called when .info dict doesn't have earningsGrowth / revenueGrowth — which is
    the case for most S&P 500 stocks on Yahoo Finance's current API.

    Strategy: compare most-recent quarter (iloc[0]) vs same quarter 1 year ago
    (iloc[4]) — four columns apart = exactly 12 months back.
    Requires at least 5 columns (quarters) of history.
    """
    try:
        # Try quarterly_income_stmt (newer yfinance) then quarterly_financials (older alias)
        qis = None
        for attr in ("quarterly_income_stmt", "quarterly_financials"):
            try:
                df = getattr(t, attr, None)
                if df is not None and not df.empty and df.shape[1] >= 5:
                    qis = df
                    break
            except Exception:
                pass
        if qis is None:
            return

        # Revenue YoY growth
        if base["rev_growth_yoy"] is None:
            for label in ("Total Revenue", "TotalRevenue", "Revenue"):
                if label in qis.index:
                    rev = qis.loc[label].dropna()
                    if len(rev) >= 5:
                        curr = float(rev.iloc[0])
                        prior = float(rev.iloc[4])
                        if prior != 0 and curr > 0:
                            base["rev_growth_yoy"] = round((curr - prior) / abs(prior) * 100, 1)
                    break

        # EPS YoY growth — prefer Diluted EPS, fall back to Net Income as proxy
        if base["eps_growth_yoy"] is None:
            for label in ("Diluted EPS", "DilutedEPS", "Basic EPS", "BasicEPS",
                          "Net Income Common Stockholders",
                          "Net Income", "NetIncome"):
                if label in qis.index:
                    row = qis.loc[label].dropna()
                    if len(row) >= 5:
                        curr = float(row.iloc[0])
                        prior = float(row.iloc[4])
                        if prior != 0:
                            base["eps_growth_yoy"] = round((curr - prior) / abs(prior) * 100, 1)
                    break

        # Prior gross margin (for YoY contraction check in fundamentals_filter)
        if base["gross_margin_prior"] is None:
            gp_lbl = next((l for l in ("Gross Profit", "GrossProfit") if l in qis.index), None)
            rv_lbl = next((l for l in ("Total Revenue", "TotalRevenue", "Revenue")
                           if l in qis.index), None)
            if gp_lbl and rv_lbl:
                gp_row = qis.loc[gp_lbl].dropna()
                rv_row = qis.loc[rv_lbl].dropna()
                if len(gp_row) >= 5 and len(rv_row) >= 5:
                    gp_prior = float(gp_row.iloc[4])
                    rv_prior = float(rv_row.iloc[4])
                    if rv_prior != 0:
                        base["gross_margin_prior"] = round(gp_prior / rv_prior * 100, 1)
                    # Current gross margin (overwrite .info value if more precise)
                    if base["gross_margin_current"] is None:
                        gp_curr = float(gp_row.iloc[0])
                        rv_curr = float(rv_row.iloc[0])
                        if rv_curr != 0:
                            base["gross_margin_current"] = round(gp_curr / rv_curr * 100, 1)

        # Prior-year EPS for acceleration check
        if base["eps_growth_prior_yoy"] is None and qis.shape[1] >= 6:
            for label in ("Diluted EPS", "DilutedEPS", "Basic EPS", "BasicEPS",
                          "Net Income Common Stockholders",
                          "Net Income", "NetIncome"):
                if label in qis.index:
                    row = qis.loc[label].dropna()
                    if len(row) >= 6:
                        curr_prior = float(row.iloc[1])  # Q-1 (one quarter back)
                        year_prior = float(row.iloc[5])  # same Q, 1 year before Q-1
                        if year_prior != 0:
                            base["eps_growth_prior_yoy"] = round(
                                (curr_prior - year_prior) / abs(year_prior) * 100, 1
                            )
                    break

    except Exception as exc:
        logger.debug("_yfinance_quarterly_growth error for %s: %s",
                     base.get("ticker"), exc)


# ---------------------------------------------------------------------------
# Source 2: Finnhub (FALLBACK — only when yfinance has no EPS data)
# ---------------------------------------------------------------------------

def _finnhub_fundamentals(ticker: str, base: dict) -> None:
    """
    Secondary fundamentals source: Finnhub /stock/metric + /stock/financials-reported.
    Only called when yfinance gave no useful EPS growth data.
    Token is never logged — only HTTP status codes appear in log output.
    """
    metrics_data = _finnhub_get("/stock/metric", {"symbol": ticker, "metric": "all"})
    if metrics_data and "metric" in metrics_data:
        m = metrics_data["metric"]
        base["raw"] = m
        base["status"] = "partial"

        # Fill in any fields yfinance didn't provide
        if base["roe"] is None:
            base["roe"] = m.get("roeTTM")
        if base["gross_margin_current"] is None:
            gm = m.get("grossMarginTTM")
            if gm is not None:
                base["gross_margin_current"] = gm
        if base["eps_growth_annual"] is None:
            base["eps_growth_annual"] = m.get("epsGrowth5Y")
        if base["institutional_own_pct"] is None:
            base["institutional_own_pct"] = m.get("institutionalOwnershipPercentage")

        # EPS/revenue growth fields (Finnhub naming varies by API version)
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

        for _gm_field in ("grossMarginAnnual", "grossMargin5Y"):
            _v = m.get(_gm_field)
            if _v is not None and base["gross_margin_prior"] is None:
                base["gross_margin_prior"] = float(_v)
                break

        if base["eps_growth_yoy"] is not None:
            logger.info(
                "Fundamentals %s [finnhub]: EPS=%+.1f%% rev=%s",
                ticker, base["eps_growth_yoy"],
                f"{base['rev_growth_yoy']:+.1f}%" if base["rev_growth_yoy"] is not None else "n/a",
            )

    fins = _finnhub_get("/stock/financials-reported",
                        {"symbol": ticker, "freq": "quarterly"})
    if fins and fins.get("data"):
        _parse_quarterly(fins["data"], base)
        if base["eps_growth_yoy"] is not None:
            base["status"] = "ok"


def _parse_quarterly(reports: list, base: dict) -> None:
    """Extract YoY EPS/revenue growth from Finnhub reported financials."""
    try:
        reports.sort(key=lambda r: r.get("period", ""), reverse=True)
        if len(reports) < 5:
            return

        _EPS_LABELS = ["eps", "earnings per share", "diluted eps",
                       "basic eps", "earningspersharediluted"]
        _REV_LABELS = ["revenue", "revenues", "net revenue",
                       "total revenue", "salesrevenuenet"]
        _GP_LABELS  = ["gross profit", "grossprofit"]

        def _find(report, names):
            for concept in report.get("report", {}).get("ic", []):
                if concept.get("label", "").lower() in names:
                    return concept.get("value")
            return None

        q0 = reports[0]
        q4 = reports[4]

        eps_now = _find(q0, _EPS_LABELS)
        eps_ly  = _find(q4, _EPS_LABELS)
        rev_now = _find(q0, _REV_LABELS)
        rev_ly  = _find(q4, _REV_LABELS)
        gp_now  = _find(q0, _GP_LABELS)
        gp_ly   = _find(q4, _GP_LABELS)

        if eps_now is not None and eps_ly is not None and eps_ly != 0:
            base["eps_growth_yoy"] = (eps_now - eps_ly) / abs(eps_ly) * 100
        if rev_now is not None and rev_ly is not None and rev_ly != 0:
            base["rev_growth_yoy"] = (rev_now - rev_ly) / abs(rev_ly) * 100
        if gp_now is not None and rev_now and rev_now != 0:
            base["gross_margin_current"] = gp_now / rev_now * 100
        if gp_ly is not None and rev_ly and rev_ly != 0:
            base["gross_margin_prior"] = gp_ly / rev_ly * 100

        if len(reports) >= 6:
            q1 = reports[1]
            q5 = reports[5]
            eps_q1 = _find(q1, _EPS_LABELS)
            eps_q5 = _find(q5, _EPS_LABELS)
            if eps_q1 is not None and eps_q5 is not None and eps_q5 != 0:
                base["eps_growth_prior_yoy"] = (eps_q1 - eps_q5) / abs(eps_q5) * 100

    except Exception as exc:
        logger.debug("Quarterly parse error for %s: %s", base.get("ticker"), exc)


# ---------------------------------------------------------------------------
# Source 3: Alpha Vantage (LAST RESORT — 25 calls/day)
# ---------------------------------------------------------------------------

def _alpha_vantage_fallback(ticker: str, base: dict) -> None:
    """Last-resort fallback. Two endpoints: EARNINGS for EPS, INCOME_STATEMENT for revenue."""

    def _float(d: dict, k: str) -> Optional[float]:
        v = d.get(k)
        if v and v not in ("None", ""):
            try:
                return float(v)
            except ValueError:
                pass
        return None

    eps_data = _alpha_vantage_get("EARNINGS", {"symbol": ticker})
    if eps_data and "quarterlyEarnings" in eps_data:
        try:
            rpts = eps_data["quarterlyEarnings"]
            if len(rpts) >= 5:
                eps_now = _float(rpts[0], "reportedEPS")
                eps_ly  = _float(rpts[4], "reportedEPS")
                if eps_now is not None and eps_ly and eps_ly != 0:
                    base["eps_growth_yoy"] = (eps_now - eps_ly) / abs(eps_ly) * 100
                surprise = _float(rpts[0], "surprisePercentage")
                if surprise is not None:
                    base["eps_surprise_pct"] = surprise
        except Exception as exc:
            logger.debug("Alpha Vantage EARNINGS parse for %s: %s", ticker, exc)

    income_data = _alpha_vantage_get("INCOME_STATEMENT", {"symbol": ticker})
    if income_data and "quarterlyReports" in income_data:
        try:
            rpts = income_data["quarterlyReports"]
            if len(rpts) >= 5:
                rev_now = _float(rpts[0], "totalRevenue")
                rev_ly  = _float(rpts[4], "totalRevenue")
                gp_now  = _float(rpts[0], "grossProfit")
                gp_ly   = _float(rpts[4], "grossProfit")
                if rev_now is not None and rev_ly and rev_ly != 0:
                    base["rev_growth_yoy"] = (rev_now - rev_ly) / abs(rev_ly) * 100
                if gp_now is not None and rev_now and rev_now != 0:
                    base["gross_margin_current"] = gp_now / rev_now * 100
                if gp_ly is not None and rev_ly and rev_ly != 0:
                    base["gross_margin_prior"] = gp_ly / rev_ly * 100
        except Exception as exc:
            logger.debug("Alpha Vantage INCOME_STATEMENT parse for %s: %s", ticker, exc)

    if base["eps_growth_yoy"] is not None or base["rev_growth_yoy"] is not None:
        base["status"] = "ok"


if __name__ == "__main__":
    from database.db import init_db
    import logging as _logging
    _logging.basicConfig(level=logging.INFO)
    init_db()
    result = fetch_fundamentals("AAPL")
    print(f"fundamentals.py: status={result['status']}, passes={result['passes_hard_gates']}")
    print(f"  EPS growth YoY: {result['eps_growth_yoy']}")
    print(f"  Revenue growth: {result['rev_growth_yoy']}")
    print(f"  Gross margin:   {result['gross_margin_current']}")
