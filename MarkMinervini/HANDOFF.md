# HANDOFF.md — Minervini SEPA Signal System
### Master Continuity Document — Updated After Every Session

> **How to use:** In a new Claude Code session say:
> *"Read HANDOFF.md in the MarkMinervini project and continue from there."*
> This document is the single source of truth for the entire project.

---

## Table of Contents
1. [Project Identity & Infrastructure](#1-project-identity--infrastructure)
2. [System Purpose & Trading Strategy](#2-system-purpose--trading-strategy)
3. [Complete Architecture — All Files](#3-complete-architecture--all-files)
4. [Full Build History — What Was Done in Each Session](#4-full-build-history)
5. [All Bugs Found & Fixed (Across All Sessions)](#5-all-bugs-found--fixed)
6. [Current System State (as of 24 May 2026)](#6-current-system-state)
7. [Key Data Flows & Algorithms](#7-key-data-flows--algorithms)
8. [Configuration & Thresholds](#8-configuration--thresholds)
9. [API Contracts & Data Shapes](#9-api-contracts--data-shapes)
10. [Security Rules (Non-Negotiable)](#10-security-rules)
11. [CI/CD & Deployment](#11-cicd--deployment)
12. [Known Remaining Issues & Next Steps](#12-known-remaining-issues--next-steps)
13. [Session Log](#13-session-log)

---

## 1. Project Identity & Infrastructure

| Item | Value |
|---|---|
| **Project name** | Minervini SEPA Signal System |
| **Local repo** | `D:\Claude\MarkMinervini\` |
| **GitHub** | `https://github.com/sudeep-sasikumar/MarkMinervini` |
| **Docker image** | `ghcr.io/sudeep-sasikumar/markminervini:latest` |
| **Hosting** | Hostinger VPS — Docker Manager |
| **Dashboard URL** | `http://VPS_IP:8501` (Streamlit) |
| **Dashboard port** | `8501` |
| **DB path (container)** | `/app/data/sepa.db` (SQLite, **persistent volume — survives restarts**) |
| **Log path (container)** | `/app/logs/sepa.log` |
| **Local Python** | `D:\Sniper\venv\Scripts\python.exe` (use for local syntax checks) |
| **Timezone** | All scheduler times are **Europe/London (BST/GMT)** |
| **Target universe** | S&P 500 + Russell 1000 US equities |
| **Account currency** | GBP (brokerage: Trading 212 ISA) — USD stocks, GBP account |

---

## 2. System Purpose & Trading Strategy

This is an **automated stock scanner** implementing Mark Minervini's SEPA (Specific Entry Point Analysis) methodology:

1. **Universe**: ~1,500 US equities (S&P 500 + Russell 1000)
2. **Trend Template**: Minervini's 8-point filter (price above SMAs, SMA200 rising, RS ≥ 70, near 52-week high)
3. **Fundamentals**: EPS growth ≥ 20% YoY, Revenue growth ≥ 15% YoY, non-declining gross margins
4. **VCP Detection**: Volatility Contraction Pattern — contracting price/volume bases before breakout
5. **Market Regime**: BULL / NEUTRAL / BEAR gating based on SPY vs SMA200, distribution days, VIX, breadth
6. **Alerts**: Telegram messages with full setup details, position sizing in GBP + USD, 2R/3R targets
7. **Intraday**: Every 15 min during US market hours — checks watchlist for live breakouts on 5m bars

**Alert is only sent when ALL of these pass:**
- Trend Template: all 8 criteria ✅
- Fundamentals: hard gates ✅
- VCP score ≥ 80/100
- Breakout confirmed: close > pivot AND volume ≥ 1.4× 50-day average
- Sector in Stage 2 ✅
- No earnings within 5 days
- Regime allows signals (`signals_allowed = True`)
- Position sizing valid (stop ≤ 8%, position ≤ 20% of account)

---

## 3. Complete Architecture — All Files

```
MarkMinervini/
│
├── main.py                     Master entry point. Starts APScheduler, runs
│                               run_full_scan() on startup. Clears stale caches
│                               on every startup (critical for Docker redeploys).
│                               Bear mode: scanning continues, only alerts suppressed.
│
├── dashboard.py                Streamlit 6-page dashboard.
│                               Page 1: Live Dashboard (overhauled — SPY chart,
│                               regime diagnostics, sector table, near-pivot watchlist)
│                               Page 2: Watchlist with filters
│                               Page 3: Signal History with charts
│                               Page 4: Trade Journal with live P&L
│                               Page 5: System Status (API health, logs, funnel)
│                               Page 6: Backtest Results (run + view equity curve)
│                               Sidebar: 🔄 Force Regime Refresh button
│
├── config/
│   └── settings.py             All thresholds and parameters. Never hardcode
│                               elsewhere. Reads from env vars (.env or Docker).
│                               Key additions: VPS_IP, DISTRIBUTION_DAY_MIN_DROP
│
├── database/
│   └── db.py                   SQLite schema init. Tables: signals, watchlist,
│                               positions, cache, system_log, system_status,
│                               backtest_results, setups. Forward-only column
│                               migrations via PRAGMA table_info.
│
├── data/
│   ├── cache.py                SQLite-backed TTL cache. Constants: TTL_1H,
│   │                           TTL_6H, TTL_1D, TTL_7D. All external API calls
│   │                           go through this.
│   │
│   ├── fetcher.py              yfinance OHLCV (primary). fetch_ohlcv(),
│   │                           fetch_ohlcv_batch(), fetch_spy_ohlcv(),
│   │                           fetch_intraday_ohlcv() [5m bars for intraday],
│   │                           fetch_latest_price(), fetch_vix(),
│   │                           fetch_gbpusd_with_source() → {rate, source}
│   │
│   ├── fundamentals.py         Primary: Finnhub /stock/metric + /stock/financials-reported
│   │                           Fallback: Alpha Vantage EARNINGS (EPS) +
│   │                           INCOME_STATEMENT (revenue/margin). Has sliding-window
│   │                           Finnhub rate limiter (55 calls/60s, thread-safe).
│   │
│   ├── earnings_calendar.py    earnings_safety_status() → {action, size_factor, message}
│   │                           block: within 5 days, warn+50%: within 14 days
│   │
│   ├── economic_calendar.py    High-impact event detection (Fed/CPI/NFP/PCE/GDP).
│   │                           is_high_impact_window(), get_high_impact_events()
│   │
│   ├── news_fetcher.py         Finnhub company news for AI sentiment analysis
│   │
│   └── sec_edgar.py            SEC EDGAR management quality summaries for
│                               weekly AI analysis job
│
├── screening/
│   ├── universe.py             get_universe() → S&P 500 + Russell 1000 tickers
│   │                           get_sp500_tickers(), cached 1 day
│   │
│   ├── rs_calculator.py        IBD-style RS percentile rank. Vectorised across
│   │                           full universe. check_rs_line_new_high() detects
│   │                           RS line at new 52-week high.
│   │
│   ├── trend_template.py       8-point Minervini Trend Template. Simple MAs only
│   │                           (never EMA). Returns passes: bool + score: 0–8
│   │                           + per-criterion details dict. Has NaN guard.
│   │
│   └── fundamentals_filter.py  Wraps data/fundamentals.py with pass/fail gates.
│                               Hard gates: EPS ≥ 20%, Rev ≥ 15%, margin OK.
│
├── market_intelligence/
│   ├── regime_detector.py      detect_regime() → full regime dict with SPY
│   │                           diagnostics. BULL/NEUTRAL/BEAR. Distribution days
│   │                           uses 0.2% IBD minimum (critical fix). Reads cached
│   │                           breadth when caller passes None. FTD uses trough.
│   │
│   ├── breadth_monitor.py      compute_breadth() → % S&P 500 above 200-SMA.
│   │                           Cached 6h under "breadth:sp500_above_200sma".
│   │
│   ├── sector_analyzer.py      11-sector ETF Stage 2 check. rs_rating=100.0 for
│   │                           ETFs (structural check only, no cross-RS comparison).
│   │                           period="2y" for sufficient SMA history.
│   │
│   └── ai_analyst.py           Ollama LLM wrappers. analyse_news_catalyst(),
│                               analyse_earnings_quality(), analyse_sector_leadership(),
│                               analyse_management_quality(). Graceful offline fallback.
│
├── patterns/
│   ├── vcp_detector.py         12-step VCP algorithm. Score 0–100. Alert gate:
│   │                           score ≥ 80 AND breakout confirmed (close > pivot,
│   │                           volume ≥ 1.4× avg). MIN_BASE_TRADING_DAYS = 60.
│   │                           Watchlist candidate: score ≥ 70.
│   │
│   ├── pivot_calculator.py     enrich_vcp_result() — adds pivot zone detail,
│   │                           ATR analysis, base quality metrics.
│   │
│   └── pocket_pivot.py         Pocket pivot volume pattern detection (secondary
│                               confirmation signal).
│
├── risk/
│   ├── position_sizer.py       compute_position_size() → GBP-first sizing with
│   │                           USD equivalent. FX via fetch_gbpusd_with_source().
│   │                           fx_warning=True when source="fallback".
│   │                           risk_dollars = risk_usd (NOT risk_gbp).
│   │                           Capped at MAX_POSITION_PCT (20%).
│   │
│   └── stop_manager.py         update_open_position_stops() — trail stops for
│                               open positions. Post-market job.
│
├── alerts/
│   ├── telegram_bot.py         send_message(), send_startup_message(),
│   │                           is_telegram_available(). Retries on failure.
│   │
│   └── alert_formatter.py      format_breakout_alert() — full signal message with
│                               GBP+USD dual display, macro warning, RS line flag.
│                               format_morning_briefing() — dynamic time-to-open.
│                               format_bear_market_alert(regime=dict) — shows actual
│                               trigger reason (dist days / SPY/SMA / VIX / breadth).
│
├── scheduler/
│   └── job_runner.py           APScheduler (BackgroundScheduler, BST timezone).
│                               All 7 weekday jobs have day_of_week="mon-fri".
│                               job_market_intelligence() clears 6 cache keys
│                               before recompute (SPY, QQQ, VIX, breadth, sector,
│                               regime).
│
├── backtesting/
│   ├── backtest.py             Walk-forward backtest (vectorbt). 18-month train,
│   │                           6-month test, 6-month roll. Slippage: 0.2%.
│   │
│   └── metrics.py              CAGR, Sharpe, Sortino, Max Drawdown, Win Rate,
│                               Expectancy, Avg Win/Loss.
│
├── tests/
│   ├── conftest.py             sys.path insertion so imports work from any cwd.
│   ├── test_vcp_gating.py      VCP structural + invariant tests (alert→breakout).
│   ├── test_alert_formatter.py Formatter output tests (GBP/USD, warnings, events).
│   └── test_position_sizer.py  Position sizing invariants (FX, gates, aliases).
│
├── requirements.txt            Production deps (yfinance, streamlit, apscheduler,
│                               pandas, numpy, plotly, python-dotenv, pytz, etc.)
├── requirements-test.txt       CI only: pytest, numpy, pandas
├── Dockerfile                  Multi-arch build (linux/amd64 + linux/arm64).
│                               Base: python:3.11-slim. ENTRYPOINT: main.py.
├── docker-compose.yml          Local dev compose (mounts data/ and logs/ volumes).
├── .env.example                Placeholder values only — never real credentials.
├── .gitignore                  Excludes .env, __pycache__, *.pyc, sepa.db, logs/
│
└── .github/
    └── workflows/
        └── docker-publish.yml  CI gate: compileall + pytest MUST pass before
                                Docker image is built and pushed.
```

---

## 4. Full Build History

### Session 6 — Sixth Review: 4 HIGH-priority fixes + Fundamentals Diagnostics (24 May 2026 — new session)

**Trigger:** Resuming from Session 5 handoff. Tackling HIGH-priority items from HANDOFF.md §12B.

| # | File | Bug | Fix |
|---|---|---|---|
| 1 | `regime_detector.py` | Distribution days ≥ DANGER (5) alone triggered BEAR even when SPY was 10%+ above SMA200 | Compound gate: full suppression ONLY when `dist_days ≥ DANGER AND spy_above_sma200 is False`; when SPY is above SMA200, reduces aggression to 25% but keeps `signals_allowed=True` |
| 2 | `main.py` | No universe coverage check — if <80% of tickers loaded due to API failure, RS rankings were silently skewed | Added abort if `len(price_data) < len(universe) * 0.8`; warns (not abort) at 80–95% coverage |
| 3 | `main.py` | `_get_ticker_sector()` called twice per ticker (at score≥70 and again at score≥80) | Moved single `sector_info = _get_ticker_sector(ticker)` call to top of inner loop |
| 4 | `main.py` | `get_sector_stage2_status()` called three times per alert (gate check, `format_breakout_alert()`, and implicit reuse) | Saved result from gate check and reused in `format_breakout_alert()` |
| 5 | `data/fundamentals.py` | `_parse_quarterly()` EPS/revenue label mismatches were silent — impossible to diagnose | Added DEBUG logging: lists first 15 available IC labels when EPS/revenue not matched; logs when AV fallback triggered; logs per-ticker summary |

**Key commits:** `4ffc6fe`

---

### Session 1 — Initial System Build

**What was built:** The entire 48-file system from scratch.

- `config/settings.py` — all thresholds and env vars
- `database/db.py` — SQLite schema (signals, watchlist, positions, cache, logs, backtest_results)
- `data/cache.py` — SQLite TTL cache with purge_expired()
- `data/fetcher.py` — yfinance OHLCV, Finnhub fallback, GBP/USD fetch
- `data/fundamentals.py` — Finnhub metrics + quarterly financials, AV fallback
- `data/earnings_calendar.py` — earnings block/warn gate
- `data/news_fetcher.py` — Finnhub company news
- `data/economic_calendar.py` — high-impact macro event detection
- `data/sec_edgar.py` — SEC EDGAR management quality
- `screening/universe.py` — S&P 500 + Russell 1000
- `screening/rs_calculator.py` — IBD-style RS percentile
- `screening/trend_template.py` — 8-point Minervini filter
- `screening/fundamentals_filter.py` — hard gate wrapper
- `market_intelligence/regime_detector.py` — BULL/NEUTRAL/BEAR detection
- `market_intelligence/breadth_monitor.py` — % above 200-SMA
- `market_intelligence/sector_analyzer.py` — 11-sector Stage 2
- `market_intelligence/ai_analyst.py` — Ollama LLM wrappers
- `patterns/vcp_detector.py` — 12-step VCP scorer + alert gate
- `patterns/pivot_calculator.py` — VCP enrichment
- `patterns/pocket_pivot.py` — pocket pivot detection
- `risk/position_sizer.py` — GBP-first sizing with FX conversion
- `risk/stop_manager.py` — trailing stop updates
- `alerts/telegram_bot.py` — Telegram send wrapper
- `alerts/alert_formatter.py` — rich message formatting
- `scheduler/job_runner.py` — APScheduler jobs
- `main.py` — master entry point with `run_full_scan()` and `run_intraday_check()`
- `dashboard.py` — Streamlit 6-page dashboard
- `backtesting/backtest.py` + `metrics.py` — walk-forward backtest
- `Dockerfile` + `docker-compose.yml` + `.env.example`

---

### Session 2 — Second Review: 10 Fixes

**Trigger:** Code review after initial build.

| Fix | File | What Changed |
|---|---|---|
| Docker context | `docker-publish.yml` | context was repo root; changed to `./MarkMinervini` |
| Intraday volume projection | `main.py` | Was using daily bar volumes; fixed to project intraday volume to full-day equivalent |
| Intraday message | `main.py` | `_send_intraday_breakout_alert()` refactored to use list of lines (not concatenated f-strings) |
| VCP base minimum | `vcp_detector.py` | Base check moved to correct position in algorithm flow |
| SQLite migrations | `database/db.py` | Added forward-only column migrations using PRAGMA table_info |
| Regime dict shape | `regime_detector.py` | Standardised key names across callers |
| GBP/USD display | `alert_formatter.py` | Initial dual-currency layout |
| Watchlist enrichment | `main.py` | Added `insert_setup()` for full VCP snapshot alongside watchlist entry |
| Sector normalisation | `sector_analyzer.py` | Added `SECTOR_NAME_ALIASES` map (yfinance → canonical names) |
| Missing import guards | Multiple | Added try/except around optional imports |

---

### Session 3 — Third Review: P0 Fixes

**Trigger:** Review of GBP/USD truthfulness, intraday message, migrations.

| Fix | File | What Changed |
|---|---|---|
| GBP/USD display (P0) | `alert_formatter.py` | Shows `£{pos_gbp:,}` AND `${pos_usd:,}` — dual display in all size lines |
| risk_dollars alias | `position_sizer.py` | `risk_dollars` = `risk_usd` (USD, not GBP) — was causing wrong value |
| Intraday stop_pct live | `main.py` | Recalculates `stop_pct = (entry - stop) / entry * 100` from live intraday entry price, not stale stored value |
| position_value alias | `position_sizer.py` | `position_value` = `position_value_gbp` (legacy alias) |
| FX warning display | `alert_formatter.py` | Leading space removed; "⚠️ FX fallback" only shows when `fx_warning=True` |
| SQLite setups table | `database/db.py` | Added `setups` table for full VCP snapshot storage |
| Cache TTL correctness | `data/cache.py` | Verified TTL_7D applied to fundamentals, TTL_6H to sector/breadth |

---

### Session 4 — Fourth Review: CI + Test Suite

**Trigger:** Review recommending smoke tests and CI pipeline.

| Fix | File | What Changed |
|---|---|---|
| GitHub Actions CI | `.github/workflows/docker-publish.yml` | Added `lint-and-test` job: `compileall` + `pytest`; `build-and-push` has `needs: lint-and-test` |
| Unit tests — VCP | `tests/test_vcp_gating.py` | Tests for return dict structure, alert→breakout invariant, negative cases, stop arithmetic |
| Unit tests — formatter | `tests/test_alert_formatter.py` | Tests for GBP/USD display, FX warning, macro warning, event list (str and dict) |
| Unit tests — position | `tests/test_position_sizer.py` | Tests for FX conversion, risk_dollars alias, gates, aggression scaling |
| conftest.py | `tests/conftest.py` | sys.path insertion so imports work from any directory |
| VCP test signature | `tests/test_vcp_gating.py` | Fixed detect_vcp() call signature: `(ticker, df, trend_template_passes, rs_line_new_high)` — all keyword args |
| Macro warning fix | `alert_formatter.py` | `format_breakout_alert()` shows macro event warning when `regime.high_impact_event_imminent=True` |

---

### Session 5 — Fifth Review: 16 Bug Fixes + Dashboard Overhaul (24 May 2026)

**Trigger:** Live production failure — Telegram sent "BEAR MARKET MODE — SPY below 200-day SMA" while SPY was ~10.5% above its 200-day SMA. Full brutal audit conducted.

**Root cause chain:** Distribution days 7 ≥ danger threshold (5) → `signals_allowed=False` → `format_bear_market_alert()` called with hardcoded "SPY below 200-day SMA" message.

**Sub-root cause:** Distribution day counting had no 0.2% minimum — any micro-down-tick on slightly higher volume was counted as a distribution day, over-counting severely in choppy but healthy markets.

**Secondary root cause of ❌ sectors:** `rs_rating=50.0` was passed for ETF Stage 2 check. Since `RS_MINIMUM=70`, criterion c8 always failed (50 < 70), making every sector appear as Not Stage 2 and blocking all signals permanently.

| # | File | Bug | Fix |
|---|---|---|---|
| 1 | `regime_detector.py` | Distribution day no 0.2% minimum (root cause of false BEAR) | Added `daily_change <= -DISTRIBUTION_DAY_MIN_DROP` (0.002) check |
| 2 | `regime_detector.py` | Breadth always None in regime — disconnected from breadth_monitor | `detect_regime()` reads `"breadth:sp500_above_200sma"` from cache when `breadth_pct=None` |
| 3 | `regime_detector.py` | No SPY diagnostic fields in return dict — impossible to audit BEAR trigger | Added `spy_close/sma50/sma150/sma200`, `spy_above_sma200`, `spy_ma_stack_ok`, `spy_last_date`, `bear_gate` |
| 4 | `regime_detector.py` | FTD anchor was `index[-1]` (last in-correction day, not trough) | Changed to `correction_window.idxmin()` — actual lowest close |
| 5 | `alert_formatter.py` | `format_bear_market_alert()` hardcoded "SPY below 200-day SMA" | Now accepts `regime: dict`, shows actual trigger with exact prices |
| 6 | `scheduler/job_runner.py` | ALL 7 weekday jobs had no `day_of_week` restriction — fired on Sundays | Added `day_of_week="mon-fri"` to all 7 jobs |
| 7 | `scheduler/job_runner.py` | `job_market_intelligence` only deleted `regime:latest` — recomputed on stale underlying data | Now also deletes `ohlcv:SPY:2y`, `ohlcv:QQQ:2y`, `vix:latest`, `breadth:sp500_above_200sma`, `sector:performance` |
| 8 | `sector_analyzer.py` | `rs_rating=50.0` → c8 always fails (50 < RS_MINIMUM=70) → all sectors ❌ | Changed to `rs_rating=100.0` — ETFs don't participate in cross-universe RS |
| 9 | `sector_analyzer.py` | `period="1y"` borderline for 200-SMA | Changed to `period="2y"` |
| 10 | `alert_formatter.py` | Morning briefing hardcoded "US market opens in ~1h" even on non-trading days | Now computes dynamically from BST clock vs 13:30 open time |
| 11 | `config/settings.py` | No `VPS_IP` env var — all dashboard links showed "YOUR_VPS_IP" | Added `VPS_IP = os.getenv("VPS_IP", "YOUR_VPS_IP")` |
| 12 | `config/settings.py` | No named constant for distribution day minimum | Added `DISTRIBUTION_DAY_MIN_DROP = 0.002` |
| 13 | `main.py` | Bear mode did `return []` — stopped ALL scanning including watchlist building | Changed to `signals_suppressed` flag; scanning continues, only alert-sending blocked |
| 14 | `vcp_detector.py` | Step 3 used `MIN_BASE_WEEKS * 5 = 15` days instead of `MIN_BASE_TRADING_DAYS = 60` | Changed to `settings.MIN_BASE_TRADING_DAYS` |
| 15 | `data/fundamentals.py` | `_finnhub_get` bypassed all rate limiting (direct requests.get) | Added sliding-window token bucket: `threading.Lock` + `deque`, 55 calls/60s |
| 16 | `data/fundamentals.py` | AV EPS fallback used `INCOME_STATEMENT` which lacks `reportedEPS` | Now calls `EARNINGS` endpoint for EPS; `INCOME_STATEMENT` only for revenue/margin |
| 17 | `screening/trend_template.py` | No NaN guard after rolling SMA — could propagate NaN silently | Added `if any(pd.isna(v) for v in (price, s50, s150, s200)): return` |

**Dashboard overhaul (same session):**

- `main.py`: Clears 6 stale cache keys on every startup (SQLite persists across Docker restarts on mounted volume — old pre-fix cache was causing the BEAR and ❌ sectors to persist after redeploy)
- `dashboard.py`: Added `🔄 Force Regime Refresh` sidebar button (deletes all market caches, reruns)
- `dashboard.py`: Live Dashboard page completely rewritten:
  - 5 metric cards (Regime+aggression, VIX+zone, Dist Days+threshold, Breadth%, Signals/Watchlist)
  - SPY Health panel: price vs SMA200 (exact $$+%), MA stack status, 52-week high/low, QQQ vs SMA200
  - 90-day SPY candlestick chart with SMA50/150/200 overlay (Plotly)
  - Distribution days panel: progress bar + IBD definition note + zone context
  - VIX panel: zone labels with exact thresholds
  - Breadth panel: bar + zone context
  - FTD status with explanation
  - Macro event warning
  - All 11 sectors table: 1m%, 3m%, TT score (N/8), Stage 2 ✅/❌ — sorted by 3m performance
  - Near-pivot watchlist: stocks within ±5% of pivot (live price check)
  - Active signals: shows regime-suppression reason when no signals

---

## 5. All Bugs Found & Fixed

Complete chronological list across all sessions. See Section 4 for details per session.

**Architecture/structural fixes (Sessions 1–4):**
- Docker build context path
- Intraday volume projection (daily bars → 5m bar projection to full-day equivalent)
- Intraday message building (list-of-lines, not f-string concatenation)
- SQLite forward-only column migrations
- GBP/USD dual currency display
- `risk_dollars` = `risk_usd` not `risk_gbp`
- `position_value` legacy alias = `position_value_gbp`
- Live stop_pct recalculation from intraday entry price
- Sector name normalisation (yfinance → canonical)
- VCP test signature (wrong positional arg order)
- Macro warning in alerts

**Critical system correctness fixes (Session 5):**
- Distribution day 0.2% IBD minimum (root cause of false BEAR)
- Breadth disconnected from regime detector
- No SPY diagnostic fields in regime dict
- FTD anchor wrong (last in-correction day, not trough)
- Bear alert hardcoded message regardless of actual cause
- All weekday scheduler jobs firing on weekends
- Stale cache not cleared before regime recompute
- Sector ETF rs_rating=50 → always ❌ (blocked all signals)
- Sector period=1y (insufficient for 200-SMA)
- Morning briefing hardcoded "~1h" time string
- Bear mode stopped all scanning (not just alerts)
- VCP minimum base 15 days instead of 60
- Finnhub rate limiter missing in fundamentals
- Alpha Vantage wrong endpoint for EPS
- NaN propagation in trend template

---

## 6. Current System State (as of 24 May 2026 — Session 6)

**Latest commits (newest first):**
```
4ffc6fe  Sixth review: 4 HIGH-priority fixes + fundamentals diagnostics
4ea2d46  Add HANDOFF-24May.md
c6bf3b0  Dashboard overhaul + startup cache clearing
8cac696  Fifth review: 16 bug fixes (false BEAR alert, weekend scheduler, sector gate)
ed8f87c  Fix test_vcp_gating.py: correct detect_vcp() call signature
2c75bfb  Add CI compile check, unit test suite, and lint-and-test gate
f247607  Fourth review: FX truthfulness, intraday stop_pct, macro warning
3f45f96  Third review P0 fixes: GBP/USD display, intraday msg, SQLite migrations
bbd9ac3  Second review: 10 targeted fixes
[initial] Full 48-file system build
```

**What is working:**
- Full scanner pipeline (universe → trend → fundamentals → VCP → alerts)
- Intraday breakout engine (5m bars, volume projection, deduplication)
- Regime detection with correct IBD distribution day counting
- Breadth now flows correctly into regime
- Sectors correctly evaluated (rs_rating=100 for ETFs)
- All weekday scheduler jobs restricted to Mon–Fri
- Bear mode scans and builds watchlist but suppresses alert sending
- Dashboard shows full SPY/regime/sector diagnostic detail
- Startup cache clearing ensures fresh data after redeploy
- Force Refresh button in dashboard sidebar
- CI pipeline: compileall + pytest before Docker build
- Unit tests: 30+ tests across 3 test files

**What was observed on 24 May deployment:**
- Distribution days showing 7/5 even after the 0.2% fix was deployed
- Sectors still showing ❌ after deployment
- **Root cause**: SQLite cache persisted old values across Docker restart
- **Fix deployed**: startup cache clearing in main.py + Force Refresh button
- **Action required**: After next redeploy, click "🔄 Force Regime Refresh" in sidebar

---

## 7. Key Data Flows & Algorithms

### Full Scan Pipeline (run_full_scan())
```
1. fetch_ohlcv_batch(universe, "2y")        → price_data dict
2. compute_breadth(price_data)              → float (% above SMA200), cached 6h
3. detect_regime(spy_df, breadth_pct)       → regime dict, cached 1h
   [if signals_suppressed: continue scanning, just don't alert]
4. compute_rs_ratings(price_data)           → rs_df (ticker → percentile rank)
5. for each ticker:
   a. check_trend_template(ticker, df, rs)  → passes: bool
   b. apply_fundamentals_filter(ticker)     → passes: bool
   c. detect_vcp(ticker, df, tt_passes)     → score, alert, contractions, pivot...
   d. if score >= 70: upsert_watchlist() + insert_setup()
   e. if alert AND NOT signals_suppressed:
      - get_sector_stage2_status(sector)    → bool
      - earnings_safety_status(ticker)      → action, size_factor
      - compute_position_size(entry, stop)  → shares, GBP/USD values, FX rate
      - analyse_news_catalyst(ticker)       → AI sentiment
      - format_breakout_alert(...)          → Telegram message string
      - send_message(msg)
      - insert_signal(...)
```

### Intraday Check Pipeline (run_intraday_check())
```
Every 15 min, 13:30–21:00 BST Mon–Fri:
1. Load watchlist WHERE vcp_score >= 70
2. For each: fetch_intraday_ohlcv(ticker, "5m")
3. current_price = last close on 5m bars
4. intraday_vol = sum of 5m volumes
5. projected_vol = intraday_vol / fraction_of_session_elapsed
6. If current_price > pivot AND projected_vol >= 1.4× 50d_avg:
   a. Check not already alerted today (signals table)
   b. Compute live stop_pct = (entry - stored_stop) / entry × 100
   c. Compute live targets: T1 = entry + 2×risk, T2 = entry + 3×risk
   d. All gates: regime, sector, earnings, position sizing
   e. send intraday alert
   f. Break (one alert per 15-min window)
```

### Regime Detection Logic
```
1. Fetch SPY "2y" OHLCV (or use passed df)
2. Compute: sma50, sma150, sma200, spy_close, spy_last_date
3. Bear gate: spy_close < sma200 → BEAR, signals_allowed=False
4. MA stack: sma50 > sma150 > sma200? If not → reduce aggression 0.75
5. Distribution days (last 25 sessions):
   count days where: close_change ≤ -0.2% AND volume > prior_volume
   [IBD definition — 0.2% minimum is CRITICAL, prevents false BEAR]
   ≥ 5: signals_allowed=False, aggression=0
   ≥ 3: aggression=0.5
6. FTD: find correction trough (idxmin), look for day 4+ with +1.7% on higher vol
   Not confirmed → aggression=0.5
7. VIX: ≥35 suppress, ≥25 half-size
8. Breadth: read from cache if None passed
   <20% suppress, <40% reduce aggression
9. High-impact macro event: aggression=0.5
10. Derive BULL/NEUTRAL/BEAR label from final aggression and issues list
11. Return dict with all SPY diagnostics for dashboard and alert audit trail
```

### VCP Scoring (0–100)
```
Step 1:  Trend Template must pass (hard gate, not scored)
Step 2:  Prior advance ≥ 30% before base (hard gate)
Step 3:  Base length ≥ 60 trading days (MIN_BASE_TRADING_DAYS)
Step 4:  Identify contractions (swing-point high/low pairs)
Step 5:  Contraction series must tighten (each < 85% of prior depth)
         ≥ 3 contractions: +25 pts
         ≥ 4 contractions: +10 pts bonus
Step 6:  Volume declining across contractions
         All declining: +25 pts
         Most declining: +10 pts
Step 7:  ATR collapse in pivot zone (last 5–15 days)
         ATR < 25% of 50-day ATR: +25 + 10 bonus
         ATR < 35% of 50-day ATR: +25 pts
Step 8:  Volume dry-up in final 5 days (< 3 of 5 under avg: -10 pts)
Step 9:  No wide-and-loose bars in pivot zone (>3% daily range: -15 pts)
Step 10: RS line new high bonus: +10 pts
Step 11: Entry = pivot + $0.05; Stop = pivot_zone_low × 0.995
         Stop > 8%: reject (risk_valid=False)
Step 12: Gap-up filter: open > pivot × 1.05: reject (MISSED)
Step 13: Breakout: close > pivot AND vol ≥ 1.4× 50d avg
         Confirmed: +5 pts (strong vol ≥ 2×: +5 more)
Alert:   score ≥ 80 AND breakout_confirmed=True
Watchlist: score ≥ 70 (setup forming, not yet broken out)
```

### Position Sizing
```
1. risk_gbp = ACCOUNT_EQUITY_GBP × RISK_PER_TRADE_PCT × aggression_factor × earnings_size_factor
2. risk_usd = risk_gbp × gbpusd_rate
3. risk_per_share = entry_price - stop_price
4. shares = floor(risk_usd / risk_per_share)
5. position_value_usd = shares × entry_price
6. position_value_gbp = position_value_usd / gbpusd_rate
7. Cap at MAX_POSITION_PCT (20%): reduce shares if needed
8. Validate: stop ≤ 8%, position ≥ MIN_POSITION_PCT (warn if < 2%)
9. risk_dollars = risk_usd  ← (NOT risk_gbp — important alias)
10. fx_warning = True if source="fallback" (from fetch_gbpusd_with_source)
```

---

## 8. Configuration & Thresholds

All in `config/settings.py`. Key values:

```python
# Trend Template
RS_MINIMUM = 70
HIGH_PROXIMITY_THRESHOLD = 0.75  # price >= 52wk_high × 0.75
LOW_DISTANCE_THRESHOLD = 1.30    # price >= 52wk_low × 1.30
SMA200_RISING_DAYS = 20          # minimum days for rising 200-SMA check
SMA200_RISING_STRONG_DAYS = 100  # 4–5 months (strong flag)

# Fundamentals
EPS_GROWTH_MIN = 20    # % YoY quarterly
REVENUE_GROWTH_MIN = 15
ROE_MIN = 17           # % (scored, not hard gate)

# VCP
VCP_SCORE_MIN = 80
MIN_BASE_TRADING_DAYS = 60      # CRITICAL — was wrongly 15 (MIN_BASE_WEEKS×5) before fix
MAX_BASE_TRADING_DAYS = 120
MIN_CONTRACTIONS = 2
BREAKOUT_VOLUME_RATIO = 1.4
BREAKOUT_STRONG_VOLUME = 2.0
MAX_STOP_PCT = 0.08             # 8% maximum stop loss
ENTRY_ABOVE_PIVOT = 0.05        # entry = pivot + $0.05

# Liquidity gates
MIN_DAILY_VOLUME = 500_000
MIN_DOLLAR_VOLUME = 5_000_000
MIN_PRICE = 10.0

# Regime
DISTRIBUTION_DAY_MIN_DROP = 0.002   # 0.2% IBD minimum — CRITICAL, do not remove
DISTRIBUTION_DAYS_CAUTION = 3       # 50% sizing
DISTRIBUTION_DAYS_DANGER = 5        # suppress all signals
DISTRIBUTION_LOOKBACK = 25          # sessions window
SPY_DROP_CORRECTION = 0.05          # 5% drop = correction
FTD_MIN_GAIN = 0.017                # 1.7% minimum FTD
VIX_CAUTION = 25                    # 50% sizing
VIX_DANGER = 35                     # suppress all signals
BREADTH_BULL = 60                   # healthy
BREADTH_WEAK = 40                   # reduce sizing
BREADTH_BEAR = 20                   # suppress

# Risk
RISK_PER_TRADE_PCT = float(os.getenv(..., 0.015))  # 1.5% default
MAX_POSITION_PCT = 0.20             # 20% single position cap
PORTFOLIO_DRAWDOWN_CAUTION = 0.20   # 50% aggression
PORTFOLIO_DRAWDOWN_SEVERE = 0.25    # 25% aggression
PORTFOLIO_DRAWDOWN_STOP = 0.30      # 0% (no new positions)

# Earnings safety
EARNINGS_BLOCK_DAYS = 5
EARNINGS_WARNING_DAYS = 14

# Account
ACCOUNT_EQUITY_GBP = float(os.getenv(..., 50000))
VPS_IP = os.getenv("VPS_IP", "YOUR_VPS_IP")
DASHBOARD_PORT = 8501
```

**Environment variables to set in Hostinger Docker Manager:**
```
TELEGRAM_BOT_TOKEN     — bot token from BotFather
TELEGRAM_CHAT_ID       — your personal chat ID
FINNHUB_API_KEY        — Finnhub free tier key
ALPHA_VANTAGE_KEY      — Alpha Vantage free tier key
ACCOUNT_EQUITY_GBP     — e.g. 2000
RISK_PER_TRADE_PCT     — e.g. 0.015
VPS_IP                 — your server IP (for dashboard links in Telegram)
DB_PATH                — /app/data/sepa.db  (default)
LOG_PATH               — /app/logs/sepa.log (default)
LOG_LEVEL              — INFO (default)
```

---

## 9. API Contracts & Data Shapes

### detect_regime() return dict
```python
{
    "regime": "BULL" | "NEUTRAL" | "BEAR",
    "aggression_factor": float,        # 0.0 – 1.0
    "signals_allowed": bool,
    "vix_level": float,
    "breadth_pct": float | None,
    "distribution_days": int,
    "ftd_confirmed": bool,
    "high_impact_event_imminent": bool,
    "regime_summary": str,             # human-readable; used in bear alert
    # SPY diagnostics (added Session 5):
    "spy_close": float | None,
    "spy_sma50": float | None,
    "spy_sma150": float | None,
    "spy_sma200": float | None,
    "spy_above_sma200": bool | None,
    "spy_ma_stack_ok": bool | None,    # sma50 > sma150 > sma200
    "spy_last_date": str | None,       # "YYYY-MM-DD"
    "bear_gate": bool,                 # True only when SPY < SMA200
}
```

### detect_vcp() return dict
```python
{
    "ticker": str,
    "vcp_score": int,              # 0–100
    "grade": str,                  # "ELITE" | "HIGH QUALITY" | "MODERATE" | "NOT A VCP"
    "alert": bool,                 # score >= 80 AND breakout_confirmed
    "watchlist_candidate": bool,   # score >= 70
    "risk_valid": bool,            # stop <= 8%
    "contractions": list[dict],    # [{depth_pct, vol_avg, start, end}]
    "pivot_price": float | None,
    "entry_price": float | None,
    "stop_price": float | None,
    "stop_pct": float | None,
    "target_1": float | None,      # entry + 2R
    "target_2": float | None,      # entry + 3R
    "base_days": int,
    "breakout_confirmed": bool,
    "rejection_reason": str | None,
    "steps": dict,                 # per-step details for dashboard
}
```

### compute_position_size() return dict
```python
{
    "shares": int,
    "position_value_usd": float,
    "position_value_gbp": float,
    "position_value": float,       # legacy alias = position_value_gbp
    "position_pct": float,         # % of account
    "risk_gbp": float,
    "risk_usd": float,
    "risk_dollars": float,         # alias = risk_usd (NOT risk_gbp — named dollars = USD)
    "risk_pct": float,
    "stop_pct": float,
    "gbpusd_rate": float,
    "fx_rate_source": str,         # "live" | "fallback"
    "fx_warning": bool,            # True when source="fallback"
    "valid": bool,
    "note": str,
}
```

### Scheduler jobs (all Mon–Fri BST)
```
08:00  job_data_refresh()         OHLCV download, RS rankings, breadth compute
09:00  job_market_intelligence()   Regime, sectors, macro; clears 6 cache keys first
11:00  job_full_scan()             Full pipeline scan #1
13:00  job_morning_briefing()      Telegram briefing with dynamic time-to-open
13:30–21:00 (every 15m) job_intraday_check()   Watchlist breakout monitor
15:30  job_full_scan()             Full pipeline scan #2
19:00  job_full_scan()             Full pipeline scan #3
21:15  job_post_market()           Stop updates, daily Telegram summary
Sunday 10:00  job_weekly()         AI management quality + backtest
```

---

## 10. Security Rules

**These rules are non-negotiable and must be preserved in every future session:**

1. **Real credentials must NEVER be committed to GitHub** — not in any file, not even temporarily
2. `.env.example` contains placeholder values only (e.g. `TELEGRAM_BOT_TOKEN=your_token_here`)
3. Real credentials are entered only in **Hostinger Docker Manager → Environment Variables UI**
4. Local testing uses a `.env` file which is in `.gitignore`
5. The `.gitignore` already excludes: `.env`, `*.db`, `logs/`, `__pycache__/`
6. Never log credential values even at DEBUG level

---

## 11. CI/CD & Deployment

### GitHub Actions Pipeline (`.github/workflows/docker-publish.yml`)
```
Trigger: push to main branch (or workflow_dispatch)

Job 1: lint-and-test (must pass first)
  - ubuntu-latest, Python 3.11
  - pip install -r MarkMinervini/requirements-test.txt
  - python -m compileall -q MarkMinervini/
  - pytest MarkMinervini/tests/ -v --tb=short

Job 2: build-and-push (needs: lint-and-test)
  - docker buildx (linux/amd64 + linux/arm64)
  - push to ghcr.io/sudeep-sasikumar/markminervini:latest
  - push to ghcr.io/sudeep-sasikumar/markminervini:{git-sha}
  - cache-from/to: type=gha (GitHub Actions cache)
```

### Deployment Process
```
1. Push commits to main branch
2. GitHub Actions runs lint + test (2–3 min)
3. Docker image built and pushed to ghcr.io (5–10 min)
4. In Hostinger: pull new image → restart container
5. After restart: click "🔄 Force Regime Refresh" in dashboard sidebar
   OR wait for startup cache clearing (runs automatically in main.py)
```

### Local Syntax Check (no full env needed)
```powershell
& "D:\Sniper\venv\Scripts\python.exe" -m compileall -q "D:/Claude/MarkMinervini/path/to/file.py"
```

---

## 12. Known Remaining Issues & Next Steps

### 12A. Immediate — Verify After Latest Redeploy

1. **Click "🔄 Force Regime Refresh"** in dashboard sidebar after deploying `c6bf3b0`
2. **Distribution days should recalculate** — with 0.2% minimum applied, count should be lower than 7. If still 7, these are genuine IBD-definition distribution days from the volatile April–May 2026 period and the BEAR call may be accurate (even though SPY is 10.5% above SMA200 — distribution days is a separate signal)
3. **Sectors should show ✅** — XLK (+28.7% 3m), XLE (+8.7% 3m) should pass Stage 2
4. **Dashboard SPY panel** should show price vs SMA200 with exact numbers

### 12B. HIGH Priority — Next Session

| Priority | File | Issue | Suggested Fix |
|---|---|---|---|
| ~~HIGH~~ ✅ | ~~`regime_detector.py`~~ | ~~Dist_days ≥ 5 alone triggers BEAR even when SPY is 10%+ above SMA200~~ | **FIXED in Session 6** — compound gate: full suppression only when SPY is also below SMA200 |
| HIGH | Hostinger env vars | `VPS_IP` not yet set → dashboard links in Telegram show "YOUR_VPS_IP" | Add `VPS_IP=your_server_ip` in Hostinger Docker Manager |
| ~~HIGH~~ ✅ | ~~`main.py`~~ | ~~No minimum universe coverage check~~ | **FIXED in Session 6** — aborts if <80% of universe loads |
| ~~HIGH~~ ✅ | ~~`main.py`~~ | ~~`_get_ticker_sector()` called twice per ticker~~ | **FIXED in Session 6** — single call at top of inner loop |

### 12C. MEDIUM Priority

| Priority | Item |
|---|---|
| MEDIUM | **Verify VCP score ≥ 80 calibration** — `MIN_BASE_TRADING_DAYS=60` is stricter (was 15). Fewer setups will pass Step 3. May need to run a test scan to verify we still get watchlist candidates |
| ~~MEDIUM~~ ✅ | ~~**`data/fundamentals.py`** — `_parse_quarterly` EPS label matching is fragile. Should log how often `eps_growth_yoy` returns None~~ | **FIXED in Session 6** — DEBUG logs now list available IC labels on every mismatch and AV fallback trigger |
| MEDIUM | **`data/fetcher.py`** — No central Finnhub rate limiter. Only `fundamentals.py` has one now. If other modules ever call Finnhub directly they bypass it |
| MEDIUM | **Run `python main.py --test-mode`** on VPS to confirm full pipeline end-to-end produces at least one watchlist candidate from live market data |
| MEDIUM | **Paper trading phase** — system is built but never paper-traded. Run for 2–4 weeks to validate that stocks reaching score≥80 are genuinely setting up for breakouts |

### 12D. LOW Priority / Future

| Priority | Item |
|---|---|
| LOW | Add `pandas_market_calendars` or `exchange_calendars` for US holiday detection (current fix only covers weekends, not Memorial Day, Thanksgiving, etc.) |
| LOW | Add regime-transition Telegram alerts ("BULL → NEUTRAL", "NEUTRAL → BEAR") so changes are visible without checking the dashboard |
| LOW | `dashboard.py` System Status page — show next scheduled job fire times (APScheduler `scheduler.get_jobs()` returns next fire time) |
| LOW | `backtesting/backtest.py` — walk-forward backtest built but never actually run against live VCP parameters to validate score≥80 threshold |
| LOW | `screening/rs_calculator.py` — add minimum universe coverage gate (abort if <80% loaded) |
| LOW | Weekly job (`job_weekly`) calls `analyse_management_quality()` but this is Ollama LLM — verify it doesn't time out when Ollama is offline |

---

## 13. Session Log

Track each work session here. Add a new entry at the start of every new Claude Code session.

---

### Session 1 — Initial Build
**Date:** (early May 2026)
**Focus:** Build entire system from scratch
**Outcome:** 48 files committed; full pipeline working end-to-end; Docker running on Hostinger
**Commits:** Initial build + multiple fixup commits

---

### Session 2 — Second Review
**Date:** (mid May 2026)
**Focus:** Code review fixes — Docker, intraday, DB schema
**Outcome:** 10 fixes; intraday volume projection working; dual GBP/USD started
**Key commits:** `bbd9ac3`

---

### Session 3 — Third Review P0 Fixes
**Date:** (mid May 2026)
**Focus:** GBP/USD truthfulness, intraday message, SQLite migrations
**Outcome:** risk_dollars alias fixed; live stop_pct from intraday entry; setups table added
**Key commits:** `3f45f96`

---

### Session 4 — Fourth Review: CI + Tests
**Date:** (late May 2026)
**Focus:** Add GitHub Actions CI pipeline and unit test suite
**Outcome:** 3 test files (30+ tests); compileall + pytest gate before Docker build
**Key commits:** `2c75bfb`, `ed8f87c`

---

### Session 5 — Fifth Review: 16 Bug Fixes + Dashboard Overhaul
**Date:** 24 May 2026
**Trigger:** Live false BEAR alert — Telegram sent "SPY below 200-day SMA" while SPY was 10.5% above
**Focus:** Full brutal audit; fix root causes; overhaul dashboard with rich diagnostics
**Outcome:** 16 bugs fixed across 9 files; dashboard completely rewritten; startup cache clearing; Force Refresh button
**Key commits:** `8cac696` (bug fixes), `c6bf3b0` (dashboard), `4ea2d46` (HANDOFF-24May.md)
**Observation at session end:** Dashboard still showing BEAR / ❌ sectors after redeploy — confirmed root cause is SQLite cache persisting pre-fix values; Force Refresh button in sidebar will clear this

---

### Session 6 — Sixth Review: HIGH-priority fixes
**Date:** 24 May 2026 (new context window after Session 5)
**Focus:** 4 HIGH-priority items from HANDOFF.md §12B + fundamentals diagnostic logging
**Outcome:**
- Distribution days compound gate: no longer triggers BEAR when SPY is above SMA200 (was the root cause of the 24 May false alert — the 0.2% minimum fixed over-counting, but even with correct counting, 5+ dist days in a volatile bull market was still causing suppression)
- Universe coverage abort guard: scan will not proceed with skewed RS rankings if <80% of universe loaded
- Eliminated duplicate `_get_ticker_sector()` and `get_sector_stage2_status()` calls per ticker
- DEBUG logging for Finnhub label mismatches so fragile `_parse_quarterly` issues surface in logs
**Key commits:** `4ffc6fe`

---

### Session 7 — (next session — fill in here)
**Date:**
**Focus:**
**Outcome:**
**Key commits:**

---

*End of HANDOFF.md — update the Session Log and Remaining Issues sections at the end of each session.*
