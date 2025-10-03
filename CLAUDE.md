# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WordFlux is an event-driven agent system with Redis-backed job queue, S3 artifact storage, dual-service architecture (API + Cockpit), and 14 specialized agents. The system combines a visual Kanban-style workflow interface with a REST API for programmatic access, enabling both human operators and automated systems to manage content workflows through specialized agent execution.

**Key Components:**
- **API Service** (port 8080): Job queue REST API for webhooks and automation
- **Cockpit UI** (port 80/8081): Visual workflow management with real-time updates
- **Worker Service**: Background processor executing 14 specialized agents
- **Planner Agent**: Autonomous card creation and workflow progression with APScheduler
- **WIP Enforcement**: Work-in-progress limits preventing bottlenecks

## Core Commands

### Development Setup
```bash
# Create and activate virtual environment
python3 -m venv .venv && source .venv/bin/activate

# Install all dependencies (including FastAPI/Uvicorn)
make dev

# Run linter and formatter
make lint
make format

# Run tests
make test                              # Quick test run
make coverage                          # Full coverage report
pytest tests/unit/test_specific.py    # Run specific test
pytest -k "test_function_name"        # Run test by name pattern
```

### Running the System
```bash
# Start API server (FastAPI)
.venv/bin/python -m src.api.main      # Runs on http://localhost:8080

# Start Cockpit service
export PORT=8081
.venv/bin/python playbooks/cockpit/wordflux_cockpit.py  # Runs on http://localhost:8081

# Run a worker to process jobs
make worker                            # Process one job and exit
.venv/bin/python -m scripts.run_worker --continuous  # Keep processing

# Execute an agent locally for testing
make run-agent AGENT=echo              # Test echo agent
.venv/bin/python -m scripts.run_agent --agent slack_notifier --message "Test"

# Trigger planner agent manually
curl -X POST http://localhost:8081/agent/run_planner

# Toggle autopilot mode
curl -X POST http://localhost:8081/agent/autopilot -d '{"enabled":true}'

# Requeue stuck jobs (Redis mode)
.venv/bin/python scripts/requeue_processing.py
```

## Architecture

### Agent System
- **Base Pattern**: All agents inherit from `BaseAgent` (src/core/base_agent.py) and implement `run(payload)` method
- **Job Queue**: Supports memory mode (local dev) or Redis mode (production) via `QUEUE_MODE` environment variable
- **Worker Processing**: Workers poll the queue and execute agents based on the `agent` field in job payloads
- **Artifact Storage**: S3-based storage with configurable encryption (S3-managed AES256 or KMS)
- **Agent Registry**: 14 agents registered with aliases for flexibility (src/agents/__init__.py)

### Cockpit Architecture
- **Visual Board**: 5-column Kanban (Backlog → In Progress → Waiting Approval → Scheduled → Published)
- **Agent-First Design**: Agents create and move cards, not manual user actions
- **WIP Limits**: Enforced work-in-progress constraints (default: 2 cards in "In Progress")
- **Autonomous Planning**: APScheduler runs planner_agent periodically (default: every hour)
- **Real-Time Updates**: Server-Sent Events (SSE) via Redis pub/sub
- **Nginx Reverse Proxy**: Port 80 → 8081 with SSE-optimized buffering

### Key Components
- `src/api/main.py`: FastAPI endpoint with idempotency support, receives events and queues jobs
- `playbooks/cockpit/wordflux_cockpit.py`: Visual workflow management interface (1024 lines)
- `src/core/queue.py`: Abstraction layer for memory/Redis queues with at-most-once delivery
- `src/core/worker.py`: Job processing loop that executes agents
- `src/core/ledger.py`: Job history tracking with Redis persistence
- `src/core/locks.py`: Distributed locking for WIP limit enforcement
- `src/core/playbook.py`: YAML playbook execution engine
- `src/core/artifacts.py`: S3 artifact management with presigned URL generation
- `src/core/metrics.py`: Metrics collection and reporting system

### Agent Types (14 Total)

**Core Agents**:
- **echo**: Simple test agent for development and queue connectivity verification
- **slack_notifier**: Sends notifications to Slack channels with CSV export URLs and event updates
- **stripe_disputes**: Processes Stripe dispute CSV exports with S3 artifact storage
- **linear_connector**: Updates Linear issues with workflow state synchronization
- **playbook_runner**: Executes multi-step YAML workflows with conditional logic

