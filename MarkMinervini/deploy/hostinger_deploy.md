# Hostinger VPS Deployment — 10 Steps, No SSH Required

## How it works
Every `git push` to `main` triggers a GitHub Actions workflow that:
1. Builds the Docker image for both x86 and ARM64
2. Pushes it to `ghcr.io/sudeep-sasikumar/markminervini:latest`

Hostinger then **pulls** this pre-built image — no build step on the VPS at all.

---

## One-time setup: Make the Docker image public

After your first `git push`, GitHub Actions builds and pushes the image.
You then need to make it publicly pullable (so Hostinger can pull without credentials):

1. Go to: https://github.com/sudeep-sasikumar?tab=packages
2. Click on **markminervini**
3. Package Settings (bottom right) → **Change visibility** → **Public**
4. Confirm

This is a one-time step. The image stays public forever unless you change it.

---

## Deployment steps

### Step 1: Push code to GitHub
```bash
git push origin main
```
Wait ~3 minutes for GitHub Actions to build and push the image.
Check progress at: https://github.com/sudeep-sasikumar/MarkMinervini/actions

### Step 2: Log in to hPanel
Go to: https://hpanel.hostinger.com → VPS → Manage

### Step 3: Install Docker OS
VPS → Manage → **Change OS** → search "Docker" → select **Ubuntu 24.04 + Docker**
Takes 2–3 minutes. Docker is pre-installed automatically.

### Step 4: Open Docker Manager
VPS → Manage → **Docker Manager** → Click **"Compose from URL"**

### Step 5: Paste the docker-compose.yml URL
```
https://raw.githubusercontent.com/sudeep-sasikumar/MarkMinervini/main/docker-compose.yml
```

### Step 6: Name the project
Give it a name: **minervini-sepa**

### Step 7: Add environment variables
In the Environment Variables section, paste all of the following:
```
TELEGRAM_BOT_TOKEN=8945620098:AAEfNLKMvAR7giXJyAb-Li0E2Fkhx_yk4AU
TELEGRAM_CHAT_ID=1304666970
FINNHUB_API_KEY=d88tim1r01qs9ff649cgd88tim1r01qs9ff649d0
ALPHA_VANTAGE_KEY=7SX32XWZ361QY19O
ACCOUNT_EQUITY_GBP=2000
RISK_PER_TRADE_PCT=0.015
```

### Step 8: Deploy
Click **Deploy**. The VPS will:
1. Pull `ghcr.io/sudeep-sasikumar/markminervini:latest` (~500MB, takes ~1 min)
2. Pull `ollama/ollama`
3. Start both containers

**YOUR DASHBOARD IS NOW LIVE AT: `http://YOUR_VPS_IP:8501`**

---

## First-time: Pull the AI model
After deployment, pull the Ollama model once (needed for AI analysis):

In Docker Manager → **ollama** container → **Terminal** (or SSH):
```bash
docker exec ollama ollama pull llama3.2:3b
```
Downloads ~2GB. After this, Ollama works offline forever.

---

## Update after code changes
1. Push changes to GitHub: `git push origin main`
2. Wait ~3 min for GitHub Actions to rebuild the image
3. Hostinger Docker Manager → project → click **"Redeploy"**

No SSH. No terminal. No Linux commands.

---

## Firewall: open port 8501
If the dashboard is unreachable, open port 8501:
VPS → Manage → Firewall → Add rule:
- Port: 8501, Protocol: TCP, Action: Allow

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `No such image: ...` | Image is private — make it public (see One-time setup above) |
| Dashboard not loading | Check VPS firewall — port 8501 must be open |
| No Telegram messages | Verify env vars were entered correctly in Docker Manager |
| AI analysis offline | Run: `docker exec ollama ollama pull llama3.2:3b` |
| GitHub Actions failing | Check Actions tab: https://github.com/sudeep-sasikumar/MarkMinervini/actions |
