"""
SEC EDGAR free API integration — Section 9 (Weekly AI Management Quality).
No API key required. Uses the public EDGAR data APIs.

Required by SEC: identify your app in the User-Agent header.
  User-Agent: "MinerviniSEPA/1.0 contact@example.com"

Data sources:
  - CIK lookup:        https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&...
  - Company facts:     https://data.sec.gov/api/xbrl/companyfacts/{CIK}.json
  - Filing index:      https://data.sec.gov/submissions/{CIK}.json
  - 10-K filing text:  Full text from EDGAR filing viewer

Cached 7 days (fundamentals change slowly).
"""

import logging
import re
from typing import Optional

import requests

from data.cache import get as cache_get, set as cache_set, TTL_7D

logger = logging.getLogger(__name__)

_EDGAR_BASE = "https://data.sec.gov"
_EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index"

# Required by SEC fair-use policy
_HEADERS = {
    "User-Agent": "MinerviniSEPA/1.0 sepa-system@example.com",
    "Accept": "application/json",
}


# ---------------------------------------------------------------------------
# CIK lookup
# ---------------------------------------------------------------------------

def get_cik(ticker: str) -> Optional[str]:
    """
    Resolve a ticker symbol to its SEC CIK number (10-digit, zero-padded).
    Cached 7 days.
    """
    cache_key = f"sec_cik:{ticker.upper()}"
    cached = cache_get(cache_key)
    if cached:
        return str(cached)

    try:
        # EDGAR provides a bulk ticker→CIK map
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        ticker_upper = ticker.upper()
        for entry in data.values():
            if entry.get("ticker", "").upper() == ticker_upper:
                cik = str(entry["cik_str"]).zfill(10)
                cache_set(cache_key, cik, ttl_seconds=TTL_7D)
                return cik
    except Exception as exc:
        logger.warning("SEC CIK lookup failed for %s: %s", ticker, exc)
    return None


# ---------------------------------------------------------------------------
# Company financials from XBRL facts
# ---------------------------------------------------------------------------

def get_company_facts(ticker: str) -> Optional[dict]:
    """
    Fetch the full XBRL company facts from EDGAR for a ticker.
    Returns the raw JSON dict or None on failure.
    Cached 7 days.
    """
    cache_key = f"sec_facts:{ticker.upper()}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    cik = get_cik(ticker)
    if not cik:
        return None

    try:
        url = f"{_EDGAR_BASE}/api/xbrl/companyfacts/CIK{cik}.json"
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        facts = resp.json()
        cache_set(cache_key, facts, ttl_seconds=TTL_7D)
        return facts
    except Exception as exc:
        logger.warning("SEC company facts fetch failed for %s: %s", ticker, exc)
        return None


def get_annual_revenue_trend(ticker: str, years: int = 4) -> list[dict]:
    """
    Extract the last `years` annual revenue figures from EDGAR XBRL facts.
    Returns [{year: int, revenue: float}] sorted newest first.
    """
    facts = get_company_facts(ticker)
    if not facts:
        return []

    try:
        # Revenues are under us-gaap → Revenues or RevenueFromContractWithCustomerExcludingAssessedTax
        gaap = facts.get("facts", {}).get("us-gaap", {})
        revenue_key = None
        for key in ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                    "SalesRevenueNet", "SalesRevenueGoodsNet"]:
            if key in gaap:
                revenue_key = key
                break
        if not revenue_key:
            return []

        units = gaap[revenue_key].get("units", {}).get("USD", [])
        # Filter annual (10-K) filings only
        annual = [u for u in units if u.get("form") == "10-K" and u.get("fp") == "FY"]
        # Deduplicate by fiscal year end date, keep most recent filing per year
        by_year: dict[str, dict] = {}
        for u in annual:
            fy = u.get("end", "")[:4]  # YYYY
            if fy not in by_year or u["filed"] > by_year[fy]["filed"]:
                by_year[fy] = u
        result = [
            {"year": int(fy), "revenue": entry["val"]}
            for fy, entry in sorted(by_year.items(), reverse=True)
        ]
        return result[:years]
    except Exception as exc:
        logger.debug("Revenue trend parse failed for %s: %s", ticker, exc)
        return []