**Workflow Action Agents**:
- **task_starter**: Starts work on cards (Backlog → In Progress), respects WIP limits
- **review_requester**: Requests review/approval (In Progress → Waiting Approval)
- **content_approver**: Approves content for publication (Waiting Approval → Scheduled)
- **content_publisher**: Publishes finalized content (Scheduled → Published)
- **metrics_reporter**: Reports KPIs and efficiency metrics (Published, no state change)
- **change_requester**: Requests changes with feedback (Waiting Approval → In Progress)
- **scheduler**: Reschedules tasks with new publication time (Scheduled, updates metadata)
- **task_pauser**: Pauses/resumes tasks with pause metadata (any column, adds metadata)

**Autonomous Agents**:
- **planner_agent**: Creates cards from templates and progresses workflows autonomously (runs via APScheduler every hour, configurable via WF_PLANNER_INTERVAL)

## Environment Configuration

Copy `.env.example` to `.env` (or use `wordflux.env` in production) and configure:
- **Queue**: Set `QUEUE_MODE=redis` and `REDIS_URL=redis://localhost:6379/0` for production
- **API**: Set `API_HOST=0.0.0.0` and `API_PORT=8080` for API service
- **Cockpit**: Set `PORT=8081` for cockpit service (nginx proxies 80 → 8081)
- **WIP Limits**: Set `WF_WIP_LIMIT=2` to configure work-in-progress constraints
- **Planner**: Set `WF_PLANNER_INTERVAL=3600` for scheduler interval (seconds)
- **AWS**: Configure `AWS_REGION`, `ARTIFACT_BUCKET` for S3 storage
- **Artifact Encryption**: Use `ARTIFACT_ENCRYPTION=s3` for S3-managed encryption (recommended)
- **GitHub**: Set `GITHUB_TOKEN`, `GITHUB_OWNER`, `GITHUB_REPO` if using GitHub integrations
- **Slack**: Set `SLACK_WEBHOOK_URL` for Slack notifications
- **LLM Provider**: Set `WF_LLM_PROVIDER=bedrock` (default) or `anthropic` for chat functionality
  - **Bedrock (Default)**: Uses AWS Bedrock Converse API
    - Requires `AWS_REGION` (e.g., `us-east-1`)
    - Requires IAM role with `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` permissions
    - Optional: `ANTHROPIC_BEDROCK_MODEL` (default: `anthropic.claude-sonnet-4-5-20250929-v1:0` - Sonnet 4.5)
    - See `docs/bedrock-iam-policy.json` for sample IAM policy
  - **Anthropic Direct**: Uses Anthropic Messages API
    - Requires `ANTHROPIC_API_KEY`
    - Optional: `ANTHROPIC_MODEL` (default: `claude-sonnet-4-5-20250929`)
- **System Prompt**: Set `WF_SYSTEM_PROMPT_VERSION=v4_bedrock` (recommended) for ultra-short PT-BR prompt
  - Options: `v1_assistive`, `v2_direct_enhanced`, `v3_ultrashort`, `v4_bedrock`

## Testing Strategy

- **Unit Tests**: Mirror src/ structure in tests/unit/, use pytest fixtures for mocking
- **Integration Tests**: End-to-end flows in tests/integration/ with lightweight stubs
- **Coverage Targets**: ≥90% for src/agents, ≥80% overall
- **Queue Testing**: Reset with `set_default_queue(None)` between test cases when changing modes

## Development Workflow

1. Feature branches follow the pattern in recent commits (conventional commits style)
2. All new agents require documentation in `docs/agents/<agent-name>.md`
3. Configuration via YAML files in `configs/agents/` (when implemented)
4. Python 3.11+, type hints required, dataclasses for contracts
5. Formatting with ruff (black-compatible), 4-space indentation

## Deployment Notes

### Production Environment
- **Public URL**: http://wordflux.3-228-174-188.nip.io (cockpit on port 80)
- **API URL**: http://wordflux.3-228-174-188.nip.io:8080 (API direct access)
- **Instance**: EC2 t4g.micro (ARM64, Ubuntu 22.04)

### Services
- **API Service**: Runs via Uvicorn on port 8080 (systemd: `wordflux-api.service`)
- **Cockpit Service**: Runs via Uvicorn on port 8081, nginx proxies port 80 → 8081 (systemd: `wordflux-cockpit.service`)
- **Worker Service**: Runs in continuous mode (systemd: `wordflux-worker.service`)

