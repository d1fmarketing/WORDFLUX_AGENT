# WordFlux

**Event-Driven Agent Workflow System**

WordFlux is a production-ready, Redis-backed job queue system with visual workflow management, autonomous agents, and real-time updates. It orchestrates content operations through a Kanban-style interface while providing a robust API for programmatic access.

---

## 🎯 Overview

WordFlux combines three core services to deliver a complete workflow automation platform:

1. **API Service** (Port 8080) - Job queue for webhooks and automation
2. **Cockpit UI** (Port 80) - Visual workflow management interface
3. **Worker Service** - Background job processor executing specialized agents

The system manages end-to-end content workflows from ideation through publication, with autonomous planning, WIP limit enforcement, and real-time collaboration.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         INTERNET                                 │
└──────────────────────┬──────────────────┬───────────────────────┘
                       │                  │
                   Port 80            Port 8080
                  (Cockpit UI)       (API Service)
                       │                  │
                  ┌────▼────┐        ┌───▼────┐
                  │  Nginx  │        │FastAPI │
                  │ Reverse │        │  Job   │
                  │  Proxy  │        │ Queue  │
                  └────┬────┘        └───┬────┘
                       │                 │
                  Port 8081              │
                 ┌─────▼─────┐           │
                 │  FastAPI  │           │
                 │  Cockpit  │           │
                 │  Service  │           │
                 └─────┬─────┘           │
                       │                 │
                       └────────┬────────┘
                                │
                        ┌───────▼───────┐
                        │  Redis Queue  │
                        │  + PubSub +   │
                        │  Board State  │
                        └───────┬───────┘
                                │
                        ┌───────▼───────┐
                        │    Worker     │
                        │   Service     │
                        │ (14 Agents)   │
                        └───────────────┘
```

**Data Flow:**
1. Events arrive via API (webhooks, automation) or Cockpit UI (user actions)
2. Jobs queued in Redis with idempotency support
3. Workers poll queue and execute appropriate agents
4. Results broadcast via Redis pub/sub
5. UIs receive real-time updates via Server-Sent Events (SSE)

---

## ⚡ Quick Start

### Access Production

**Cockpit UI (Visual Interface):**
```
http://wordflux.3-228-174-188.nip.io/
```

**API Service (Programmatic Access):**
```
http://wordflux.3-228-174-188.nip.io:8080/
```

### Health Checks

```bash
# Cockpit health
curl http://wordflux.3-228-174-188.nip.io/health

# API health
curl http://wordflux.3-228-174-188.nip.io:8080/health
```

### Submit Your First Job

```bash
curl -X POST http://wordflux.3-228-174-188.nip.io:8080/event \
  -H "Content-Type: application/json" \
  -d '{
    "action": "echo",
    "payload": {"message": "Hello WordFlux!"},
    "idempotency_key": "my-first-job-123"
  }'