def get_gross_margin_trend(ticker: str, years: int = 4) -> list[dict]:
    """
    Compute annual gross margin trend from EDGAR XBRL facts.
    Returns [{year: int, gross_margin_pct: float}] sorted newest first.
    """
    facts = get_company_facts(ticker)
    if not facts:
        return []

    try:
        gaap = facts.get("facts", {}).get("us-gaap", {})

        def _extract_annual(key: str) -> dict[str, float]:
            """Extract annual filing values keyed by fiscal year."""
            if key not in gaap:
                return {}
            units = gaap[key].get("units", {}).get("USD", [])
            annual = [u for u in units if u.get("form") == "10-K" and u.get("fp") == "FY"]
            by_year: dict[str, dict] = {}
            for u in annual:
                fy = u.get("end", "")[:4]
                if fy not in by_year or u["filed"] > by_year[fy]["filed"]:
                    by_year[fy] = u
            return {fy: entry["val"] for fy, entry in by_year.items()}

        rev_key = next(
            (k for k in ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                          "SalesRevenueNet"] if k in gaap),
            None,
        )
        gp_key = next(
            (k for k in ["GrossProfit"] if k in gaap),
            None,
        )
        if not rev_key or not gp_key:
            return []

        revenues = _extract_annual(rev_key)
        gross_profits = _extract_annual(gp_key)

        result = []
        for fy in sorted(set(revenues) & set(gross_profits), reverse=True)[:years]:
            rev = revenues[fy]
            gp = gross_profits[fy]
            if rev > 0:
                result.append({"year": int(fy), "gross_margin_pct": round(gp / rev * 100, 1)})
        return result
    except Exception as exc:
        logger.debug("Gross margin trend parse failed for %s: %s", ticker, exc)
        return []


# ---------------------------------------------------------------------------
# 10-K MD&A text extraction
# ---------------------------------------------------------------------------