### Configuration
- Redis idempotency keys have 1-hour TTL
- S3 presigned URLs expire after 1 hour by default (configurable via ARTIFACT_URL_TTL)
- WIP limits default to 2 cards (configurable via WF_WIP_LIMIT)
- Planner runs every hour by default (configurable via WF_PLANNER_INTERVAL)
- SSE buffering disabled in nginx for real-time updates

### Health Checks
- **Cockpit**: `GET http://wordflux.3-228-174-188.nip.io/health` - Returns queue mode, Redis status, queue depth
- **API**: `GET http://wordflux.3-228-174-188.nip.io:8080/health` - Returns Redis connectivity, queue mode

### Service Management
```bash
# View all services
sudo systemctl status wordflux-{api,cockpit,worker} --no-pager

# Restart services
sudo systemctl restart wordflux-api
sudo systemctl restart wordflux-cockpit
sudo systemctl restart wordflux-worker

# View logs
sudo journalctl -u wordflux-api -f
tail -f /var/log/wordflux-cockpit.log
sudo journalctl -u wordflux-worker -f
```

### Nginx Configuration
- Config file: `/etc/nginx/sites-available/wordflux`
- Enabled via: `/etc/nginx/sites-enabled/wordflux`
- SSE endpoint (`/events/stream`) has buffering disabled for real-time updates
- Security headers applied: X-Frame-Options, X-Content-Type-Options, X-XSS-Protection

**Complete deployment guide**: See [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md)
## Testing with Cockpit

### Access Cockpit UI

**Production**:
```bash
# Open in browser
open http://wordflux.3-228-174-188.nip.io/
```

**Local Development**:
```bash
# Start cockpit service
export PORT=8081
.venv/bin/python playbooks/cockpit/wordflux_cockpit.py

# Access in browser
open http://localhost:8081/
```

### Create Cards via Planner

```bash
# Trigger planner manually (production)
curl -X POST http://wordflux.3-228-174-188.nip.io/agent/run_planner \
  -H "Content-Type: application/json" \
  -d '{"mode":"full"}'

# Response: {"success":true,"job_id":"cockpit-xxxxx","mode":"full"}

# Trigger planner locally
curl -X POST http://localhost:8081/agent/run_planner
```

### Toggle Autopilot Mode

```bash
# Enable autopilot (production)
curl -X POST http://wordflux.3-228-174-188.nip.io/agent/autopilot \
  -H "Content-Type: application/json" \
  -d '{"enabled":true}'

# Disable autopilot
curl -X POST http://localhost:8081/agent/autopilot \
  -d '{"enabled":false}'

# Check autopilot status
curl http://localhost:8081/health | jq '.autopilot'
```

### Create Card Manually (Testing Only)

```bash
# Note: In production, planner agent creates cards autonomously
# Manual creation is available for testing purposes

curl -X POST http://localhost:8081/board/card \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test Card - Manual Creation",
    "intent": "Testing workflow progression",
    "meta": {
      "priority": "high",
      "assigned_to": "dev-team"
    }
  }'
```

### Execute Agent Actions

```bash
# Get suggested actions for a card
curl "http://localhost:8081/agent/suggest?card_id=c-abc123"

# Execute an action (e.g., start work)
curl -X POST http://localhost:8081/agent/act \
  -H "Content-Type: application/json" \
  -d '{
    "card_id": "c-abc123",
    "action": "start_work"
  }'

# Response: {"success":true,"job_id":"cockpit-xxxxx"}
```

### Monitor Real-Time Updates

```bash
# Subscribe to SSE stream
curl -N http://localhost:8081/events/stream

# You'll see events like:
# data: {"kind":"board_update","ts":...}
# data: {"kind":"agent_action","action":"start_work",...}
# data: {"kind":"job_queued","job_id":"..."}
```

### Check Queue and Board State

```bash
# Get complete board state
curl http://localhost:8081/board/state | jq

# Check queue status
curl http://localhost:8081/queue/status

# View recent events
curl http://localhost:8081/events/recent | jq
```

### Test WIP Limits

```bash
# Try to add more than 2 cards to "In Progress"
# The 3rd attempt should fail with wip_limit_exceeded event

# Create multiple test cards
for i in {1..3}; do
  curl -X POST http://localhost:8081/board/card \
    -d "{\"title\":\"WIP Test $i\"}"
done

# Try to move all to "In Progress" - 3rd should be rejected
# Watch SSE stream to see wip_limit_exceeded event
```

**Cockpit User Guide**: See [docs/guides/cockpit-user-guide.md](docs/guides/cockpit-user-guide.md) (when created)
