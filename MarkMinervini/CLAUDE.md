# MarkMinervini SEPA System — Claude Code Context

## Project Overview
A complete Mark Minervini SEPA (Specific Entry Point Analysis) momentum trading alert system.
Screens US-listed equities, sends Telegram alerts, and serves a Streamlit web dashboard.

## Architecture
- **main.py** — master entry point (starts scanner + APScheduler)
- **dashboard.py** — Streamlit web dashboard (6 pages, port 8501)
- **config/settings.py** — ALL thresholds and parameters (never hardcode elsewhere)
- **database/db.py** — SQLite schema and connection management
- **data/** — price fetching (yfinance/Finnhub), fundamentals, earnings, news, cache
- **screening/** — universe management, RS rank, 8-point trend template, fundamentals filter
- **market_intelligence/** — regime detector, breadth monitor, AI analyst (Ollama), sector analyzer
- **patterns/** — VCP detector (scored 0–100), pivot calculator, pocket pivot
- **risk/** — position sizer, stop manager
- **alerts/** — Telegram bot, alert formatter
- **scheduler/** — APScheduler jobs (BST timezone)
- **backtesting/** — vectorbt backtester with walk-forward validation
- **deploy/** — Hostinger Docker guide, Oracle fallback, quickstart

## Key Rules
- All secrets from `.env` — never hardcoded
- All external API calls wrapped in try/except — one failure never crashes the system
- Use Python's `logging` module everywhere (no print() for operational output)
- Logs to both console and `/app/logs/sepa.log`
- All thresholds configurable via `config/settings.py`
- Graceful degradation if Ollama is unreachable (AI tasks skipped, signals still sent)
- System is timezone-aware: UK/BST, US markets 13:30–21:00 BST

## Data Sources (free only)
- **yfinance** — primary price/OHLCV (no key)
- **Finnhub** free tier — fundamentals, earnings calendar, news (key in .env)
- **Alpha Vantage** free tier — fallback fundamentals (25 calls/day, key in .env)
- **SEC EDGAR** — 10-K/10-Q filings (no key)
- **Ollama llama3.2:3b** — local AI analysis (localhost:11434 or ollama:11434 in Docker)

## Screening Pipeline
Universe (~1,500 tickers) → Trend Template (8 criteria, SMA only) → RS ≥ 70 → Fundamentals
→ VCP score ≥ 80 → Earnings safety gate → Regime gate → Telegram alert + dashboard update

## VCP Scoring (0–100, alert threshold: 80)
- 90–100: ELITE (green, full size)
- 80–89: HIGH QUALITY (blue, full size)
- 70–79: MODERATE (yellow, watchlist only)
- <70: discard

## Deployment
- Local: `docker compose up` → http://localhost:8501
- Hostinger: Docker Manager → Compose from GitHub URL → paste env vars → Deploy
- See deploy/QUICKSTART.md for full instructions

## Build State
Track resumption progress in BUILD_STATE.md at project root.
