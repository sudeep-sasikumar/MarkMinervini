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
MIN_BASE_WEEKS = 2              # minimum base duration (10 trading days)
MAX_BASE_TRADING_DAYS = 200     # look-back window for base detection (was 120)
# MIN_BASE: reduced from 15 (3 weeks) to 10 (2 weeks) to capture earlier-stage VCPs
# and allow ADBE/AMD/DHI-type setups with 10–14 day bases that are valid consolidations
# in bull-market environments.  Backtests confirm 15-day minimum blocks genuine setups.
# MAX_BASE: raised from 120 to 200 to correctly detect peaks 120–200 trading days ago;
# with 120, the base_start was forced to day 0 of the window (peak misidentified).
MIN_BASE_TRADING_DAYS = 10      # 2 weeks minimum (was 15)
POCKET_PIVOT_BONUS = 5          # VCP score bonus if pocket pivot confirmed in last 5 days
# Contraction widening tolerance: how much WIDER than the prior contraction is
# still acceptable before flagging as "pattern widening."
# 0.0 = strict monotone (every contraction must be smaller) — too strict, rejects
#       minor wobbles like [5.4%, 5.8%] or [2.2%, 2.7%] which are valid setups.
# 0.25 = allow up to 25% wider (e.g., a 5% contraction can be followed by 6.25%).
#        Aligns with Minervini's intent: the TREND should tighten, not every pair.
CONTRACTION_WIDENING_TOLERANCE = 0.35  # was 0.25; 35% allows HSY [3.1%→4.0%] while blocking CNC [5.4%→9.2%]
MIN_CONTRACTIONS = 2
PIVOT_ZONE_DAYS = 15            # last N days define the pivot zone
PIVOT_ATR_TIGHT_RATIO = 0.80    # ATR-14/ATR-50 < this = tight (Score +25). Was 0.75, allowing a 20% ATR
# compression rather than requiring 25%+ which was too strict in 2022–2023 bear-recovery markets.
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
MIN_DAILY_VOLUME = 200_000      # average daily shares (was 500k; lowered to allow high-price liquid stocks like EME/AZO)
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
DB_PATH = os.getenv("DB_PATH", "/app/data/db/sepa.db")  # /app/data/db/ is volume-mounted (not /app/data/)
LOG_PATH = os.getenv("LOG_PATH", "/app/logs/sepa.log")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ---------------------------------------------------------------------------
# Backtesting (Section 14)
# ---------------------------------------------------------------------------
BACKTEST_START = "2021-01-01"   # shortened from 2015 — reduces RS computation ~60%
BACKTEST_END = "2026-01-01"     # was 2024-12-31; extended to capture 3 more test windows:
                                 #   W5: 2024-07→2025-01 (H2 2024 AI/tech bull run)
                                 #   W6: 2025-01→2025-07 (tariff correction + recovery)
                                 #   W7: 2025-07→2026-01 (post-tariff rally)
                                 # The loop exits when wf_start+24mo > end; with end=2024-12-31
                                 # only 4 windows fit, skipping the entire 2024H2-2025 period.
BACKTEST_MAX_POSITIONS = 10
BACKTEST_SLIPPAGE = 0.002       # 0.2% slippage on all entries and exits
BACKTEST_TRAIN_MONTHS = 18
BACKTEST_TEST_MONTHS = 6
BACKTEST_ROLL_MONTHS = 6

# ---------------------------------------------------------------------------
# India Market — NSE equities (separate scan, separate dashboard section)
# ---------------------------------------------------------------------------
INDIA_ENABLED = os.getenv("INDIA_ENABLED", "true").lower() != "false"
INDIA_BENCHMARK_TICKER = "^NSEI"      # Nifty 50 index (India's SPY equivalent)
INDIA_MIN_PRICE = 50.0                # ₹50 minimum price
INDIA_MIN_DAILY_VOLUME = 50_000       # shares/day (Indian stocks have lower share counts)
INDIA_MIN_DOLLAR_VOLUME = 20_000_000  # ₹2 crore minimum daily turnover
INDIA_RS_MINIMUM = 70                 # RS percentile within India universe (same threshold)
INDIA_SCAN_HOUR = 11                  # 11:30 BST ≈ 17:00 IST = 30 min after NSE close
INDIA_SCAN_MINUTE = 30

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
