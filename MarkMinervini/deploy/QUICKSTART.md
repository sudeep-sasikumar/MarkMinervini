# QUICKSTART — Local + Hostinger Deployment

## What you need before starting
- [ ] Telegram bot token + chat ID (from @BotFather)
- [ ] Finnhub API key (free at https://finnhub.io — takes 2 minutes)
- [ ] Docker Desktop installed (local) or Hostinger VPS (production)
- [ ] Optional: Alpha Vantage key (free 25 calls/day at alphavantage.co)

---

## LOCAL QUICKSTART (5 steps)

```bash
# 1. Clone the repository
git clone https://github.com/sudeep-sasikumar/MarkMinervini.git
cd MarkMinervini

# 2. Configure your credentials
cp .env.example .env
# Edit .env: add your Telegram token, Finnhub key, account size

# 3. Start the system (requires Docker Desktop)
docker compose up

# 4. Open the dashboard
# http://localhost:8501

# 5. First-time: pull the AI model (in a second terminal)
docker exec ollama ollama pull llama3.2:3b
```

A Telegram message will arrive within 60 seconds confirming the system is running.

---

## HOSTINGER PRODUCTION DEPLOYMENT (8 steps)

See `deploy/hostinger_deploy.md` for full step-by-step with screenshots.

**Summary:**
1. Push code to GitHub
2. Log into hPanel → VPS → Manage
3. Change OS to Ubuntu 24.04 + Docker
4. Docker Manager → Compose from URL
5. Paste your GitHub docker-compose.yml URL
6. Name project: minervini-sepa
7. Add environment variables from your .env file
8. Click Deploy

**Dashboard live at:** `http://YOUR_VPS_IP:8501`

---

## VERIFY THE SYSTEM IS WORKING

1. **Dashboard loads** at http://localhost:8501 (or VPS IP)
2. **System Status page** shows all API health checks ✅
3. **Telegram message** arrives confirming startup
4. **Morning briefing** arrives at 1:00 PM BST (UK time)
5. **Signals page** shows results after the first scan at 11:00 AM BST

---

## FIRST SCAN (manual)

To trigger a scan immediately (without waiting for 11:00 BST):
```bash
# Local:
python main.py --test-mode   # one scan, one Telegram message, exits

# Docker:
docker exec minervini_sepa python main.py --test-mode
```

---

## UPDATE THE SYSTEM

```bash
git pull origin main
docker compose up -d --build   # rebuilds and restarts with new code
```

On Hostinger: Docker Manager → project → Redeploy

---

## TROUBLESHOOTING

**Dashboard not loading:**
- Check logs: `docker compose logs sepa-system`
- Ensure port 8501 is open in your VPS firewall

**No Telegram messages:**
- Verify TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
- Test: `python -c "from alerts.telegram_bot import send_message; send_message('test')"`

**AI analysis showing "offline":**
- Pull the model: `docker exec ollama ollama pull llama3.2:3b`
- Check Ollama: `docker exec ollama ollama list`

**No signals generated:**
- The system may be in BEAR MARKET mode (SPY below 200-SMA)
- Check the Live Dashboard for regime status
- In test mode, signals are shown even in bear markets

---

## SYSTEM SCHEDULE (BST / UK Time)

| Time | Job |
|------|-----|
| 08:00 | Data refresh (OHLCV, RS, breadth) |
| 09:00 | Regime + sector analysis |
| 11:00 | Full screening run #1 |
| 13:00 | **Morning Telegram briefing** |
| 13:30–21:00 | Intraday checks every 15 minutes |
| 15:30 | Full screening run #2 |
| 19:00 | Full screening run #3 |
| 21:15 | Post-market summary |
| Sunday 10:00 | Weekly AI + backtest validation |

---

## DATA SOURCES (all free)

| Source | Used for | Key required? |
|--------|---------|--------------|
| yfinance | OHLCV price data | No |
| Finnhub | Fundamentals, earnings, news | Yes (free) |
| Alpha Vantage | Fallback fundamentals | Yes (free, 25/day) |
| SEC EDGAR | 10-K/10-Q filings | No |
| Ollama llama3.2:3b | AI analysis | No (runs locally) |

---

*Minervini SEPA System v3.0 — For educational and research purposes.*
*All trading involves risk of capital loss.*
