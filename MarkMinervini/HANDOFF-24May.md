# HANDOFF — Minervini SEPA System — 24 May 2026

> **How to use this file:** Drop it into a new Claude Code session and say:
> *"Read HANDOFF-24May.md and continue the Minervini SEPA project from there."*

---

## 1. Project Identity

| Item | Value |
|---|---|
| Local repo | `D:\Claude\MarkMinervini\` |
| GitHub | `https://github.com/sudeep-sasikumar/MarkMinervini` |
| Docker image | `ghcr.io/sudeep-sasikumar/markminervini:latest` |
| Hosted on | Hostinger VPS (Docker Manager) |
| Dashboard port | `8501` (Streamlit) |
| Python (local) | `D:\Sniper\venv\Scripts\python.exe` (use for syntax checks) |
| DB path (container) | `/app/data/sepa.db` (SQLite, **persistent volume**) |

---

## 2. What Was Done in This Session (24 May 2026)

### 2A. Bug Fixes — Fifth Review (commit `8cac696`)

Thirteen bugs fixed across nine files following a live production failure where Telegram sent "BEAR MARKET MODE — SPY below 200-day SMA" while SPY was ~10.5% above the 200-day SMA.

| # | File | Bug | Fix |
|---|---|---|---|
| 1 | `regime_detector.py` | Distribution day counting had no 0.2% minimum — ANY down-tick on higher volume counted (root cause of false BEAR) | Added `daily_change <= -DISTRIBUTION_DAY_MIN_DROP` check |
| 2 | `regime_detector.py` | Breadth always None in regime — computed at 08:00, never reached 09:00 detect_regime() | `detect_regime()` now reads `"breadth:sp500_above_200sma"` from SQLite cache when caller passes `breadth_pct=None` |
| 3 | `regime_detector.py` | Return dict had no SPY diagnostic fields — impossible to audit why BEAR was triggered | Added `spy_close`, `spy_sma50/150/200`, `spy_above_sma200`, `spy_ma_stack_ok`, `spy_last_date`, `bear_gate` to return dict |
| 4 | `regime_detector.py` | FTD anchor was `index[-1]` (last in-correction day = a recovery day, not the trough) | Changed to `correction_window.idxmin()` to find the actual lowest close |
| 5 | `alert_formatter.py` | `format_bear_market_alert()` hardcoded "SPY below 200-day SMA" regardless of actual cause | Now accepts `regime: dict` parameter; shows actual trigger: SPY levels, dist_days count, VIX, or breadth |
| 6 | `scheduler/job_runner.py` | ALL 7 weekday jobs had no `day_of_week` restriction — fired on Sundays | Added `day_of_week="mon-fri"` to data_refresh, market_intelligence, full_scan×3, morning_briefing, post_market |
| 7 | `scheduler/job_runner.py` | `job_market_intelligence` only deleted `regime:latest` — recomputed on stale SPY/VIX/breadth | Now also deletes `ohlcv:SPY:2y`, `ohlcv:QQQ:2y`, `vix:latest`, `breadth:sp500_above_200sma`, `sector:performance` |
| 8 | `sector_analyzer.py` | `rs_rating=50.0` for ETF Stage 2 check — 50 < RS_MINIMUM=70, so ALL sectors always failed (every sector showed ❌, blocking all signals forever) | Changed to `rs_rating=100.0` (ETFs don't have cross-universe RS; structural criteria still apply) |
| 9 | `sector_analyzer.py` | `period="1y"` was borderline for 200-SMA trend template checks | Changed to `period="2y"` |
| 10 | `alert_formatter.py` | Morning briefing always said "US market opens in ~1h" even on non-trading days | Now computes dynamically from BST clock: `datetime.now(BST)` vs 13:30 |
| 11 | `config/settings.py` | No `VPS_IP` env var — dashboard links showed "YOUR_VPS_IP" | Added `VPS_IP = os.getenv("VPS_IP", "YOUR_VPS_IP")` and `DISTRIBUTION_DAY_MIN_DROP = 0.002` |
| 12 | `main.py` | `return []` in bear mode stopped all scanning — watchlist went stale | Changed to `signals_suppressed = True` flag; scanning/watchlist-building continues, only alert-sending is blocked |
| 13 | `vcp_detector.py` | Step 3 used `MIN_BASE_WEEKS * 5 = 15` days instead of `MIN_BASE_TRADING_DAYS = 60` | Changed to use `settings.MIN_BASE_TRADING_DAYS` |
| 14 | `data/fundamentals.py` | `_finnhub_get` bypassed all rate limiting — direct `requests.get()` | Added sliding-window token bucket (55 calls/60s) using `threading.Lock` + `deque` |
| 15 | `data/fundamentals.py` | Alpha Vantage EPS fallback used `INCOME_STATEMENT` which lacks `reportedEPS` | Now calls `EARNINGS` endpoint for EPS; `INCOME_STATEMENT` only for revenue/margin |
| 16 | `screening/trend_template.py` | No NaN guard after rolling SMA computation | Added `if any(pd.isna(v) for v in (price, s50, s150, s200)): return` guard |

### 2B. Dashboard Overhaul (commit `c6bf3b0`)

Live Dashboard completely rewritten with rich diagnostics:

**Startup cache clearing (`main.py`)**
- On every container start, deletes 6 stale cache keys so Docker redeployments always get fresh data (SQLite persists across restarts on mounted volume)

**Sidebar**
- `🔄 Force Regime Refresh` button — instantly clears all caches and reruns (no redeployment needed)

**Live Dashboard page** — replaces sparse 4-card layout with:

| Panel | Content |
|---|---|
| 5 metric cards | Regime + aggression%, VIX + zone, Distribution days + threshold, Breadth %, Signals/Watchlist |
| SPY Health | Price vs SMA200 (exact $$ + %), MA stack aligned/broken, 52-week high/low proximity, QQQ vs SMA200 |
| 90-day SPY chart | Candlestick + SMA50 (blue) / SMA150 (orange) / SMA200 (red) |
| Distribution days | Progress bar + zone context + IBD definition note |
| VIX | Level + exact threshold zones (calm/normal/caution/extreme) |
| Market Breadth | Bar + zone label with exact thresholds |
| FTD status | Confirmed/unconfirmed with explanation |
| Macro event | Warning when CPI/Fed/NFP/PCE/GDP within 2 trading days |
| Sector table | All 11 sectors: 1m%, 3m%, TT score (N/8), Stage 2 ✅/❌ — sorted by 3m performance |
| Near-pivot watchlist | Stocks within ±5% of pivot (live price check) |
| Active signals | With regime-suppression reason when no signals |

### 2C. Earlier Session Work (for context)

- Full 48-file system built from scratch (scanner, regime, VCP, alerts, dashboard, backtest)
- CI/CD pipeline: `.github/workflows/docker-publish.yml` — lint + pytest gate before Docker build
- Unit test suite: `tests/test_vcp_gating.py`, `test_alert_formatter.py`, `test_position_sizer.py`, `conftest.py`
- Intraday breakout engine with volume-projection (5m bars, projected to full-day equivalent)
- GBP/USD dual currency display with FX fallback detection
- Live stop recalculation using intraday entry price (not stale stored stop_pct)

---

## 3. Current Architecture — 48-File Map

```
MarkMinervini/
├── main.py                          ← Master entry; scheduler startup; run_full_scan()
├── config/
│   └── settings.py                  ← All thresholds; VPS_IP; DISTRIBUTION_DAY_MIN_DROP
├── data/
│   ├── cache.py                     ← SQLite cache; TTL constants; delete/purge
│   ├── fetcher.py                   ← yfinance OHLCV; Finnhub; fetch_intraday_ohlcv
│   ├── fundamentals.py              ← Finnhub metrics + quarterly; AV EARNINGS fallback; rate limiter
│   ├── earnings_calendar.py         ← Earnings block/warn gate (5-day / 14-day)
│   ├── economic_calendar.py         ← High-impact event detection (CPI/Fed/NFP/PCE/GDP)
│   ├── news_fetcher.py              ← Finnhub news for AI sentiment
│   └── sec_edgar.py                 ← SEC EDGAR management quality summaries
├── screening/
│   ├── universe.py                  ← S&P 500 + Russell 1000 tickers
│   ├── rs_calculator.py             ← RS percentile rank (IBD method); rs_line_new_high()
│   ├── trend_template.py            ← 8-point Minervini Trend Template; NaN guard
│   └── fundamentals_filter.py       ← Hard gates: EPS ≥20%, Rev ≥15%, margin non-declining
├── market_intelligence/
│   ├── regime_detector.py           ← BULL/NEUTRAL/BEAR; SPY diagnostics; dist_days 0.2% min
│   ├── breadth_monitor.py           ← % S&P 500 above 200-SMA; cached 6h
│   ├── sector_analyzer.py           ← 11-sector ETF Stage 2; rs_rating=100 for ETFs; period=2y
│   └── ai_analyst.py                ← Ollama LLM for news catalyst, earnings quality, sector
├── patterns/
│   ├── vcp_detector.py              ← 12-step VCP; MIN_BASE_TRADING_DAYS=60; breakout gate
│   ├── pivot_calculator.py          ← Enrich VCP with pivot zone detail
│   └── pocket_pivot.py              ← Pocket pivot volume pattern detection
├── risk/
│   ├── position_sizer.py            ← GBP-first sizing; FX conversion; aggression_factor
│   └── stop_manager.py              ← Trail stops; update open positions
├── alerts/
│   ├── telegram_bot.py              ← send_message(); is_telegram_available()
│   └── alert_formatter.py           ← format_breakout_alert(); format_morning_briefing();
│                                       format_bear_market_alert(regime=dict)
├── scheduler/
│   └── job_runner.py                ← APScheduler; ALL jobs have day_of_week="mon-fri"
├── database/
│   └── db.py                        ← SQLite init; signals, watchlist, positions, cache tables
├── dashboard.py                     ← Streamlit 6-page dashboard (overhauled Live Dashboard)
├── backtesting/
│   ├── backtest.py                  ← Walk-forward backtest (vectorbt)
│   └── metrics.py                   ← CAGR, Sharpe, Sortino, Expectancy
├── tests/
│   ├── conftest.py                  ← sys.path setup
│   ├── test_vcp_gating.py           ← VCP invariant tests
│   ├── test_alert_formatter.py      ← Formatter output tests
│   └── test_position_sizer.py       ← GBP/USD sizing invariant tests
├── requirements.txt                 ← Production deps
├── requirements-test.txt            ← pytest + numpy + pandas (CI only)
├── Dockerfile                       ← Multi-arch (amd64 + arm64)
└── .github/workflows/docker-publish.yml  ← CI: compileall + pytest → Docker build
```

---

## 4. Key Settings / Thresholds (config/settings.py)

```python
# Trend Template
RS_MINIMUM = 70                  # RS percentile gate
HIGH_PROXIMITY_THRESHOLD = 0.75  # price >= 52wk_high * 0.75
LOW_DISTANCE_THRESHOLD = 1.30    # price >= 52wk_low * 1.30

# VCP
VCP_SCORE_MIN = 80               # hard gate for alert
MIN_BASE_TRADING_DAYS = 60       # minimum base length (was wrongly 15 before fix)
BREAKOUT_VOLUME_RATIO = 1.4      # volume >= 1.4× 50-day avg on breakout

# Regime
DISTRIBUTION_DAY_MIN_DROP = 0.002   # 0.2% minimum (IBD definition — CRITICAL FIX)
DISTRIBUTION_DAYS_DANGER = 5        # suppress all signals
DISTRIBUTION_DAYS_CAUTION = 3       # 50% sizing
DISTRIBUTION_LOOKBACK = 25          # sessions window
VIX_DANGER = 35                     # suppress signals
VIX_CAUTION = 25                    # 50% sizing
BREADTH_BULL = 60                   # healthy
BREADTH_WEAK = 40                   # 75% sizing
BREADTH_BEAR = 20                   # suppress

# Risk
RISK_PER_TRADE_PCT = 0.015          # from env var
MAX_STOP_PCT = 0.08                  # 8% max stop
MAX_POSITION_PCT = 0.20             # 20% max single position
```

---

## 5. Regime Dict Shape (after fix)

`detect_regime()` now returns:

```python
{
    # Core
    "regime": "BULL" | "NEUTRAL" | "BEAR",
    "aggression_factor": float,      # 0.0 – 1.0
    "signals_allowed": bool,
    "vix_level": float,
    "breadth_pct": float | None,
    "distribution_days": int,
    "ftd_confirmed": bool,
    "high_impact_event_imminent": bool,
    "regime_summary": str,           # human-readable explanation

    # SPY diagnostics (NEW — for dashboard and audit trail)
    "spy_close": float | None,
    "spy_sma50": float | None,
    "spy_sma150": float | None,
    "spy_sma200": float | None,
    "spy_above_sma200": bool | None,
    "spy_ma_stack_ok": bool | None,  # sma50 > sma150 > sma200
    "spy_last_date": str | None,     # "2026-05-23"
    "bear_gate": bool,               # True only when SPY < SMA200
}
```

---

## 6. Remaining Known Issues / Next Steps

### 6A. IMMEDIATE — Verify After Redeploy

After deploying commit `c6bf3b0`:

1. **Click "🔄 Force Regime Refresh"** in dashboard sidebar — this clears the stale SQLite cache entries and triggers fresh recomputation.

2. **Check distribution days** — with the 0.2% IBD minimum correctly applied, the count should drop from 7. If it stays at 7, these are genuine distribution days from the volatile April–May 2026 tariff period. In that case, the BEAR call is *accurate* (though still debatable given SPY is 10.5% above its 200-SMA). The BEAR trigger is due to distribution days, NOT SPY below SMA200.

3. **Check sector ✅ marks** — after cache clear, Technology (XLK), Energy (ELE) etc. should now show ✅ given their +28.7% and +8.7% 3m performance.

### 6B. HIGH — Should be tackled next session

| Priority | File | Issue |
|---|---|---|
| HIGH | `regime_detector.py` | Distribution days ≥5 triggers BEAR even when SPY is 10% above SMA200. Consider requiring BOTH: dist_days ≥5 AND SPY within 5% of SMA200, to avoid false BEAR in technically-healthy markets |
| HIGH | `config/settings.py` | `VPS_IP` is not yet in Hostinger Docker Manager env vars — add it so dashboard links work |
| HIGH | `screening/rs_calculator.py` | No minimum universe coverage check: if <80% of universe loads (API failure), RS rankings are meaningless but scan continues silently |
| HIGH | `main.py` | `_get_ticker_sector()` is called twice per ticker in the scan loop: once at vcp_score≥70 (watchlist) and once at score≥80 (alert gate). Cache hit means no extra network call, but it's still a code smell |

### 6C. MEDIUM — Paper trading calibration

| Priority | Item |
|---|---|
| MEDIUM | Verify whether VCP score≥80 threshold is correctly calibrated for current settings (MIN_BASE_TRADING_DAYS=60 is stricter — fewer setups will pass, which is correct but may need score recalibration) |
| MEDIUM | Run `python main.py --test-mode` on the VPS to confirm end-to-end pipeline produces at least one watchlist candidate |
| MEDIUM | `data/fundamentals.py` — `_parse_quarterly` EPS label matching is fragile (relies on string matching `"eps"`, `"earningspersharediluted"` etc). Finnhub label formats vary by company; should log how often this returns None |
| MEDIUM | `data/fetcher.py` — no central Finnhub rate limiter here (only in fundamentals.py now). If other modules call Finnhub directly, they bypass it |

### 6D. LOW / FUTURE

| Priority | Item |
|---|---|
| LOW | Add `pandas_market_calendars` for US trading day checks so jobs also skip US holidays (Memorial Day, Thanksgiving, etc.) — currently only weekday filtering is done |
| LOW | `screening/rs_calculator.py` — add minimum universe coverage gate (abort if <80% loaded) |
| LOW | `backtesting/backtest.py` — walk-forward backtest was built but never run against live VCP parameters to validate the VCP score≥80 threshold |
| LOW | Add regime transition Telegram alerts ("BULL → NEUTRAL", "NEUTRAL → BEAR") so you see when the regime changes without watching the dashboard |
| LOW | `dashboard.py` System Status page — show next scheduled job fire times (APScheduler can report these) |

---

## 7. Security Constraint (MUST preserve in all future work)

**Real credentials must NEVER be committed to GitHub.**

- `.env.example` contains placeholder values only
- Real credentials are entered in **Hostinger Docker Manager → Environment Variables UI**
- Local testing uses a `.env` file (in `.gitignore`)

The required env vars are documented in `.env.example`. Do not add their real values to any file that git tracks.

---

## 8. CI/CD Pipeline

```
Push to main
    └── lint-and-test job (ubuntu-latest)
          ├── pip install -r MarkMinervini/requirements-test.txt
          ├── python -m compileall -q MarkMinervini/
          └── pytest MarkMinervini/tests/ -v --tb=short
               └── [only on pass] →
    └── build-and-push job
          ├── docker buildx (linux/amd64 + linux/arm64)
          └── push to ghcr.io/sudeep-sasikumar/markminervini:latest
```

Hostinger pulls `:latest` on each redeploy.

---

## 9. Scheduler Schedule (all Mon–Fri BST, fixed in this session)

| Time (BST) | Job | Notes |
|---|---|---|
| 08:00 | Data refresh + RS + Breadth | Downloads OHLCV, computes RS rankings, computes breadth |
| 09:00 | Market intelligence | Regime + sectors + macro; clears stale caches first |
| 11:00 | Full scan #1 | Universe → Trend → Fundamentals → VCP → Alerts |
| 13:00 | Morning briefing | Telegram summary with accurate time-to-open |
| 13:30–21:00 | Intraday check (every 15m) | 5m bars; volume projection; breakout confirmation |
| 15:30 | Full scan #2 | |
| 19:00 | Full scan #3 | |
| 21:15 | Post-market wrap | Stop updates + daily summary |
| Sunday 10:00 | Weekly AI + backtest | Management quality analysis |

---

## 10. Recommended First Actions in Next Session

```
1. git pull  (get latest from GitHub)

2. Redeploy on Hostinger (if not already done with c6bf3b0)

3. Open dashboard → click "🔄 Force Regime Refresh" in sidebar

4. Verify:
   - Distribution days < 5 (should be — unless genuinely elevated)
   - At least some sectors showing ✅ Stage 2 (XLK, XLE likely)
   - SPY panel shows correct price vs SMA200

5. If distribution days is still elevated (say 4–7 even with 0.2% fix):
   - Consider raising DISTRIBUTION_DAYS_DANGER from 5 to 7 OR
   - Adding the compound gate: dist_days ≥ 5 AND spy_above_sma200 = False

6. Add VPS_IP to Hostinger env vars so dashboard links work

7. Run: python main.py --test-mode  on VPS to verify full pipeline
```

---

## 11. Git History Reference

```
c6bf3b0  Dashboard overhaul + startup cache clearing
8cac696  Fifth review: 13 bug fixes - false BEAR alert, weekend scheduler, sector gate
ed8f87c  Fix test_vcp_gating.py: correct detect_vcp() call signature
2c75bfb  Add CI compile check, unit test suite, and lint-and-test gate
f247607  Fourth review fixes: FX truthfulness, intraday stop_pct, macro warning
3f45f96  Third review P0 fixes: GBP/USD display, intraday msg, SQLite migrations
bbd9ac3  Second review pass: 10 targeted fixes
[initial] Full 48-file system build
```

---

*Generated at end of 24 May 2026 session. Next session: import this file, redeploy, verify dashboard, then tackle section 6B items.*
