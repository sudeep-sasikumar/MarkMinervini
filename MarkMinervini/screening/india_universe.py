"""
India stock universe builder — NSE-listed equities.

Covers the five indices the user requested:
  • Nifty 500      (top 500 NSE stocks by market cap)
  • Nifty Midcap 50
  • BSE MidCap     (NSE equivalents of BSE MidCap constituents)
  • Nifty Smallcap 50
  • BSE SmallCap   (select NSE-listed smallcaps)

All tickers use the yfinance '.NS' suffix (NSE format).
The list is reviewed and updated periodically — run a scan to verify
coverage before trading.

Fetch strategy:
  1. Try to download the official Nifty 500 CSV from NSE (requires cookie;
     fails on cloud IPs most of the time).
  2. Fall back to the hardcoded lists below.
"""

import logging
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Nifty 50  (50 stocks — flagship large-cap index)
# ---------------------------------------------------------------------------
_NIFTY_50: list[str] = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "BHARTIARTL.NS", "ICICIBANK.NS",
    "INFY.NS", "SBIN.NS", "ITC.NS", "BAJFINANCE.NS", "KOTAKBANK.NS",
    "LT.NS", "MARUTI.NS", "AXISBANK.NS", "ASIANPAINT.NS", "SUNPHARMA.NS",
    "TITAN.NS", "ULTRACEMCO.NS", "NESTLEIND.NS", "WIPRO.NS", "ONGC.NS",
    "NTPC.NS", "POWERGRID.NS", "COALINDIA.NS", "BAJAJFINSV.NS", "BPCL.NS",
    # TATAMOTORS.NS removed: HTTP 404 "Quote not found" from Yahoo Finance API
    # (likely due to 2024 CV demerger restructuring; add back once yfinance resolves).
    "HEROMOTOCO.NS", "APOLLOHOSP.NS", "TECHM.NS", "HCLTECH.NS",
    "JSWSTEEL.NS", "TATASTEEL.NS", "BAJAJ-AUTO.NS", "CIPLA.NS", "DRREDDY.NS",
    "EICHERMOT.NS", "GRASIM.NS", "HINDALCO.NS", "INDUSINDBK.NS", "M&M.NS",
    "TATACONSUM.NS", "BRITANNIA.NS", "DIVISLAB.NS", "ADANIENT.NS", "ADANIPORTS.NS",
    "SHRIRAMFIN.NS", "BEL.NS", "TRENT.NS", "HINDUNILVR.NS", "ZOMATO.NS",
]

# ---------------------------------------------------------------------------
# Nifty Midcap 50  (50 stocks)
# ---------------------------------------------------------------------------
_NIFTY_MIDCAP_50: list[str] = [
    "ABB.NS", "ABCAPITAL.NS", "ASTRAL.NS", "AUBANK.NS", "BERGEPAINT.NS",
    "BOSCHLTD.NS", "CHOLAFIN.NS", "COFORGE.NS", "CUMMINSIND.NS", "DLF.NS",
    "GODREJPROP.NS", "HAVELLS.NS", "IDFCFIRSTB.NS", "INDHOTEL.NS", "IRCTC.NS",
    "JIOFIN.NS", "KALYANKJIL.NS", "KPITTECH.NS", "LICHSGFIN.NS", "LUPIN.NS",
    "MANKIND.NS", "MPHASIS.NS", "MUTHOOTFIN.NS", "NAUKRI.NS", "OBEROIRLTY.NS",
    "PAGEIND.NS", "PERSISTENT.NS", "PIIND.NS", "POLYCAB.NS", "RECLTD.NS",
    "SIEMENS.NS", "SOLARINDS.NS", "SUPREMEIND.NS", "TORNTPHARM.NS", "TVSMOTOR.NS",
    "VBL.NS", "ZYDUSLIFE.NS", "ATGL.NS", "HDFCAMC.NS", "GODREJCP.NS",
    "ADANIGREEN.NS", "BANKBARODA.NS", "GAIL.NS", "INDIGO.NS", "PGHH.NS",
    "SUNTV.NS", "UNIONBANK.NS", "VEDL.NS", "BIKAJI.NS", "JSWINFRA.NS",
]

