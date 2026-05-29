"""
All system thresholds and parameters. Never hardcode these values elsewhere.
Import with: from config.settings import *  or  from config import settings
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Trend Template (Section 5A)
# ---------------------------------------------------------------------------
SMA_PERIODS = [50, 150, 200]
SMA200_RISING_DAYS = 20          # minimum days for rising 200-SMA check
SMA200_RISING_STRONG_DAYS = 100  # strong rising flag (4–5 months)
HIGH_PROXIMITY_THRESHOLD = 0.75  # price must be >= 52wk_high * 0.75
LOW_DISTANCE_THRESHOLD = 1.30    # price must be >= 52wk_low * 1.30
RS_MINIMUM = 70                  # minimum RS percentile rank

# ---------------------------------------------------------------------------
# Fundamental Filter (Section 5C)
# ---------------------------------------------------------------------------
EPS_GROWTH_MIN = 20       # % quarterly EPS growth YoY (hard gate)
REVENUE_GROWTH_MIN = 15   # % quarterly revenue growth YoY (hard gate)
ROE_MIN = 17              # % return on equity (scored, not hard gate)
FUNDAMENTALS_CACHE_DAYS = 7  # cache fundamentals for 7 days

# ---------------------------------------------------------------------------
# VCP Pattern Detection (Section 6)
# ---------------------------------------------------------------------------
VCP_SCORE_MIN = 80              # hard gate — 79 does NOT alert
MIN_PRIOR_ADVANCE = 30          # % advance required before base started
MIN_BASE_WEEKS = 3              # minimum base duration (15 trading days)
MAX_BASE_TRADING_DAYS = 120     # look-back window for base detection
# Minervini minimum base = 3 weeks = 15 trading days.
# The master prompt states "Look back 60–120 trading days" for the SEARCH WINDOW,
# not the minimum duration. 60 was incorrectly used as the minimum in Session 5.
MIN_BASE_TRADING_DAYS = 15      # 3 weeks minimum (was wrongly 60)
POCKET_PIVOT_BONUS = 5          # VCP score bonus if pocket pivot confirmed in last 5 days
CONTRACTION_TIGHTENING_RATIO = 0.90  # each contraction must be < prev * 0.90 (10% tighter minimum)
MIN_CONTRACTIONS = 2
PIVOT_ZONE_DAYS = 15            # last N days define the pivot zone
PIVOT_ATR_TIGHT_RATIO = 0.75    # ATR-14 / ATR-50 < this = tight (Score +25)
# Any 25%+ reduction in ATR during the pivot zone vs. the full base qualifies.
# Old threshold of 0.35 required a 65%+ collapse — essentially impossible for
# real stocks, causing 0 ATR score for every VCP and vcp_wc=0 in all backtest windows.
PIVOT_ATR_VERY_TIGHT_RATIO = 0.50  # 50%+ ATR reduction = very tight (Score +35)
VOLUME_DRY_UP_DAYS = 5          # final days to check for volume dry-up
BREAKOUT_VOLUME_RATIO = 1.4     # volume must be >= 1.4× 50-day avg on breakout
BREAKOUT_STRONG_VOLUME = 2.0    # >= 2.0× = strong institutional confirmation
GAP_UP_MAX = 0.05               # open > pivot * 1.05 → skip (gap-up)
WIDE_LOOSE_BAR_PCT = 0.03       # daily range > 3% = wide/loose (penalty)
RS_LINE_HIGH_BONUS = 10         # score bonus if RS line at new 52-wk high
ENTRY_ABOVE_PIVOT = 0.05        # entry = pivot + $0.05

# ---------------------------------------------------------------------------
# Liquidity Gates (Section 6, anti-false-positive)
# ---------------------------------------------------------------------------
MIN_DAILY_VOLUME = 500_000      # average daily shares
MIN_DOLLAR_VOLUME = 5_000_000   # average daily dollar volume
MIN_PRICE = 10.0                # stock price floor

# ---------------------------------------------------------------------------
# Risk Management (Section 10)
# ---------------------------------------------------------------------------
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", 0.015))
MAX_POSITION_PCT = 0.20         # no single position > 20% of account
MAX_STOP_PCT = 0.08             # stop never wider than 8%
MIN_POSITION_PCT = 0.02         # note as "too small" if < 2%

# Portfolio drawdown circuit breakers
PORTFOLIO_DRAWDOWN_CAUTION = 0.20   # aggression → 0.50
PORTFOLIO_DRAWDOWN_SEVERE = 0.25    # aggression → 0.25
PORTFOLIO_DRAWDOWN_STOP = 0.30      # aggression → 0.0, no new positions

# ---------------------------------------------------------------------------
# Market Regime Detection (Section 7)
# ---------------------------------------------------------------------------
DISTRIBUTION_DAYS_CAUTION = 3   # reduce position sizes to 50%
DISTRIBUTION_DAYS_DANGER = 5    # suppress all signals
DISTRIBUTION_LOOKBACK = 25      # sessions to scan for distribution days
DISTRIBUTION_DAY_MIN_DROP = 0.002   # 0.2% minimum close-to-close drop (IBD definition)
SPY_DROP_CORRECTION = 0.05      # SPY drop threshold for FTD tracking
FTD_MIN_GAIN = 0.017            # minimum FTD gain (1.7%)
FTD_MIN_DAY = 4                 # FTD must be day 4+ of attempted rally

VIX_LOW = 15                    # low fear / bullish
VIX_NORMAL_HIGH = 25            # normal ceiling
VIX_CAUTION = 25                # position sizes 50%
VIX_DANGER = 35                 # suppress all signals

BREADTH_BULL = 60               # % S&P 500 above 200-SMA = healthy
BREADTH_WEAK = 40               # below = mixed/weak (reduce sizes)
BREADTH_BEAR = 20               # below = bear (suppress all signals)
BREADTH_MIXED_LOW = BREADTH_WEAK  # alias kept for backwards compat

# ---------------------------------------------------------------------------
# Earnings Safety (Section 8)
# ---------------------------------------------------------------------------
EARNINGS_BLOCK_DAYS = 5         # block signal entirely
EARNINGS_WARNING_DAYS = 14      # allow, but warn + 50% size
EARNINGS_LOOKBACK_DAYS = 2      # post-earnings assessment window

# ---------------------------------------------------------------------------
# Watchlist management
# ---------------------------------------------------------------------------
WATCHLIST_MAX_AGE_DAYS = 14     # remove watchlist entries not refreshed in this many days
NEAR_PIVOT_THRESHOLD = 0.05     # within 5% of pivot price = "near pivot"

# ---------------------------------------------------------------------------
# Fundamentals scoring
# ---------------------------------------------------------------------------
EPS_ACCELERATION_SCORE = 2      # bonus score when EPS growth is accelerating quarter-over-quarter

# ---------------------------------------------------------------------------
# Scheduler — UK/BST times (Section 12)
# ---------------------------------------------------------------------------
MARKET_OPEN_BST = "13:30"
MARKET_CLOSE_BST = "21:00"
INTRADAY_INTERVAL_MINUTES = 15
SCHEDULER_TIMEZONE = "Europe/London"

# ---------------------------------------------------------------------------
# API Configuration
# ---------------------------------------------------------------------------
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")

# Finnhub rate limit: max 60/min on free tier; use 55 as safe ceiling
FINNHUB_MAX_CALLS_PER_MIN = 55
FINNHUB_NEWS_DAYS = 30          # days of company news to fetch

# Alpha Vantage: 25 calls/day free tier
ALPHA_VANTAGE_MAX_DAILY = 25

# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------
ACCOUNT_EQUITY_GBP = float(os.getenv("ACCOUNT_EQUITY_GBP", 50000))
VPS_IP = os.getenv("VPS_IP", "YOUR_VPS_IP")   # used in Telegram dashboard links

# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
DASHBOARD_PORT = 8501
DASHBOARD_REFRESH_SECONDS = 60

# ---------------------------------------------------------------------------
# Database / Logging
# ---------------------------------------------------------------------------
DB_PATH = os.getenv("DB_PATH", "/app/data/sepa.db")
LOG_PATH = os.getenv("LOG_PATH", "/app/logs/sepa.log")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ---------------------------------------------------------------------------
# Backtesting (Section 14)
# ---------------------------------------------------------------------------
BACKTEST_START = "2021-01-01"   # shortened from 2015 — reduces RS computation ~60%
BACKTEST_END = "2024-12-31"
BACKTEST_MAX_POSITIONS = 10
BACKTEST_SLIPPAGE = 0.002       # 0.2% slippage on all entries and exits
BACKTEST_TRAIN_MONTHS = 18
BACKTEST_TEST_MONTHS = 6
BACKTEST_ROLL_MONTHS = 6

# ---------------------------------------------------------------------------
# Sector ETF map (for sector Stage 2 gate)
# ---------------------------------------------------------------------------
SECTOR_ETF_MAP = {
    "Technology": "XLK",
    "Health Care": "XLV",
    "Industrials": "XLI",
    "Financials": "XLF",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Communication Services": "XLC",
}

# yfinance .info["sector"] can return different names than the keys above.
# This map normalises yfinance sector names → canonical SECTOR_ETF_MAP keys.
SECTOR_NAME_ALIASES = {
    "Healthcare": "Health Care",
    "Financial Services": "Financials",
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
    "Basic Materials": "Materials",
    "Communication Services": "Communication Services",  # already matches
    "Technology": "Technology",
    "Industrials": "Industrials",
    "Energy": "Energy",
    "Real Estate": "Real Estate",
    "Utilities": "Utilities",
}
