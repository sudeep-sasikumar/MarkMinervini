"""
AI-powered analysis via Ollama (llama3.2:3b, runs locally).
Graceful degradation: if Ollama is unreachable, all tasks return a fallback dict
and signals are still sent flagged with "AI analysis offline".

Four tasks (Section 9):
  1. Earnings quality check
  2. News catalyst assessment
  3. Sector leadership analysis (daily)
  4. Management quality from SEC 10-K (weekly)
"""

import json
import logging
import re
from typing import Optional

import requests

from config import settings
from data.news_fetcher import fetch_company_news, format_news_for_ai
from data.cache import get as cache_get, set as cache_set, TTL_7D, TTL_1D

logger = logging.getLogger(__name__)

_OLLAMA_AVAILABLE: Optional[bool] = None  # cached after first check


# ---------------------------------------------------------------------------
# Ollama availability
# ---------------------------------------------------------------------------

def _check_ollama() -> bool:
    """
    Check that Ollama is reachable AND the configured model is available.
    Caches result; call is_ai_online() to force a recheck.
    """
    global _OLLAMA_AVAILABLE
    if _OLLAMA_AVAILABLE is not None:
        return _OLLAMA_AVAILABLE
    try:
        resp = requests.get(f"{settings.OLLAMA_URL}/api/tags", timeout=5)
        if resp.status_code != 200:
            raise ConnectionError(f"HTTP {resp.status_code}")
        # Confirm the model is actually pulled
        tags = resp.json()
        models = [m.get("name", "") for m in tags.get("models", [])]
        model_available = any(
            settings.OLLAMA_MODEL in m or m.startswith(settings.OLLAMA_MODEL.split(":")[0])
            for m in models
        )
        if not model_available:
            logger.warning(
                "Ollama reachable but model '%s' not found (available: %s) — AI offline",
                settings.OLLAMA_MODEL, models
            )
            _OLLAMA_AVAILABLE = False
        else:
            _OLLAMA_AVAILABLE = True
    except Exception as exc:
        logger.warning("Ollama availability check failed (%s) — AI analysis skipped", exc)
        _OLLAMA_AVAILABLE = False
    return _OLLAMA_AVAILABLE


