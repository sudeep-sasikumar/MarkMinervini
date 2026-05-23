# Hostinger VPS Deployment — 8 Steps, No SSH Required

## Prerequisites
- Hostinger VPS (any plan — even the smallest works)
- GitHub repository: https://github.com/sudeep-sasikumar/MarkMinervini
- Your `.env` values ready (Telegram token, Finnhub key, account size)

---

## Step 1: Push code to GitHub
```bash
git add .
git commit -m "Deploy SEPA system"
git push origin main
```

## Step 2: Log in to hPanel
Go to: https://hpanel.hostinger.com → VPS → Manage

## Step 3: Install Docker OS
VPS → Manage → **Change OS** → search "Docker" → select **Ubuntu 24.04 + Docker**
- Takes 2–3 minutes. Docker is pre-installed automatically.

## Step 4: Open Docker Manager
VPS → Manage → **Docker Manager** → Click **"Compose from URL"**

## Step 5: Paste your GitHub docker-compose.yml URL
```
https://raw.githubusercontent.com/sudeep-sasikumar/MarkMinervini/main/docker-compose.yml
```

## Step 6: Name the project
Give it a name: **minervini-sepa**

## Step 7: Add environment variables
In the Environment Variables section, paste all of the following (one per line):
```
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
FINNHUB_API_KEY=your_finnhub_key_here
ALPHA_VANTAGE_KEY=your_alpha_vantage_key_here
ACCOUNT_EQUITY_GBP=50000
RISK_PER_TRADE_PCT=0.015
```

## Step 8: Deploy
Click **Deploy**. Wait 2–3 minutes for:
1. Docker to pull the python:3.11-slim and ollama/ollama images
2. Python dependencies to install
3. System to start

**YOUR DASHBOARD IS NOW LIVE AT: `http://YOUR_VPS_IP:8501`**

---

## First-Time: Pull the AI Model
After deployment, you need to pull the Ollama model once:
```
# In Docker Manager → ollama container → Terminal (or SSH):
docker exec ollama ollama pull llama3.2:3b
```
This downloads ~2GB. After this, Ollama works offline forever.

---

## Update After Code Changes
1. Push code changes to GitHub: `git push origin main`
2. Docker Manager → your project → click **"Redeploy"**
3. It pulls the latest code from GitHub and restarts automatically.

No SSH. No terminal. No Linux commands.

---

## Verify It's Working
1. Open `http://YOUR_VPS_IP:8501` in your browser
2. Check the System Status page — API health checks show ✅
3. Within 60 seconds of deployment, a Telegram message should arrive confirming startup
4. The morning briefing arrives at 1:00 PM BST every trading day

---

## Firewall Note
Hostinger VPS may have a firewall blocking port 8501.
If the dashboard is unreachable, go to: VPS → Manage → Firewall → Add rule:
- Port: 8501, Protocol: TCP, Action: Allow