# ---------------------------------------------------------------------------
# BSE MidCap — additional mid-caps not already in Nifty Midcap 50
# ---------------------------------------------------------------------------
_BSE_MIDCAP_EXTRA: list[str] = [
    "AMBUJACEM.NS", "BLUEDART.NS", "CROMPTON.NS", "DABUR.NS", "EMAMILTD.NS",
    "ESCORTS.NS", "GRINDWELL.NS", "HAL.NS", "INDUSTOWER.NS", "JYOTHYLAB.NS",
    "LATENTVIEW.NS", "MARICO.NS", "NBCC.NS", "NMDC.NS", "COLPAL.NS",
    "THERMAX.NS", "TATAPOWER.NS", "TORNTPOWER.NS", "VOLTAS.NS", "PNB.NS",
    "UNITDSPR.NS", "SAIL.NS", "HFCL.NS", "RATNAMANI.NS", "WHIRLPOOL.NS",
    "BAJAJHLDNG.NS", "CONCOR.NS", "GICRE.NS", "IREDA.NS", "SJVN.NS",
]

# ---------------------------------------------------------------------------
# Nifty Smallcap 50  (50 stocks)
# ---------------------------------------------------------------------------
_NIFTY_SMALLCAP_50: list[str] = [
    "AIAENG.NS", "APLAPOLLO.NS", "BEML.NS", "BHEL.NS", "CAPLIPOINT.NS",
    "CMSINFO.NS", "DEEPAKFERT.NS", "DEVYANI.NS", "ELGIEQUIP.NS", "FINPIPE.NS",
    "GABRIEL.NS", "GOCOLORS.NS", "HAPPSTMNDS.NS", "JBCHEPHARM.NS", "KFINTECH.NS",
    "KRBL.NS", "MAHLIFE.NS", "MATRIMONY.NS", "MEDPLUS.NS", "NAVINFLUOR.NS",
    "NHPC.NS", "NUVAMA.NS", "OLECTRA.NS", "PCBL.NS", "PRINCEPIPE.NS",
    "RVNL.NS", "SAPPHIRE.NS", "SUZLON.NS", "TANLA.NS",
    "THYROCARE.NS", "UJJIVANSFB.NS", "UTIAMC.NS", "VSTIND.NS", "WABAG.NS",
    "WELCORP.NS", "APTUS.NS", "CERA.NS", "CLEAN.NS", "DEEPAKNTR.NS",
    "JLHL.NS", "MOIL.NS", "NATIONALUM.NS", "NILKAMAL.NS",
    "ORIENTBELL.NS", "RELAXO.NS", "RPGLIFE.NS", "VESUVIUS.NS", "VOLTAMP.NS",
    "DELHIVERY.NS", "RAILTEL.NS",
]

# ---------------------------------------------------------------------------
# BSE SmallCap additional selections
# ---------------------------------------------------------------------------
_BSE_SMALLCAP_EXTRA: list[str] = [
    "ALKYLAMINE.NS", "BALRAMCHIN.NS", "CHAMBLFERT.NS", "DCBBANK.NS", "DELTACORP.NS",
    "ESTER.NS", "IGPL.NS", "KITEX.NS",
    "NUVOCO.NS", "ORIENTCEM.NS", "RBLBANK.NS", "SAKSOFT.NS", "SNOWMAN.NS",
    "STOVEKRAFT.NS", "VAIBHAVGBL.NS", "VARROC.NS", "WOCKPHARMA.NS",
    # Quality growth mid/smallcaps worth tracking
    "LXCHEM.NS", "MOLDTKPAC.NS", "NAVNETEDUL.NS", "NIITLTD.NS", "ANGELONE.NS",
]

