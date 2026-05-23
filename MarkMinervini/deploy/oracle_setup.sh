#!/bin/bash
# Oracle Cloud Free Tier — ARM Ubuntu setup script
# Fallback deployment option if Hostinger expires
# Run as: sudo bash oracle_setup.sh

set -e

echo "=== Minervini SEPA — Oracle Cloud ARM Setup ==="

# 1. System update
apt-get update && apt-get upgrade -y

# 2. Install Docker
apt-get install -y ca-certificates curl gnupg lsb-release
mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 3. Enable Docker to start on boot
systemctl enable docker
systemctl start docker

# 4. Open firewall port 8501 (Streamlit dashboard)
iptables -I INPUT -p tcp --dport 8501 -j ACCEPT
iptables -I INPUT -p tcp --dport 22 -j ACCEPT
# Make persistent (Ubuntu)
apt-get install -y iptables-persistent
netfilter-persistent save

# 5. Clone the repository
cd /opt
git clone https://github.com/sudeep-sasikumar/MarkMinervini.git sepa
cd sepa

# 6. Create .env file (edit this before running)
cat > .env << 'EOF'
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
FINNHUB_API_KEY=your_finnhub_key_here
ALPHA_VANTAGE_KEY=your_alpha_vantage_key_here
ACCOUNT_EQUITY_GBP=50000
RISK_PER_TRADE_PCT=0.015
OLLAMA_URL=http://ollama:11434
DB_PATH=/app/data/sepa.db
LOG_PATH=/app/logs/sepa.log
EOF

echo ""
echo "⚠️  IMPORTANT: Edit /opt/sepa/.env with your real API keys before continuing!"
echo "   nano /opt/sepa/.env"
echo ""
read -p "Press ENTER after editing .env to continue deployment..."

# 7. Start with Docker Compose
docker compose up -d

# 8. Pull Ollama model
echo "Pulling Ollama llama3.2:3b model (this may take 5-10 minutes)..."
sleep 10  # wait for ollama container to start
docker exec ollama ollama pull llama3.2:3b

# 9. Create systemd service for auto-restart on reboot
cat > /etc/systemd/system/sepa.service << EOF
[Unit]
Description=Minervini SEPA Signal System
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/sepa
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down

[Install]
WantedBy=multi-user.target
EOF

systemctl enable sepa
systemctl start sepa

echo ""
echo "=== Deployment Complete ==="
PUBLIC_IP=$(curl -s ifconfig.me 2>/dev/null || echo "YOUR_SERVER_IP")
echo "Dashboard: http://${PUBLIC_IP}:8501"
echo "Logs: docker compose logs -f sepa-system"
echo "Stop: docker compose down"
echo "Update: git pull && docker compose up -d --build"
