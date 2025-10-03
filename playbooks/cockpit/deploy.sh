#!/bin/bash
# WordFlux Cockpit - Quick Deployment Script

set -e

echo "🚀 WordFlux Cockpit Deployment"
echo "=============================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as ubuntu user
if [ "$USER" != "ubuntu" ]; then
    echo -e "${RED}❌ Please run as ubuntu user${NC}"
    exit 1
fi

# Function to check command status
check_status() {
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ $1${NC}"
    else
        echo -e "${RED}❌ $1 failed${NC}"
        exit 1
    fi
}

# Step 1: Check Redis
echo -e "\n${YELLOW}1. Checking Redis...${NC}"
if systemctl is-active --quiet redis-server; then
    echo -e "${GREEN}✅ Redis is running${NC}"
else
    echo "Starting Redis..."
    sudo systemctl start redis-server
    check_status "Redis started"
fi

# Step 2: Check Python environment
echo -e "\n${YELLOW}2. Checking Python environment...${NC}"
if [ -d "/home/ubuntu/.venv" ]; then
    echo -e "${GREEN}✅ Virtual environment exists${NC}"
else
    echo "Creating virtual environment..."
    cd /home/ubuntu
    python3 -m venv .venv
    check_status "Virtual environment created"
fi

# Step 3: Install Python dependencies
echo -e "\n${YELLOW}3. Installing Python dependencies...${NC}"
source /home/ubuntu/.venv/bin/activate
pip install -q fastapi uvicorn redis
check_status "Python dependencies installed"

# Step 4: Check environment file
echo -e "\n${YELLOW}4. Checking environment configuration...${NC}"
if [ -f "/home/ubuntu/wordflux.env" ]; then
    echo -e "${GREEN}✅ Environment file exists${NC}"
    # Check for required variables
    if grep -q "REDIS_URL" /home/ubuntu/wordflux.env; then
        echo -e "${GREEN}✅ REDIS_URL configured${NC}"
    else
        echo "REDIS_URL=redis://localhost:6379/0" >> /home/ubuntu/wordflux.env
        echo "Added REDIS_URL to environment"
    fi
    if grep -q "PORT" /home/ubuntu/wordflux.env; then
        echo -e "${GREEN}✅ PORT configured${NC}"
    else
        echo "PORT=8080" >> /home/ubuntu/wordflux.env
        echo "Added PORT to environment"
    fi
else
    echo "Creating environment file..."
    cat > /home/ubuntu/wordflux.env << EOF
# WordFlux Cockpit Configuration
REDIS_URL=redis://localhost:6379/0
QUEUE_MODE=redis
PORT=8080

# Optional: Slack notifications
# SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL

# Optional: AWS for artifacts
# AWS_REGION=us-east-1
# ARTIFACT_BUCKET=your-bucket
EOF
    check_status "Environment file created"
fi

# Step 5: Deploy systemd service
echo -e "\n${YELLOW}5. Deploying systemd service...${NC}"
sudo cp /home/ubuntu/playbooks/cockpit/wordflux-cockpit.service /etc/systemd/system/
check_status "Service file copied"

sudo systemctl daemon-reload
check_status "Systemd reloaded"

# Stop if running, then start
sudo systemctl stop wordflux-cockpit 2>/dev/null || true
sudo systemctl enable wordflux-cockpit
sudo systemctl start wordflux-cockpit
check_status "Cockpit service started"

# Step 6: Configure Nginx
echo -e "\n${YELLOW}6. Configuring Nginx...${NC}"
if [ ! -f "/etc/nginx/sites-available/wordflux-cockpit" ]; then
    sudo cp /home/ubuntu/playbooks/cockpit/nginx-wordflux-cockpit.conf /etc/nginx/sites-available/wordflux-cockpit
    check_status "Nginx config copied"
fi

# Enable site if not already enabled
if [ ! -L "/etc/nginx/sites-enabled/wordflux-cockpit" ]; then
    sudo ln -sf /etc/nginx/sites-available/wordflux-cockpit /etc/nginx/sites-enabled/
    check_status "Nginx site enabled"
fi

# Remove default site if exists
if [ -L "/etc/nginx/sites-enabled/default" ]; then
    sudo rm -f /etc/nginx/sites-enabled/default
    echo "Removed default nginx site"
fi

# Test and reload nginx
sudo nginx -t &>/dev/null
check_status "Nginx configuration valid"

sudo systemctl reload nginx
check_status "Nginx reloaded"

# Step 7: Health check
echo -e "\n${YELLOW}7. Running health check...${NC}"
sleep 2  # Give service time to start

# Check local service
if curl -s http://localhost:8080/health | grep -q '"status":"ok"'; then
    echo -e "${GREEN}✅ Cockpit API is healthy${NC}"
else
    echo -e "${RED}❌ Cockpit API health check failed${NC}"
    echo "Check logs: sudo journalctl -u wordflux-cockpit -n 50"
fi

# Get public IP
PUBLIC_IP=$(curl -s http://checkip.amazonaws.com 2>/dev/null || echo "unknown")

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}✨ WordFlux Cockpit Deployed Successfully!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "📍 Access your cockpit at:"
echo "   Local:  http://localhost/"
if [ "$PUBLIC_IP" != "unknown" ]; then
    echo "   Public: http://${PUBLIC_IP}/"
fi
echo ""
echo "📊 Useful commands:"
echo "   Status:  sudo systemctl status wordflux-cockpit"
echo "   Logs:    sudo journalctl -u wordflux-cockpit -f"
echo "   Restart: sudo systemctl restart wordflux-cockpit"
echo "   Redis:   redis-cli MONITOR"
echo ""
echo "🔒 Remember to:"
echo "   1. Open port 80 in your firewall/security group"
echo "   2. Configure SLACK_WEBHOOK_URL in wordflux.env for notifications"
echo "   3. Run workers to process queued jobs:"
echo "      python -m scripts.run_worker --continuous"
echo ""