def get_latest_10k_mda(ticker: str, max_chars: int = 3000) -> Optional[str]:
    """
    Fetch the Management Discussion & Analysis section from the latest 10-K.
    Returns a plain-text excerpt (first max_chars characters).
    Cached 7 days.
    """
    cache_key = f"sec_mda:{ticker.upper()}"
    cached = cache_get(cache_key)
    if cached:
        return str(cached)

    cik = get_cik(ticker)
    if not cik:
        return None

    try:
        # Get filing submissions to find latest 10-K accession number
        sub_url = f"{_EDGAR_BASE}/submissions/CIK{cik}.json"
        resp = requests.get(sub_url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        submissions = resp.json()

        filings = submissions.get("filings", {}).get("recent", {})
        forms = filings.get("form", [])
        accessions = filings.get("accessionNumber", [])
        dates = filings.get("filingDate", [])

        # Find latest 10-K
        latest_10k = None
        latest_date = ""
        for form, acc, filed in zip(forms, accessions, dates):
            if form == "10-K" and filed > latest_date:
                latest_10k = acc
                latest_date = filed

        if not latest_10k:
            return None

        acc_clean = latest_10k.replace("-", "")
        text = _fetch_filing_text(int(cik), acc_clean, latest_10k)
        if text:
            mda = _extract_mda_section(text, max_chars)
            if mda:
                cache_set(cache_key, mda, ttl_seconds=TTL_7D)
                return mda

    except Exception as exc:
        logger.warning("SEC 10-K MD&A fetch failed for %s: %s", ticker, exc)
    return None


def _fetch_filing_text(cik_int: int, acc_clean: str, acc_dashed: str) -> Optional[str]:
    """
    Attempt to fetch the primary HTML/text document from a 10-K filing.
    Correctly resolves relative, root-relative, and absolute URLs using the
    EDGAR archive base path for this accession number.
    """
    archive_base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/"
    try:
        index_url = f"{archive_base}{acc_dashed}-index.htm"
        resp = requests.get(index_url, headers={**_HEADERS, "Accept": "text/html"}, timeout=10)
        resp.raise_for_status()

        # Extract all .htm links from the index page
        links = re.findall(r'href="([^"]*\.htm)"', resp.text, re.IGNORECASE)

        for link in links:
            # Skip table-of-contents or exhibit files
            link_lower = link.lower()
            if any(skip in link_lower for skip in ["exhibit", "ex-", "_ex", "toc"]):
                continue
            if any(x in link_lower for x in ["10k", "10-k", "annual", "form10", "doc", "filing"]):
                # Build absolute URL correctly regardless of whether link is:
                # - absolute:      https://www.sec.gov/...
                # - root-relative: /Archives/edgar/...
                # - relative:      aapl-20230930.htm
                if link.startswith("http"):
                    full_url = link
                elif link.startswith("/"):
                    full_url = f"https://www.sec.gov{link}"
                else:
                    full_url = archive_base + link

                doc_resp = requests.get(
                    full_url, headers={**_HEADERS, "Accept": "text/html"}, timeout=15
                )
                doc_resp.raise_for_status()
                # Strip HTML tags and normalise whitespace
                text = re.sub(r"<[^>]+>", " ", doc_resp.text)
                text = re.sub(r"\s+", " ", text).strip()
                if len(text) > 1000:
                    return text

    except Exception as exc:
        logger.debug("Filing text fetch failed for CIK %d / %s: %s", cik_int, acc_dashed, exc)
    return None


def _extract_mda_section(text: str, max_chars: int) -> Optional[str]:
    """
    Extract the Management Discussion & Analysis section from 10-K text.
    Looks for the MD&A header and returns text until the next section header.
    """
    mda_patterns = [
        r"MANAGEMENT.{0,30}DISCUSSION.{0,30}ANALYSIS",
        r"MD&A",
    ]
    next_section_patterns = [
        r"QUANTITATIVE.{0,30}QUALITATIVE",
        r"MARKET RISK",
        r"FINANCIAL STATEMENTS",
        r"ITEM\s+[3-9]",
    ]

    text_upper = text.upper()
    mda_start = -1
    for pattern in mda_patterns:
        match = re.search(pattern, text_upper)
        if match:
            mda_start = match.start()
            break

    if mda_start == -1:
        # No clear MD&A header — return first portion of text as fallback
        return text[:max_chars] if len(text) > 200 else None

    mda_text = text[mda_start:]
    # Find where the next section starts
    mda_end = len(mda_text)
    for pattern in next_section_patterns:
        match = re.search(pattern, mda_text[100:].upper())  # skip past header itself
        if match:
            mda_end = min(mda_end, match.start() + 100)

    excerpt = mda_text[:mda_end].strip()
    return excerpt[:max_chars] if excerpt else None


def get_sec_summary(ticker: str) -> dict:
    """
    High-level summary for a ticker: revenue trend + gross margin + MD&A snippet.
    Returns dict suitable for passing to AI management quality analysis.
    """
    revenue_trend = get_annual_revenue_trend(ticker)
    margin_trend = get_gross_margin_trend(ticker)
    mda_text = get_latest_10k_mda(ticker, max_chars=1500)

    # Build a compact text summary
    summary_parts = []

    if revenue_trend:
        rev_lines = [f"{r['year']}: ${r['revenue']/1e9:.1f}B" for r in revenue_trend]
        summary_parts.append("Revenue: " + " → ".join(rev_lines))

    if margin_trend:
        gm_lines = [f"{m['year']}: {m['gross_margin_pct']:.1f}%" for m in margin_trend]
        summary_parts.append("Gross margin: " + " → ".join(gm_lines))

    if mda_text:
        summary_parts.append(f"MD&A excerpt: {mda_text[:500]}")

    return {
        "ticker": ticker,
        "revenue_trend": revenue_trend,
        "margin_trend": margin_trend,
        "mda_excerpt": mda_text,
        "summary_text": "\n".join(summary_parts) if summary_parts else "SEC data unavailable",
    }


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=logging.INFO)

    summary = get_sec_summary("AAPL")
    print(f"sec_edgar.py: AAPL")
    print(f"  Revenue trend: {summary['revenue_trend']}")
    print(f"  Margin trend:  {summary['margin_trend']}")
    if summary["mda_excerpt"]:
        print(f"  MD&A (first 200 chars): {summary['mda_excerpt'][:200]}")
