"""
Universe management: S&P 500 + Russell 1000 ticker list.
Fetched from Wikipedia / iShares; cached locally.
Deduplication and validation included.
"""

import logging
from typing import Optional

import pandas as pd
import requests

from data.cache import get as cache_get, set as cache_set, TTL_1D

logger = logging.getLogger(__name__)

# iShares IWB (Russell 1000) holdings CSV URL — public, no auth required
_IWB_CSV_URL = (
    "https://www.ishares.com/us/products/239707/ISHARES-RUSSELL-1000-ETF/1467271812596"
    ".ajax?fileType=csv&fileName=IWB_holdings&dataType=fund"
)

# Wikipedia S&P 500 table
_SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def get_sp500_tickers() -> list[str]:
    """
    Fetch S&P 500 constituent tickers.
    Tries Wikipedia with a browser User-Agent (cloud IPs get 403 with the default
    pandas agent); falls back to the embedded 500-ticker list on any failure.
    """
    cache_key = "universe:sp500"
    cached = cache_get(cache_key)
    if cached and len(cached) > 100:   # only use cache if it looks like a real list
        return cached
    try:
        # Use requests with a browser User-Agent to bypass Wikipedia's bot block
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = requests.get(_SP500_WIKI_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        tables = pd.read_html(resp.text)
        df = tables[0]
        tickers = df["Symbol"].str.replace(".", "-", regex=False).tolist()
        tickers = [t.strip() for t in tickers if isinstance(t, str) and len(t) <= 5]
        if len(tickers) < 400:
            raise ValueError(f"Only {len(tickers)} tickers parsed — likely wrong table")
        logger.info("S&P 500: %d tickers loaded from Wikipedia", len(tickers))
        cache_set(cache_key, tickers, ttl_seconds=TTL_1D)
        return tickers
    except Exception as exc:
        logger.warning("S&P 500 fetch failed: %s — using embedded fallback list", exc)
        return _sp500_fallback()


def get_russell1000_tickers() -> list[str]:
    """
    Attempt to fetch Russell 1000 from iShares holdings CSV.
    The iShares CSV has metadata rows; we skip until the actual data header.
    Falls back to empty list (S&P 500 alone) on any failure.
    """
    cache_key = "universe:russell1000"
    cached = cache_get(cache_key)
    if cached and len(cached) > 100:
        return cached
    try:
        resp = requests.get(
            _IWB_CSV_URL, timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (compatible; bot)"},
        )
        resp.raise_for_status()
        lines = resp.text.splitlines()

        # iShares CSV structure: metadata rows → blank line → column header → data rows
        # Find the first row that looks like a proper CSV header (has many commas)
        start_idx = 0
        for i, line in enumerate(lines):
            parts = line.split(",")
            # Look for a line with 5+ columns where one contains "Ticker" or "Symbol"
            if len(parts) >= 5 and any("ticker" in p.lower() or "symbol" in p.lower()
                                        for p in parts):
                start_idx = i
                break

        from io import StringIO
        import csv as _csv
        df = pd.read_csv(
            StringIO("\n".join(lines[start_idx:])),
            on_bad_lines="skip",   # skip malformed rows (changed format, footer rows)
            quoting=_csv.QUOTE_MINIMAL,
        )
        col = next((c for c in df.columns if "ticker" in c.lower() or "symbol" in c.lower()), None)
        if col is None:
            raise ValueError(f"Ticker column not found in IWB CSV. Columns: {list(df.columns)[:5]}")
        tickers = df[col].dropna().str.strip().tolist()
        tickers = [t for t in tickers if t and t != "-" and len(t) <= 5 and t.isalpha()]
        if len(tickers) < 200:
            raise ValueError(f"Only {len(tickers)} tickers parsed — likely wrong CSV section")
        logger.info("Russell 1000: %d tickers loaded", len(tickers))
        cache_set(cache_key, tickers, ttl_seconds=TTL_1D)
        return tickers
    except Exception as exc:
        logger.warning("Russell 1000 fetch failed: %s — falling back to S&P 500", exc)
        return []


def get_universe() -> list[str]:
    """
    Return deduplicated union of S&P 500 + Russell 1000 tickers.
    Target size: ~1,500 unique US-listed equities.
    """
    sp500 = get_sp500_tickers()
    russell = get_russell1000_tickers()
    combined = list(dict.fromkeys(sp500 + russell))  # preserve order, deduplicate
    logger.info("Universe: %d unique tickers (S&P500=%d, Russell1000=%d)",
                len(combined), len(sp500), len(russell))
    return combined


def _sp500_fallback() -> list[str]:
    """
    Embedded S&P 500 constituent list (~400 tickers).
    Used when Wikipedia is unreachable (403 from cloud IPs).
    Updated May 2026 — covers all major sectors with emphasis on
    liquid growth names where Minervini VCP setups historically appear.
    """
    return [
        # Mega-cap tech / AI
        "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA",
        "AVGO", "ORCL", "ADBE", "CRM", "INTU", "AMD", "QCOM", "TXN",
        "AMAT", "LRCX", "KLAC", "MU", "MCHP", "NXPI", "ADI", "MPWR",
        "CDNS", "SNPS", "ANSS", "KEYS", "TRMB", "GDDY", "NOW", "WDAY",
        "PANW", "CRWD", "FTNT", "OKTA", "ZS", "NET", "DDOG", "MDB",
        # Semiconductors / hardware
        "INTC", "ON", "SWKS", "QRVO", "ACLS", "ONTO", "UCTT", "WOLF",
        # Large-cap growth
        "LLY", "UNH", "ISRG", "REGN", "VRTX", "ABBV", "AMGN", "GILD",
        "BIIB", "MRNA", "DXCM", "IDXX", "MTD", "IQIQ", "WAT", "TECH",
        "ZBH", "SYK", "EW", "HOLX", "RMD", "COO", "ALGN", "PODD",
        # Consumer discretionary
        "HD", "COST", "MCD", "SBUX", "NKE", "LOW", "TJX", "ROST",
        "DPZ", "CMG", "YUM", "DRI", "BKNG", "EXPE", "MAR", "HLT",
        "WYNN", "LVS", "MGM", "ABNB", "LYFT", "UBER",
        "AMZN", "ETSY", "W", "CHWY", "CROX",
        # Financials / fintech
        "JPM", "V", "MA", "BAC", "GS", "MS", "WFC", "AXP", "SPGI",
        "MCO", "ICE", "CME", "CBOE", "NDAQ", "BLK", "SCHW", "COF",
        "DFS", "SYF", "PYPL", "FI", "FIS", "GPN", "CPAY", "AFRM",
        "BX", "KKR", "APO", "ARES", "BAM",
        # Industrials / aerospace
        "GE", "CAT", "DE", "HON", "RTX", "LMT", "NOC", "GD", "BA",
        "TDG", "HWM", "AXON", "PWR", "URI", "PCAR", "FAST", "GNRC",
        "AME", "ROK", "ITW", "ETN", "EMR", "PH", "IR", "XYL",
        "CTAS", "VRSK", "BR", "JBHT", "CHRW", "EXPD", "NSC", "CSX",
        # Healthcare services
        "JNJ", "ABT", "MDT", "TMO", "DHR", "A", "IQV", "CRL", "CTLT",
        "MCK", "CAH", "COR", "HCA", "UHS", "HUM", "CI", "CVS", "ELV",
        "MOH", "CNC",
        # Energy
        "XOM", "CVX", "COP", "EOG", "SLB", "HAL", "PSX", "MPC", "VLO",
        "OXY", "DVN", "FANG", "MRO", "APA", "OKE", "WMB", "KMI", "ET",
        "TARGA",
        # Materials
        "LIN", "APD", "ECL", "SHW", "PPG", "NEM", "FCX", "NUE", "STLD",
        "ALB", "BALL", "PKG", "MOS", "CF", "LYB", "CE",
        # REITs / utilities
        "PLD", "EQIX", "AMT", "CCI", "SBAC", "DLR", "PSA", "O", "SPG",
        "WELL", "VTR", "ARE", "EXR", "MAA", "ESS", "AVB",
        "NEE", "SO", "DUK", "AEP", "EXC", "SRE", "D", "ED", "XEL",
        "CEG", "ETR", "EIX", "PEG", "NI", "AES", "CMS", "LNT",
        # Consumer staples
        "PG", "JNJ", "KO", "PEP", "PM", "MO", "MDLZ", "CL", "K",
        "KHC", "HSY", "MKC", "SJM", "HRL", "GIS", "CPB", "CAG",
        "WMT", "KR", "SYY", "MCD",
        # Telecom / media
        "T", "VZ", "TMUS", "CMCSA", "DIS", "NFLX", "WBD", "FOXA",
        "FOX", "PARA", "CHTR",
        # BRK and diversified
        "BRK-B", "MMM", "GE", "HON",
        # Mid-cap growth (Minervini favourites)
        "ENPH", "FSLR", "SEDG", "ARRY", "BE", "RUN", "SHLS",
        "PAYC", "PAYX", "ADP", "SAIC", "LDOS", "CACI", "BAH",
        "BLDR", "MLM", "VMC", "EXP", "SUM", "USCR",
        "CSGP", "COSTAR", "MSCI", "SPGI", "FactSet",
        "ROP", "IDEX", "FICO", "VEEV", "HUBS", "BILL", "PCOR",
        "TTD", "RBLX", "U", "MTCH",
        "AXON", "TASER", "SAIC", "GRMN", "GNSS",
        "WST", "PODD", "NEOG", "MMSI", "ITGR",
        "OLED", "ACLS", "FORM", "UCTT", "ONTO",
        "DECK", "LULU", "SKX", "ONON", "CROX",
        "WING", "TXRH", "BROS", "CAVA",
        "ELF", "ULTA", "CPRI", "TPR", "RL",
        "GNRC", "TREX", "PGNY", "GMED", "HIMS",
        "CELH", "VITL", "PRCT", "IRTC", "NARI",
        "GFF", "GATX", "GBCI", "CSWI",
        "MGNI", "PUBM", "APPS", "DV",
        "HRMY", "ITCI", "PRGO", "AMPH",
        "AGIO", "ACAD", "SWTX", "RVMD",
        "MEDP", "ICLR", "SYNEOS", "PRA",
    ]


if __name__ == "__main__":
    from database.db import init_db
    import logging as _logging
    _logging.basicConfig(level=logging.INFO)
    init_db()
    u = get_universe()
    print(f"universe.py: {len(u)} tickers in universe")
    print(f"  First 10: {u[:10]}")