```

### Watch Real-Time Updates

```bash
# SSE stream (Ctrl+C to exit)
curl -N http://wordflux.3-228-174-188.nip.io/events/stream
```

---

## 🎛️ Services

### 1. Cockpit UI - Visual Workflow Management

**URL:** http://wordflux.3-228-174-188.nip.io/

**Purpose:** User-facing interface for managing content workflows through a 5-column Kanban board.

**Key Features:**
- 📋 **Workflow Board:** Backlog → In Progress → Waiting Approval → Scheduled → Published
- 🤖 **Agent Actions:** Trigger specialized agents with context-aware actions
- 🚀 **Autopilot Mode:** Autonomous workflow progression with WIP limits
- 📊 **KPI Dashboard:** Real-time metrics and efficiency tracking
- ⚡ **Live Updates:** Server-Sent Events for instant UI refresh
- 🎯 **WIP Limits:** Enforced work-in-progress constraints (default: 2 cards)

**Use Cases:**
- Content operations team managing editorial workflows
- Product managers tracking feature development
- Marketing teams coordinating campaign launches

**Documentation:** [Cockpit User Guide](docs/guides/cockpit-user-guide.md)

---

### 2. API Service - Programmatic Job Queue

**URL:** http://wordflux.3-228-174-188.nip.io:8080/

**Purpose:** REST API for webhook integration, automation, and programmatic job submission.

**Key Features:**
- 📥 **Event Ingestion:** POST /event with idempotency support
- 🔍 **Agent Discovery:** GET /skills to list available agents
- 📈 **Metrics Export:** Prometheus-compatible /metrics endpoint
- 🏥 **Health Monitoring:** Comprehensive health checks
- 🎨 **Mini Cockpit:** Simple web UI for testing and debugging

**Use Cases:**
- Webhooks from GitHub, Stripe, Linear, etc.
- Scheduled jobs via cron
- Automation scripts and integrations
- Third-party service orchestration

**Documentation:** [API Reference](docs/api/api-service.md)

---

### 3. Worker Service - Background Job Processor

**Purpose:** Continuously polls Redis queue and executes jobs using specialized agents.

**Key Features:**
- 🔄 **Continuous Processing:** Runs as systemd service
- 🔁 **Automatic Retry:** Up to 3 attempts on failure
- 📝 **Ledger Integration:** Complete job history tracking
- 🎯 **Agent Execution:** Routes jobs to 14 specialized agents
- ⚠️ **Dead Letter Queue:** Failed jobs quarantined for inspection

**Systemd Control:**
```bash
sudo systemctl status wordflux-worker
sudo systemctl restart wordflux-worker
sudo journalctl -u wordflux-worker -f
```

**Documentation:** [Worker Architecture](docs/architecture/worker.md)

---

## 🤖 Agents (14 Total)

Agents are specialized processors that execute specific workflow tasks.

### Core Agents
| Agent | Purpose | Documentation |
|-------|---------|---------------|
| **echo** | Test agent for development | [docs/agents/echo.md](docs/agents/echo.md) |
| **slack_notifier** | Send Slack notifications | [docs/agents/slack_notifier.md](docs/agents/slack_notifier.md) |
| **stripe_disputes** | Process Stripe dispute exports | [docs/agents/stripe.export_disputes.md](docs/agents/stripe.export_disputes.md) |
| **linear_connector** | Update Linear issues | [docs/agents/linear_connector.md](docs/agents/linear_connector.md) |
| **playbook_runner** | Execute YAML workflows | [docs/agents/playbook_runner.md](docs/agents/playbook_runner.md) |

### Workflow Action Agents
| Agent | Action | State Transition |
|-------|--------|------------------|
| **task_starter** | Start work | Backlog → In Progress |
| **review_requester** | Request review | In Progress → Waiting Approval |
| **content_approver** | Approve content | Waiting Approval → Scheduled |
| **content_publisher** | Publish now | Scheduled → Published |
| **metrics_reporter** | Report KPIs | Published (no change) |
| **change_requester** | Request changes | Waiting Approval → In Progress |
| **scheduler** | Reschedule | Scheduled (update time) |
| **task_pauser** | Pause/resume | Any column (add metadata) |

### Autonomous Agents
| Agent | Purpose | Scheduling |
|-------|---------|------------|
| **planner_agent** | Autonomous card creation and progression | Runs every hour (configurable) |

**Full Catalog:** [Agent Documentation](docs/agents/README.md)

---

## 🌟 Key Features

### Agent-First Architecture
The cockpit operates on an **agent-first** principle: agents create and move cards, not manual user actions. The "Create Card" button was intentionally removed—cards appear automatically via the planner agent.

### WIP Limit Enforcement
Work-In-Progress limits prevent bottlenecks. The default limit of 2 cards in "In Progress" ensures focused execution. Attempts to exceed the limit are automatically rejected with rollback.

**Configuration:**
```bash
# In wordflux.env
WF_WIP_LIMIT=2
```

### Autonomous Planning
The **planner_agent** runs on a schedule (default: every hour) to:
- Create cards from templates
- Move cards to "In Progress" (respecting WIP limits)
- Progress stalled workflows autonomously

**Manual Trigger:**
```bash
curl -X POST http://wordflux.3-228-174-188.nip.io/agent/run_planner
```

### Real-Time Updates
All state changes broadcast via Redis pub/sub and stream to browser UIs via Server-Sent Events (SSE). Updates appear instantly across all connected clients.

### Idempotency
The API supports idempotency keys to prevent duplicate job execution—critical for webhook reliability and retry logic.

```bash
curl -X POST http://wordflux.3-228-174-188.nip.io:8080/event \
  -d '{"action":"echo","payload":{},"idempotency_key":"unique-key-123"}'
