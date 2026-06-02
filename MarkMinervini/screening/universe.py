"""
Universe management: S&P 500 + Russell 1000 ticker list.
Fetched from Wikipedia / iShares; cached locally.
Deduplication and validation included.
"""

import logging
import re
from typing import Optional   # noqa: F401 – used in _extract_ticker return type

import pandas as pd
import requests

from data.cache import get as cache_get, set as cache_set, TTL_1D

# Valid ticker: 1-5 uppercase letters optionally followed by -A/-B suffix.
# Allows BRK-B, BF-B while excluding numeric/CASH/footer rows in CSVs.
_TICKER_RE = re.compile(r'^[A-Z][A-Z0-9]{0,4}(-[A-Z0-9]{1,2})?$')

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
    Attempt to fetch Russell 1000 from iShares IWB holdings CSV.
    The iShares CSV has metadata rows; we skip until the actual data header.

    iShares occasionally changes the ticker column format (e.g. "NVDA US" instead
    of "NVDA").  _extract_ticker() handles multiple formats robustly.

    Falls back to a hardcoded mid-cap supplemental list (S&P 500 extensions) on
    any failure so the universe always includes ~900+ names.
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

        # iShares occasionally changes the ticker format:
        #   "NVDA"       → plain ticker ✓
        #   "NVDA US"    → ticker + exchange suffix (Bloomberg-style)
        #   "nvda"       → lowercase
        # _extract_ticker() handles all variants and returns the clean symbol.
        tickers = [_extract_ticker(v) for v in df[col].dropna()]
        tickers = [t for t in tickers if t is not None]

        if len(tickers) < 200:
            raise ValueError(
                f"Only {len(tickers)} tickers parsed after normalisation — "
                f"CSV format may have changed. Sample col values: "
                f"{df[col].dropna().head(5).tolist()}"
            )
        logger.info("Russell 1000: %d tickers loaded from iShares IWB", len(tickers))
        cache_set(cache_key, tickers, ttl_seconds=TTL_1D)
        return tickers
    except Exception as exc:
        logger.warning(
            "Russell 1000 iShares fetch failed: %s — using mid-cap supplemental list",
            exc,
        )
        return _russell1000_supplemental()


def _extract_ticker(val: object) -> Optional[str]:
    """
    Robustly extract a valid US ticker symbol from a cell value.

    Handles formats:
        "NVDA"        → "NVDA"
        "nvda"        → "NVDA"   (lowercase)
        "NVDA US"     → "NVDA"   (Bloomberg exchange suffix)
        "NVDA US EQ"  → "NVDA"   (Bloomberg full format)
        "BRK-B"       → "BRK-B"  (share-class hyphen)
        "BRK/B"       → "BRK-B"  (alternate share-class separator)
        " NVDA "      → "NVDA"   (extra whitespace)
    Returns None if no valid ticker can be extracted.
    """
    if not isinstance(val, str):
        return None
    v = val.strip().upper()
    # Normalise alternate share-class separators
    v = v.replace("/", "-")
    # Strategy 1: whole cell is already a valid ticker
    if _TICKER_RE.match(v):
        return v
    # Strategy 2: first whitespace-delimited token (handles "NVDA US", "NVDA US EQ")
    first = v.split()[0] if v else ""
    if first and _TICKER_RE.match(first):
        return first
    return None


