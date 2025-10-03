# WordFlux Cockpit - Integrated with Job Queue

## Overview

The WordFlux Cockpit is a visual workflow management system that integrates with the WordFlux job queue. It provides:

- **Visual Board**: 5-column marketing workflow (Backlog → In Progress → Waiting Approval → Scheduled → Published)
- **Agent Control**: Automated actions that queue jobs for processing
- **Real-time Updates**: Server-Sent Events (SSE) for live board updates
- **Job Queue Integration**: All actions create jobs processed by WordFlux workers
- **Slack Notifications**: Automatic notifications on state changes

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Browser   │────▶│   FastAPI   │────▶│    Redis    │
│     (UI)    │ SSE │   Cockpit   │     │   Queue +   │
└─────────────┘◀────└─────────────┘     │   PubSub    │
                           │             └─────────────┘
                           │                    │
                           ▼                    ▼
                    ┌─────────────┐     ┌─────────────┐
                    │  Job Queue  │◀────│   Workers   │
                    │   Manager   │     │  (Agents)   │
                    └─────────────┘     └─────────────┘
```

## Installation

### 1. Install Dependencies

```bash
# System packages
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip redis-server nginx

# Start Redis
sudo systemctl enable --now redis-server

# Python environment
cd /home/ubuntu
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install fastapi uvicorn redis
```

### 2. Configure Environment

Create or update `/home/ubuntu/wordflux.env`:

```bash
# Redis Configuration
REDIS_URL=redis://localhost:6379/0
QUEUE_MODE=redis

# API Configuration
PORT=8080

# Slack (optional)
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL

# AWS (if using S3 artifacts)
AWS_REGION=us-east-1
ARTIFACT_BUCKET=your-bucket
```

### 3. Deploy with Systemd

```bash
# Copy service file
sudo cp /home/ubuntu/playbooks/cockpit/wordflux-cockpit.service /etc/systemd/system/

# Reload and start
sudo systemctl daemon-reload
sudo systemctl enable --now wordflux-cockpit
sudo systemctl status wordflux-cockpit
```

### 4. Configure Nginx

```bash
# Copy nginx config
sudo cp /home/ubuntu/playbooks/cockpit/nginx-wordflux-cockpit.conf /etc/nginx/sites-available/wordflux-cockpit

# Enable site
sudo ln -sf /etc/nginx/sites-available/wordflux-cockpit /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default  # Remove default site

# Test and reload
sudo nginx -t
sudo systemctl reload nginx
```

### 5. Open Firewall

```bash
# For AWS EC2: Update Security Group to allow HTTP (port 80)
# For UFW:
sudo ufw allow 80/tcp
```

## Usage

### Access the Cockpit

Open in browser: `http://your-server-ip/`

### UI Components

**Left Panel - Agent Control**:
- Autopilot toggle (auto-execute actions)
- Selected card display
- Suggested actions for current state
- Create new card form

**Right Panel - Workflow Board**:
- 5 columns showing workflow states
- Click cards to select them
- Visual indicators for job status

### Workflow Actions

| Column | Available Actions | Next State |
|--------|------------------|------------|
| Backlog | `start_work` | In Progress |
| In Progress | `send_for_review`, `pause` | Waiting Approval |
| Waiting Approval | `approve`, `request_changes` | Scheduled / In Progress |
| Scheduled | `publish_now`, `reschedule` | Published |
| Published | `report_kpis` | Published |

### Autopilot Mode

When enabled, moving a card to a new column automatically executes the first suggested action for that column.

## API Endpoints

### Board Management
- `GET /` - Main UI
- `GET /board/state` - Get all columns and cards
- `POST /board/card` - Create new card
- `POST /board/move` - Move card to column

### Agent Operations
- `GET /agent/suggest?card_id=X` - Get suggested actions
- `POST /agent/act` - Execute action (queues job)
- `POST /agent/autopilot` - Toggle autopilot mode

### Queue Status
- `GET /health` - Health check with queue info
- `GET /queue/status` - Detailed queue statistics

### Events
- `GET /events/recent` - Last 50 events
- `GET /events/stream` - SSE stream for real-time updates

## Redis Inspection Commands

```bash
# View board state
redis-cli KEYS "wf:board:*"
redis-cli LRANGE wf:board:col:Backlog 0 -1

# View queued jobs
redis-cli LRANGE queue:default 0 10
redis-cli GET queue:stats

# Monitor events
redis-cli SUBSCRIBE wf:events
redis-cli LRANGE wf:events:recent 0 20

# Check idempotency keys
redis-cli KEYS "idempotency:*"
```

## Testing

### Run Unit Tests
```bash
cd /home/ubuntu
source .venv/bin/activate
pytest tests/integration/test_cockpit_integration.py -v
```

### Manual Testing with cURL
```bash
# Create a card
curl -X POST http://localhost:8080/board/card \
  -H "Content-Type: application/json" \
  -d '{"title":"Test Card"}'

# Execute an action
curl -X POST http://localhost:8080/agent/act \
  -H "Content-Type: application/json" \
  -d '{"card_id":"c-12345","action":"start_work"}'

# Watch SSE stream
curl -N http://localhost:8080/events/stream
```

## Troubleshooting

### Cockpit Not Loading
```bash
# Check service status
sudo systemctl status wordflux-cockpit
sudo journalctl -u wordflux-cockpit -n 50

# Check nginx
sudo systemctl status nginx
sudo tail -f /var/log/nginx/wordflux-cockpit.error.log

# Test locally
curl http://localhost:8080/health
```

### Jobs Not Processing
```bash
# Check Redis connection
redis-cli ping

# Check queue depth
redis-cli LLEN queue:default

# Run a worker manually
cd /home/ubuntu
source .venv/bin/activate
python -m scripts.run_worker --continuous
```

### No Live Updates
- Ensure SSE endpoint is not buffered (nginx config)
- Check browser console for SSE errors
- Verify Redis pub/sub is working: `redis-cli SUBSCRIBE wf:events`

## Next Steps

1. **Add More Agents**: Create agents for each cockpit action
2. **Integrate External Services**: Connect Linear, GitHub, Slack
3. **Add Metrics**: Track action execution times and success rates
4. **Implement Playbooks**: Create multi-step workflows
5. **Add Authentication**: Secure the cockpit with auth middleware

## Development

### Running Locally
```bash
cd /home/ubuntu/playbooks/cockpit
export REDIS_URL=redis://localhost:6379/0
export QUEUE_MODE=memory  # For local testing
python wordflux_cockpit.py
```

### Adding New Actions

1. Add action to column mapping in `suggest_actions()`
2. Create agent in `src/agents/`
3. Register agent in `src/agents/__init__.py`
4. Map action to agent in `ACTION_AGENTS` dict
5. Add state transition logic in `agent_act()`

### Customizing Columns

Edit `DEFAULT_COLUMNS` in the cockpit script or modify Redis:
```python
redis-cli SET wf:board:columns '["Todo","Doing","Done"]'
```

## License

Part of the WordFlux system.