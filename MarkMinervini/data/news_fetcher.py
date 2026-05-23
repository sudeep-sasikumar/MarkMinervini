"""
News fetcher via Finnhub.
Company news: /company-news (last 30 days, sentiment score included).
Market/macro news: /news?category=general.
Results cached 6 hours.
"""

import logging
from datetime import date, timedelta
from typing import Optional

import requests

from config import settings
from data.cache import get as cache_get, set as cache_set, TTL_6H

logger = logging.getLogger(__name__)


def _finnhub_get(endpoint: str, params: dict) -> Optional[dict | list]:
    if not settings.FINNHUB_API_KEY:
        return None
    params["token"] = settings.FINNHUB_API_KEY
    try:
        resp = requests.get(
            f"https://finnhub.io/api/v1{endpoint}",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Finnhub news fetch failed (%s): %s", endpoint, exc)
        return None


def fetch_company_news(ticker: str, days: int = 30) -> list[dict]:
    """
    Return recent news articles for a ticker.
    Each article: {datetime, headline, summary, source, url, sentiment (−1 to +1)}
    Cached 6 hours.
    """
    cache_key = f"news:{ticker}:{days}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    today = date.today()
    from_date = (today - timedelta(days=days)).isoformat()
    raw = _finnhub_get(
        "/company-news",
        {"symbol": ticker, "from": from_date, "to": today.isoformat()},
    )
    articles = []
    if isinstance(raw, list):
        for item in raw:
            articles.append({
                "datetime": item.get("datetime"),
                "headline": item.get("headline", ""),
                "summary": item.get("summary", ""),
                "source": item.get("source", ""),
                "url": item.get("url", ""),
                "sentiment": item.get("sentiment", 0.0),
            })

    cache_set(cache_key, articles, ttl_seconds=TTL_6H)
    return articles


def fetch_market_news(category: str = "general", count: int = 20) -> list[dict]:
    """
    Return macro/market-wide news from Finnhub.
    Cached 6 hours.
    """
    cache_key = f"market_news:{category}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    raw = _finnhub_get("/news", {"category": category, "minId": 0})
    articles = []
    if isinstance(raw, list):
        for item in raw[:count]:
            articles.append({
                "datetime": item.get("datetime"),
                "headline": item.get("headline", ""),
                "summary": item.get("summary", ""),
                "source": item.get("source", ""),
                "url": item.get("url", ""),
                "category": item.get("category", ""),
            })

    cache_set(cache_key, articles, ttl_seconds=TTL_6H)
    return articles


def format_news_for_ai(articles: list[dict], max_articles: int = 5) -> str:
    """Format the most recent N articles as a text block suitable for AI prompting."""
    texts = []
    for art in articles[:max_articles]:
        headline = art.get("headline", "")
        summary = art.get("summary", "")[:200]
        texts.append(f"• {headline}: {summary}")
    return "\n".join(texts) if texts else "No recent news available."


if __name__ == "__main__":
    from database.db import init_db
    logging.basicConfig(level=logging.INFO)
    init_db()
    news = fetch_company_news("AAPL", days=7)
    print(f"news_fetcher.py: {len(news)} articles for AAPL")
    if news:
        print(f"  Latest: {news[0]['headline'][:80]}")