# ---------------------------------------------------------------------------
# Nifty 500 extension — major large/midcaps not already covered above
# (fills gaps between Nifty 50 and Midcap indices)
# ---------------------------------------------------------------------------
_NIFTY500_EXTENSION: list[str] = [
    # IT & Software
    # LTIM.NS is wrong — post-merger ticker is LTIMINDTREE.NS (L&T Infotech + Mindtree, Nov 2022)
    "LTIMINDTREE.NS", "OFSS.NS", "MPHASIS.NS", "CYIENT.NS",
    "TATAELXSI.NS", "KPITTECH.NS",
    # Pharma & Healthcare
    "AUROPHARMA.NS", "ALKEM.NS", "BIOCON.NS", "GLAND.NS", "LALPATHLAB.NS",
    "METROPOLIS.NS", "NATCOPHARM.NS", "SYNGENE.NS", "PPLPHARMA.NS",
    # Banks & Finance
    "FEDERALBNK.NS", "CSBBANK.NS", "KARURVYSYA.NS", "SOUTHBANK.NS",
    "BANDHANBNK.NS", "EQUITASBNK.NS", "FINCABLES.NS",
    "MOTILALOFS.NS", "ICICIGI.NS", "HDFCLIFE.NS", "SBILIFE.NS",
    # Consumer & FMCG
    "PNBHOUSING.NS", "CANFINHOME.NS", "MFSL.NS", "CHOLAHLDNG.NS",
    "JUBLFOOD.NS", "WESTLIFE.NS", "VENKYS.NS",
    # Industrials & Engineering
    "TIINDIA.NS", "GREAVESCOT.NS", "KSB.NS",
    "POWERINDIA.NS", "TDPOWERSYS.NS", "TRIVENI.NS", "UNOMINDA.NS",
    # Chemicals & Materials
    "ALKYLAMINE.NS", "AAVAS.NS", "BALRAMCHIN.NS", "CHAMBLFERT.NS", "GNFC.NS",
    "PIDILITIND.NS", "SOLARINDS.NS", "SRF.NS", "TATACHEM.NS", "VINATIORGA.NS",
    # Real Estate & Infrastructure
    "PRESTIGE.NS", "PHOENIXLTD.NS", "SOBHA.NS",
    # Energy
    "CESC.NS", "JPPOWER.NS", "RPOWER.NS", "TATAPOWER.NS",
    # Auto & Components
    "ASHOKLEY.NS", "BALKRISIND.NS", "BHARATFORG.NS", "MOTHERSON.NS",
    "TITAGARH.NS", "BOSCHLTD.NS",
    # Telecom & Media
    "IDEA.NS", "TATACOMM.NS",
    # Logistics & Infrastructure
    "MAHLOG.NS", "ALLCARGO.NS",
]

# ---------------------------------------------------------------------------
# Ticker validation regex — NSE format: 1-20 uppercase letters/digits/hyphen,
# followed by '.NS'
# ---------------------------------------------------------------------------
_NS_TICKER_RE = re.compile(r'^[A-Z][A-Z0-9&\-]{0,19}\.NS$')


def _is_valid_ns_ticker(t: str) -> bool:
    return bool(_NS_TICKER_RE.match(t))


def _get_india_universe_fallback() -> list[str]:
    """Return the deduplicated hardcoded India universe (~300 tickers)."""
    all_tickers: list[str] = (
        _NIFTY_50
        + _NIFTY_MIDCAP_50
        + _BSE_MIDCAP_EXTRA
        + _NIFTY_SMALLCAP_50
        + _BSE_SMALLCAP_EXTRA
        + _NIFTY500_EXTENSION
    )
    # Deduplicate, preserve order, filter invalid
    seen: set[str] = set()
    result: list[str] = []
    for t in all_tickers:
        if t not in seen and _is_valid_ns_ticker(t):
            seen.add(t)
            result.append(t)
    return result


def get_india_universe() -> list[str]:
    """
    Return deduplicated NSE universe covering all requested indices:
    Nifty 500 + Nifty Midcap 50 + BSE MidCap + Nifty Smallcap 50 + BSE SmallCap.

    Tries to fetch the official Nifty 500 CSV from NSE; falls back to the
    hardcoded list on any failure (NSE blocks cloud IPs with a cookie wall).
    """
    from data.cache import get as cache_get, set as cache_set, TTL_1D

    cache_key = "universe:india"
    cached = cache_get(cache_key)
    if cached and len(cached) > 50:
        return cached

    tickers = _try_fetch_nifty500_from_nse()
    if tickers:
        # Merge with hardcoded midcap/smallcap supplements
        supplement = (
            _NIFTY_MIDCAP_50
            + _BSE_MIDCAP_EXTRA
            + _NIFTY_SMALLCAP_50
            + _BSE_SMALLCAP_EXTRA
        )
        seen = set(tickers)
        for t in supplement:
            if t not in seen and _is_valid_ns_ticker(t):
                tickers.append(t)
                seen.add(t)
        logger.info("India universe: %d tickers (NSE fetch + supplement)", len(tickers))
    else:
        tickers = _get_india_universe_fallback()
        logger.info("India universe: %d tickers (hardcoded fallback)", len(tickers))

    cache_set(cache_key, tickers, ttl_seconds=TTL_1D)
    return tickers