def _ollama_generate(prompt: str) -> Optional[str]:
    """Send a prompt to Ollama and return the response text."""
    if not _check_ollama():
        return None
    try:
        resp = requests.post(
            f"{settings.OLLAMA_URL}/api/generate",
            json={
                "model": settings.OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 300},
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")
    except Exception as exc:
        logger.warning("Ollama generate failed: %s", exc)
        return None


def _parse_json_response(text: Optional[str], fallback: dict) -> dict:
    """Extract the first JSON object from an LLM response string."""
    if not text:
        return fallback
    # Find outermost {...} block
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return fallback


_OFFLINE_RESULT = {
    "offline": True,
    "summary": "AI analysis offline — manual review recommended",
}


# ---------------------------------------------------------------------------
# Task 1 — Earnings Quality
# ---------------------------------------------------------------------------

def analyse_earnings_quality(ticker: str, earnings_text: str) -> dict:
    """
    Assess whether EPS growth is genuine or driven by one-time items / buybacks.
    Returns JSON-shaped dict.
    """
    cache_key = f"ai_earnings:{ticker}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    if not _check_ollama():
        return _OFFLINE_RESULT

    prompt = (
        f"Analyse this earnings report summary for {ticker}. "
        "Determine if the EPS growth is from: "
        "(a) genuine operating revenue growth, (b) cost-cutting only, "
        "(c) one-time items such as asset sales or tax benefits, "
        "or (d) share buybacks inflating EPS artificially. "
        "Note if management raised or lowered guidance. "
        "Reply ONLY in this JSON format: "
        '{"genuine_growth": bool, "one_time_items": bool, "buyback_driven": bool, '
        '"guidance": "raised"|"lowered"|"maintained"|"unknown", '
        '"confidence": "high"|"medium"|"low", "summary": "max 30 words"} '
        f"Summary: {earnings_text[:800]}"
    )
    raw = _ollama_generate(prompt)
    result = _parse_json_response(raw, {
        "genuine_growth": True,
        "one_time_items": False,
        "buyback_driven": False,
        "guidance": "unknown",
        "confidence": "low",
        "summary": "Could not parse AI response",
    })
    cache_set(cache_key, result, ttl_seconds=TTL_7D)
    return result


# ---------------------------------------------------------------------------
# Task 2 — News Catalyst Assessment
# ---------------------------------------------------------------------------

def analyse_news_catalyst(ticker: str) -> dict:
    """
    Review recent news headlines to identify catalysts or risks.
    Returns JSON-shaped dict.
    """
    cache_key = f"ai_news:{ticker}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    if not _check_ollama():
        return _OFFLINE_RESULT

    articles = fetch_company_news(ticker, days=settings.FINNHUB_NEWS_DAYS)
    news_text = format_news_for_ai(articles, max_articles=5)

    prompt = (
        f"Review these headlines and summaries for {ticker}. "
        "Is there a positive fundamental catalyst (contract, product launch, "
        "guidance upgrade, FDA approval)? "
        "Is there any negative risk (lawsuit, recall, regulatory issue, "
        "executive departure)? "
        "Reply ONLY in JSON: "
        '{"catalyst": bool, "catalyst_type": "str", "risk": bool, "risk_type": "str", '
        '"sentiment": "BULLISH"|"NEUTRAL"|"BEARISH", "summary": "max 30 words"} '
        f"Headlines: {news_text}"
    )
    raw = _ollama_generate(prompt)
    result = _parse_json_response(raw, {
        "catalyst": False,
        "catalyst_type": "",
        "risk": False,
        "risk_type": "",
        "sentiment": "NEUTRAL",
        "summary": "Could not parse AI response",
    })
    cache_set(cache_key, result, ttl_seconds=TTL_1D)
    return result


# ---------------------------------------------------------------------------
# Task 3 — Sector Leadership (daily)
# ---------------------------------------------------------------------------

def analyse_sector_leadership(sector_performance: dict) -> dict:
    """
    Identify leading, declining, and rotating sectors.
    sector_performance: {sector_name: {"1m_pct": float, "3m_pct": float}}
    """
    cache_key = "ai_sector_leadership"
    cached = cache_get(cache_key)
    if cached:
        return cached

    if not _check_ollama():
        return _OFFLINE_RESULT

    perf_text = json.dumps(sector_performance, indent=2)
    prompt = (
        "Based on these sector ETF performances over the last month and 3 months, "
        "identify: (1) top 3 leading sectors, (2) declining sectors, "
        "(3) any rotation occurring. "
        "Reply ONLY in JSON: "
        '{"leading": ["list"], "declining": ["list"], '
        '"rotation_notes": "max 40 words"} '
        f"Data: {perf_text}"
    )
    raw = _ollama_generate(prompt)
    result = _parse_json_response(raw, {
        "leading": [],
        "declining": [],
        "rotation_notes": "Could not parse AI response",
    })
    cache_set(cache_key, result, ttl_seconds=TTL_1D)
    return result


# ---------------------------------------------------------------------------
# Task 4 — Management Quality from SEC 10-K
# ---------------------------------------------------------------------------

def analyse_management_quality(ticker: str, annual_report_text: str) -> dict:
    """
    Assess management quality from 10-K MD&A language.
    Returns JSON-shaped dict. Cached 7 days.
    """
    cache_key = f"ai_mgmt:{ticker}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    if not _check_ollama():
        return _OFFLINE_RESULT

    prompt = (
        f"Assess this company's management quality from their annual report language for {ticker}. "
        "Evaluate: (1) is guidance specific and quantified?, "
        "(2) are risks addressed honestly?, "
        "(3) is capital allocation sensible (R&D, buybacks, debt)?, "
        "(4) is tone substance-based? "
        "Reply ONLY in JSON: "
        '{"rating": "STRONG"|"ADEQUATE"|"WEAK", "rationale": "max 40 words"} '
        f"Text: {annual_report_text[:1200]}"
    )
    raw = _ollama_generate(prompt)
    result = _parse_json_response(raw, {
        "rating": "ADEQUATE",
        "rationale": "Could not parse AI response",
    })
    cache_set(cache_key, result, ttl_seconds=TTL_7D)
    return result


def is_ai_online() -> bool:
    """Return True if Ollama is reachable."""
    global _OLLAMA_AVAILABLE
    _OLLAMA_AVAILABLE = None  # force recheck
    return _check_ollama()


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=logging.INFO)
    online = is_ai_online()
    print(f"ai_analyst.py: Ollama online = {online}")
    if online:
        result = analyse_news_catalyst("AAPL")
        print(f"  AAPL news sentiment: {result.get('sentiment')}")
        print(f"  summary: {result.get('summary')}")