# First call: Job executed
# Second call: Returns same job_id, no duplicate execution
```

---

## 📚 Documentation

### Getting Started
- [Quick Start Guide](docs/guides/quickstart.md) - 5 minutes to first job
- [Cockpit User Guide](docs/guides/cockpit-user-guide.md) - Visual interface walkthrough
- [Development Guide](docs/guides/development-guide.md) - Contributing and local setup

### Architecture
- [System Architecture](ARCHITECTURE.md) - Detailed architecture diagrams
- [Production Deployment](PRODUCTION_DEPLOYMENT.md) - Complete deployment guide
- [Data Flow Patterns](docs/architecture/data-flow.md) - How data moves through the system

### API Reference
- [API Service Endpoints](docs/api/api-service.md) - Port 8080 REST API
- [Cockpit Service Endpoints](docs/api/cockpit-service.md) - Port 80 REST API
- [Webhook Integration](docs/api/webhooks.md) - Webhook setup guide

### Agents
- [Agent Catalog](docs/agents/README.md) - All 14 agents documented
- [Creating Agents](docs/guides/agent-development.md) - Step-by-step agent creation
- [Agent Template](docs/agents/agent-template.md) - Documentation template

### Operations
- [Troubleshooting](docs/guides/troubleshooting.md) - Common issues and solutions
- [Monitoring Setup](docs/operations/monitoring.md) - Prometheus + Grafana
- [Backup & Recovery](docs/operations/backup-recovery.md) - Data protection

### Additional Resources
- [Testing Guide](tests/README.md) - Writing and running tests
- [Scripts Reference](scripts/README.md) - Utility scripts documentation
- [Playbook System](playbooks/README.md) - YAML workflow authoring
- [Queue Documentation](docs/queue.md) - Queue architecture details

---

## 🛠️ Development Setup

### Prerequisites
- Python 3.11+
- Redis 6.0+
- AWS account (for S3 artifacts)
- Node.js 22+ (for Chrome DevTools MCP testing)

### Local Installation

```bash
# Clone repository
git clone <repo-url>
cd wordflux

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt  # For development

# Configure environment
cp .env.example wordflux.env
# Edit wordflux.env with your settings

# Start Redis
sudo systemctl start redis-server

# Run tests
pytest tests/unit/ -v

# Start API server
python -m src.api.main
# Access: http://localhost:8080

# Start Cockpit (separate terminal)
export PORT=8081
python playbooks/cockpit/wordflux_cockpit.py
# Access: http://localhost:8081

# Run worker (separate terminal)
python -m scripts.run_worker --continuous
```

### Development Commands

```bash
# Run linter
make lint

# Format code
make format

# Run tests with coverage
make coverage

# Run specific agent locally
make run-agent AGENT=echo
python -m scripts.run_agent --agent slack_notifier --message "Test"
```

### Project Structure

```
wordflux/
├── src/
│   ├── agents/          # 14 agent implementations
│   ├── api/             # FastAPI job queue service
│   └── core/            # Shared utilities (queue, worker, metrics)
├── playbooks/
│   └── cockpit/         # Cockpit visual interface
├── scripts/             # Utility scripts (run_agent, query_ledger, etc.)
├── tests/
│   ├── unit/            # Fast, isolated tests
│   └── integration/     # End-to-end tests
├── docs/                # Comprehensive documentation
├── wordflux.env         # Environment configuration
└── requirements.txt     # Python dependencies
```

**Full Development Guide:** [docs/guides/development-guide.md](docs/guides/development-guide.md)

---

## 📦 Production Deployment

### Current Production Environment

**Instance:** EC2 t4g.micro (ARM64, Ubuntu 22.04)
**Public IP:** 3.228.174.188
**Domain:** wordflux.3-228-174-188.nip.io

**Services:**
- `wordflux-api.service` - API on port 8080
- `wordflux-cockpit.service` - Cockpit on port 8081 (nginx proxy on 80)
- `wordflux-worker.service` - Background worker

**Nginx Configuration:**
- `/etc/nginx/sites-available/wordflux`
- Reverse proxy: Port 80 → 8081 (cockpit)
- SSE-optimized with buffering disabled

**Service Management:**
```bash
# View all services
sudo systemctl status wordflux-* --no-pager

# Restart all services
sudo systemctl restart wordflux-{api,cockpit,worker}

# View logs
sudo journalctl -u wordflux-api -f
tail -f /var/log/wordflux-cockpit.log
```

**Complete Deployment Guide:** [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md)

---

## 🔒 Security

### Current Security Posture

⚠️ **Active Warnings:**
- Firewall (UFW) is disabled - all ports exposed
- No SSL/TLS configured - HTTP only
- No rate limiting on API endpoints

### Recommended Immediate Actions

```bash
# Enable firewall
sudo ufw default deny incoming
sudo ufw allow 22/tcp   # SSH
sudo ufw allow 80/tcp   # Cockpit
sudo ufw allow 8080/tcp # API
sudo ufw enable

# Install SSL certificate
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d wordflux.3-228-174-188.nip.io

# Configure rate limiting (edit nginx config)
limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
```

**Security Hardening Guide:** [docs/operations/security-hardening.md](docs/operations/security-hardening.md)

---

## 🧪 Testing

### Running Tests

```bash
# Quick test run
make test

# Full coverage report
make coverage

# Specific test file
pytest tests/unit/test_queue.py -v

# Integration tests
pytest tests/integration/ -v

# Watch mode (requires pytest-watch)
ptw tests/unit/
```

### Coverage Targets
- **src/agents:** ≥90%
- **src/core:** ≥80%
- **Overall:** ≥80%

**Current Coverage:**
```bash
make coverage
# View HTML report
open htmlcov/index.html
```

**Testing Guide:** [tests/README.md](tests/README.md)

---

## 📊 Monitoring

### Health Endpoints

```bash
# Cockpit health
curl http://wordflux.3-228-174-188.nip.io/health
# Returns: {"status":"ok","queue_mode":"redis","redis":true}