def _try_fetch_nifty500_from_nse() -> Optional[list[str]]:
    """
    Attempt to fetch Nifty 500 constituents.  Tries three endpoints in order:

    1. NSE JSON API  — requires cookie/session, always blocked on cloud IPs.
    2. NSE archives CSV  — public S3-backed file, no cookie required.
       URL: https://archives.nseindia.com/content/indices/ind_nifty500list.csv
    3. niftyindices.com CSV  — IISL (NSE subsidiary) public CDN, also cookieless.
       URL: https://www.niftyindices.com/IndexConstituents/ind_nifty500list.csv

    The CSV format (all sources):
        Company Name, Industry, Symbol, Series, ISIN Code
    We extract the 'Symbol' column and append '.NS'.

    Returns list of '.NS' tickers (≥100 entries), or None on all failures.
    """
    import csv as _csv
    import io as _io

    _BROWSER_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    _headers = {
        "User-Agent": _BROWSER_UA,
        "Accept": "*/*",
        "Referer": "https://www.nseindia.com/",
    }

    # ------------------------------------------------------------------
    # Attempt 1: JSON API (requires NSE session cookie — usually blocked)
    # ------------------------------------------------------------------
    try:
        json_url = (
            "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20500"
        )
        resp = requests.get(
            json_url, headers={**_headers, "Accept": "application/json"}, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        symbols = [
            row["symbol"] + ".NS"
            for row in data.get("data", [])
            if row.get("symbol") and _is_valid_ns_ticker(row["symbol"] + ".NS")
        ]
        if len(symbols) >= 100:
            logger.info("NSE JSON API: fetched %d Nifty 500 symbols", len(symbols))
            return symbols
        logger.debug("NSE JSON API: only %d symbols — likely blocked", len(symbols))
    except Exception as exc:
        logger.debug("NSE JSON API failed: %s", exc)

    # ------------------------------------------------------------------
    # Attempts 2 & 3: Public CSV archives (no cookie required)
    # ------------------------------------------------------------------
    _CSV_URLS = [
        "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
        "https://www.niftyindices.com/IndexConstituents/ind_nifty500list.csv",
    ]
    for csv_url in _CSV_URLS:
        try:
            resp = requests.get(csv_url, headers=_headers, timeout=15)
            resp.raise_for_status()

            # The CSV is UTF-8; sometimes has a BOM
            text = resp.content.decode("utf-8-sig").strip()
            reader = _csv.DictReader(_io.StringIO(text))

            # Header may be 'Symbol' or ' Symbol' (leading space) — normalise
            symbols = []
            for row in reader:
                # Find the Symbol key case-insensitively, stripping whitespace
                sym = next(
                    (v.strip() for k, v in row.items()
                     if k.strip().lower() == "symbol" and v.strip()),
                    None
                )
                if sym and _is_valid_ns_ticker(sym + ".NS"):
                    symbols.append(sym + ".NS")

            if len(symbols) >= 100:
                logger.info(
                    "NSE archives CSV (%s): fetched %d Nifty 500 symbols",
                    csv_url.split("/")[-1], len(symbols),
                )
                return symbols
            logger.debug(
                "NSE CSV %s returned only %d symbols",
                csv_url, len(symbols),
            )
        except Exception as exc:
            logger.debug("NSE CSV fetch failed (%s): %s", csv_url, exc)

    logger.debug("All NSE Nifty 500 fetch attempts failed — using hardcoded fallback")
    return None