def _russell1000_supplemental() -> list[str]:
    """
    Hardcoded mid-cap supplemental list used when the iShares IWB CSV fails.
    Contains ~350 high-quality Russell 1000 names NOT typically in the S&P 500
    that are prime candidates for Minervini VCP setups: liquid growth stocks,
    strong-sector mid-caps, and emerging large-caps.
    Updated May 2026.
    """
    return [
        # High-growth software / SaaS
        # Removed: SMAR (taken private 2024), ALTR (acq. SoftBank 2025)
        #          JAMF (taken private Francisco Partners 2023), CFLT (Confluent, delisted 2025)
        "HUBS", "DDOG", "BILL", "PCOR", "DOCN", "GTLB", "PATH",
        "NCNO", "BRZE", "ESTC", "MQ", "PEGA", "TOST",
        "APPF", "APPN", "ASAN", "MNDY", "NTNX", "WEX",
        # Cybersecurity
        # Removed: SCWX (SecureWorks, acq. 2023), CYBR (CyberArk, ticker change 2025)
        "S", "TENB", "RPD", "QLYS", "SAIL", "VRNS",
        # Semiconductors / equipment (mid-cap)
        # Removed: WOLF (Wolfspeed, bankruptcy/delisted 2025)
        "ACLS", "ONTO", "FORM", "UCTT", "AMBA", "AEHR",
        "SITM", "SMTC", "OLED", "ALGM", "AIOT",
        # Biotech / life science tools
        "RXRX", "KYMR", "RCUS", "ARVN", "VKTX", "KRYS", "RVMD",
        # Removed: ITCI (acq. J&J 2025), NARI (Inari Medical, acq. BD 2024)
        #          AKRO (Akero Therapeutics, acquired 2025), VERV (Verve Therapeutics, acquired 2025)
        "PTGX", "GPCR", "PRCT", "IRTC", "HRMY",
        "INVA", "HALO", "IMVT", "PRGO", "BEAM", "EDIT",
        "NTLA", "ARKG", "FATE", "IOVA",
        "MEDP", "ICLR", "ICON", "TXG", "PACB", "VEEV",
        # Consumer brands / retail
        # Removed: SKX (Skechers, taken private 3G Capital 2025)
        "DECK", "ONON", "LULU", "CROX", "BIRK",
        "WING", "TXRH", "BROS", "CAVA", "DNUT",
        "ELF", "ULTA", "CPRI", "RVLV",
        "XPOF", "HIMS", "WW",
        # Industrial / clean energy / infrastructure
        "ENPH", "FSLR", "ARRY", "BE", "SHLS",
        # Removed: DOOR (Masonite, acq. Owens Corning 2024)
        "BLDR", "TREX", "AAON", "WMS",
        # Removed: CSWI (CSW Industrials, acquired 2025)
        "AXON", "RGEN",
        "MGNI", "DV",
        # Financial / fintech
        "AFRM", "SOFI", "UPST", "LC", "OPEN",
        "OWL", "ARES", "BX", "KKR", "APO",
        "HOOD", "MKTX", "GCMG",
        # Healthcare services / devices
        "PGNY", "GMED", "PODD", "NVST", "LMAT",
        "MMSI", "ITGR", "NEOG", "INSP", "OMCL",
        # Removed: AMED (Amedisys, acq. UnitedHealth/Optum 2023)
        "ELAN", "PCRX", "AHCO",
        # Specialty / misc
        # Removed: PRFT (Perficient, acq. EQT 2023)
        "EXLS", "EPAM", "GLOB", "TTEK",
        "GATX", "GBCI", "WABC", "BUSE", "HOPE",
        # Fixed: IDACORP → IDA (correct NYSE ticker for IDACORP Inc.)
        "MGEE", "OTTR", "IDA",
        "CELH", "VITL", "POWL", "IESC",
        "PATK", "GFF",
        # Media / entertainment / gaming
        # Removed: ZNGA (Zynga, acq. Take-Two 2022); MGNI already in Industrial section
        "RBLX", "U", "TTWO", "DKNG",
        "TTD", "PUBM", "APPS",
        "FUTU", "TIGR",
        # REITs (growth-oriented)
        "CWAN", "IIPR", "COLD", "REXR", "NNN",
    ]


# ---------------------------------------------------------------------------
# Ticker blacklist — delisted, renamed, or single-letter tickers that appear
# in scraped lists but have no valid yfinance data.  Removing them eliminates
# retry penalties (each adds ~5s via Finnhub fallback + 403) and keeps logs clean.
# ---------------------------------------------------------------------------
_TICKER_BLACKLIST: set[str] = {
    "Q",      # Quintiles → merged into IQV (IQVIA) 2017; appears in some Wikipedia tables
}


def get_universe() -> list[str]:
    """
    Return deduplicated union of S&P 500 + Russell 1000 tickers.
    Target size: ~1,500 unique US-listed equities.
    """
    sp500 = get_sp500_tickers()
    russell = get_russell1000_tickers()
    combined = list(dict.fromkeys(sp500 + russell))  # preserve order, deduplicate
    # Remove known-bad tickers (delisted, renamed) that waste time on retries
    before = len(combined)
    combined = [t for t in combined if t not in _TICKER_BLACKLIST]
    if len(combined) < before:
        logger.info("Removed %d blacklisted ticker(s): %s",
                    before - len(combined),
                    sorted(_TICKER_BLACKLIST & set(sp500 + russell)))
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
        "CSGP", "MSCI", "SPGI", "FDS",  # COSTAR→CSGP already; FactSet→FDS; TASER→AXON
        "ROP", "IDEX", "FICO", "VEEV", "HUBS", "BILL", "PCOR",
        "TTD", "RBLX", "U", "MTCH",
        "AXON", "GRMN",
        "WST", "PODD", "NEOG", "MMSI", "ITGR",
        "OLED", "ACLS", "FORM", "UCTT", "ONTO",
        "DECK", "LULU", "ONON", "CROX",  # SKX taken private 2025
        "WING", "TXRH", "BROS", "CAVA",
        "ELF", "ULTA", "CPRI", "TPR", "RL",
        "GNRC", "TREX", "PGNY", "GMED", "HIMS",
        "CELH", "VITL", "PRCT", "IRTC",  # NARI acq. BD 2024
        "GFF", "GATX", "GBCI",           # CSWI acquired 2025
        "MGNI", "PUBM", "APPS", "DV",
        "HRMY", "PRGO", "AMPH",  # ITCI acq. J&J 2025
        "AGIO", "ACAD", "SWTX", "RVMD",
        "MEDP", "ICLR",  # SYNEOS taken private KKR 2023
    ]


if __name__ == "__main__":
    from database.db import init_db
    import logging as _logging
    _logging.basicConfig(level=logging.INFO)
    init_db()
    u = get_universe()
    print(f"universe.py: {len(u)} tickers in universe")
    print(f"  First 10: {u[:10]}")