# API health
curl http://wordflux.3-228-174-188.nip.io:8080/health
# Returns: {"status":"healthy","redis":"connected"}
```

### Metrics

```bash
# Prometheus metrics
curl http://localhost:8080/metrics

# Queue depth
redis-cli LLEN wf:jobs:pending

# Recent events
curl http://wordflux.3-228-174-188.nip.io/events/recent | jq
```

### Logs

```bash
# All services
sudo journalctl -u wordflux-* -f

# API logs
sudo journalctl -u wordflux-api -n 100

# Cockpit logs
tail -f /var/log/wordflux-cockpit.log

# Nginx access logs
tail -f /var/log/nginx/wordflux-cockpit.access.log
```

**Monitoring Setup Guide:** [docs/operations/monitoring.md](docs/operations/monitoring.md)

---

## 🐛 Troubleshooting

### Common Issues

**Cockpit Not Loading:**
```bash
sudo systemctl status wordflux-cockpit
sudo systemctl restart wordflux-cockpit
curl http://127.0.0.1:8081/health  # Test direct
```

**Jobs Not Processing:**
```bash
redis-cli LLEN wf:jobs:pending  # Check queue depth
sudo systemctl status wordflux-worker
sudo systemctl restart wordflux-worker
```

**SSE Updates Not Working:**
```bash
# Test SSE locally
curl -N http://127.0.0.1:8081/events/stream

# Check nginx buffering disabled
grep -A 5 "/events/stream" /etc/nginx/sites-enabled/wordflux
```

**Redis Connection Issues:**
```bash
sudo systemctl status redis-server
redis-cli ping
redis-cli info replication
```

**Complete Troubleshooting Guide:** [docs/guides/troubleshooting.md](docs/guides/troubleshooting.md)

---

## 🤝 Contributing

We welcome contributions! Whether it's bug fixes, new agents, documentation improvements, or feature requests.

### Contribution Workflow

1. **Fork and Branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make Changes**
   - Follow [coding style guidelines](AGENTS.md)
   - Add tests for new functionality
   - Update documentation

3. **Test Locally**
   ```bash
   make lint
   make test
   make coverage
   ```

4. **Commit with Conventional Commits**
   ```bash
   git commit -m "feat: add new agent for X"
   git commit -m "fix: resolve queue deadlock issue"
   git commit -m "docs: update agent catalog"
   ```

5. **Submit Pull Request**
   - Include description of changes
   - Reference related issues
   - Provide test evidence
   - Screenshots for UI changes

### Adding a New Agent

1. Create agent file: `src/agents/my_agent.py`
2. Implement `BaseAgent` interface
3. Register in `src/agents/__init__.py`
4. Write tests: `tests/unit/test_agents/test_my_agent.py`
5. Document: `docs/agents/my_agent.md`

**Detailed Guide:** [docs/guides/agent-development.md](docs/guides/agent-development.md)

### Code Review Checklist

- [ ] Tests added and passing
- [ ] Documentation updated
- [ ] Type hints present
- [ ] No secrets in code
- [ ] Conventional commit messages
- [ ] Coverage maintained (≥80%)

**Repository Guidelines:** [AGENTS.md](AGENTS.md)

---

## 📄 License

[Specify license here - e.g., MIT, Apache 2.0, proprietary]

---

## 🔗 Links

### Production URLs
- **Cockpit UI:** http://wordflux.3-228-174-188.nip.io/
- **API Service:** http://wordflux.3-228-174-188.nip.io:8080/
- **API Docs:** http://wordflux.3-228-174-188.nip.io:8080/docs (if FastAPI docs enabled)

### Documentation
- [Full Documentation Index](docs/README.md)
- [Agent Catalog](docs/agents/README.md)
- [API Reference](docs/api/README.md)
- [Operations Runbook](docs/operations/runbook.md)

### Support
- **Issues:** [GitHub Issues](../../issues)
- **Discussions:** [GitHub Discussions](../../discussions)
- **Documentation:** [docs/](docs/)

---

## 📝 Recent Updates

- **2025-09-29:** Production deployment complete with dual-service architecture
- **2025-09-29:** Added autonomous planner agent with APScheduler
- **2025-09-29:** Implemented WIP limit enforcement (default: 2 cards)
- **2025-09-29:** Chrome DevTools MCP integration for E2E testing
- **2025-09-28:** Created 4 new workflow action agents (task_starter, review_requester, etc.)
- **2025-09-27:** Nginx reverse proxy configured with SSE support

See [CHANGELOG.md](CHANGELOG.md) for full version history.

---

**Built with ❤️ using FastAPI, Redis, and Claude Code**