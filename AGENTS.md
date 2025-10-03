# AGENTS.md

**WordFlux Agent System Documentation**

This file provides comprehensive guidance for AI coding agents and developers working with the WordFlux agent system. WordFlux is an event-driven workflow automation platform with a Redis-backed job queue, S3 artifact storage, and specialized agents that orchestrate content workflows through a visual Kanban interface.

---

## Table of Contents

1. [System Architecture](#system-architecture)
2. [Agent Categories](#agent-categories)
3. [Creating a New Agent](#creating-a-new-agent)
4. [Agent Registry & Aliases](#agent-registry--aliases)
5. [Agent Catalog](#agent-catalog)
6. [Payload & Result Schemas](#payload--result-schemas)
7. [Testing Patterns](#testing-patterns)
8. [Integration Guide](#integration-guide)
9. [Best Practices](#best-practices)

---

## System Architecture

### Core Components

```
┌─────────────────────────────────────────────────────────────────┐
│                        WordFlux System                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────────┐      ┌──────────────┐      ┌──────────────┐  │
│  │  API Service │      │   Cockpit    │      │    Worker    │  │
│  │  (port 8080) │      │  (port 8081) │      │   Service    │  │
│  └──────┬───────┘      └──────┬───────┘      └──────┬───────┘  │
│         │                     │                     │           │
│         │                     │                     │           │
│         └─────────────────────┴─────────────────────┘           │
│                               │                                 │
│                    ┌──────────▼──────────┐                      │
│                    │   Redis Job Queue    │                      │
│                    │  + Event Pub/Sub     │                      │
│                    └──────────┬───────────┘                      │
│                               │                                 │
│                    ┌──────────▼──────────┐                      │
│                    │    15+ Agents       │                      │
│                    │  (Specialized Tasks) │                      │
│                    └──────────┬───────────┘                      │
│                               │                                 │
│           ┌───────────────────┼───────────────────┐             │
│           │                   │                   │             │
│      ┌────▼────┐         ┌────▼────┐        ┌────▼────┐        │
│      │S3 Bucket│         │ Slack   │        │ Linear  │        │
│      │Artifacts│         │  API    │        │   API   │        │
│      └─────────┘         └─────────┘        └─────────┘        │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

### Key Infrastructure

- **Job Queue**: Redis-backed queue supporting memory mode (dev) and Redis mode (production)
- **Event System**: Server-Sent Events (SSE) via Redis pub/sub for real-time UI updates
- **Artifact Storage**: S3-based storage with AES256 encryption
- **Workflow Board**: 5-column Kanban (Backlog → In Progress → Waiting Approval → Scheduled → Published)
- **WIP Enforcement**: Distributed locks via Redis to prevent workflow bottlenecks
- **Autonomous Scheduling**: APScheduler runs planner_agent periodically (default: hourly)

---

## Agent Categories

WordFlux agents are organized into three primary categories:

### 1. Infrastructure Agents
**Purpose**: Connect external systems, handle data import/export, provide foundational services

**Examples**:
- `echo` - Test agent for queue connectivity verification
- `stripe_disputes` - Process Stripe dispute CSV exports with S3 storage
- `slack_notifier` - Send notifications to Slack channels
- `linear_connector` - Sync workflow state with Linear issues
- `playbook_runner` - Execute multi-step YAML workflows

### 2. Workflow Orchestration Agents
**Purpose**: Move cards through Kanban workflow states, enforce business rules

**Examples**:
- `task_starter` - Move cards from Backlog → In Progress (respects WIP limits)
- `review_requester` - Move cards In Progress → Waiting Approval
- `content_approver` - Move cards Waiting Approval → Scheduled
- `content_publisher` - Move cards Scheduled → Published
- `change_requester` - Request changes (Waiting Approval → In Progress)
- `scheduler` - Reschedule tasks with new publication time
- `task_pauser` - Pause/resume tasks with metadata updates
- `metrics_reporter` - Report KPIs and efficiency metrics

### 3. Autonomous Agents
**Purpose**: Operate on schedules or triggers, create/modify cards automatically

**Examples**:
- `planner_agent` - Creates cards from templates, progresses workflow (runs via APScheduler)
- `board_operator` - Chat integration for board operations (create, move, update, comment)

---

## Creating a New Agent

### Step 1: Create Agent Class

All agents **must** inherit from `BaseAgent` and implement the `run(payload) -> result` method.

**File**: `src/agents/your_agent.py`

```python
#!/usr/bin/env python3
"""Your Agent - Brief description of what it does."""

import logging
from typing import Dict, Any
from src.core.base_agent import BaseAgent, Payload, Result

logger = logging.getLogger(__name__)


class YourAgent(BaseAgent):
    """Detailed description of agent purpose and behavior."""

    def __init__(self):
        super().__init__("your_agent")

    def run(self, payload: Payload) -> Result:
        """
        Execute agent logic.

        Expected payload:
        {
            "field1": "value1",  # Description
            "field2": 123,       # Description
            "optional_field": "value"  # Optional parameter
        }

        Returns:
        {
            "success": True,
            "result_field": "value",
            "message": "Human-readable message"
        }
        """
        try:
            # Extract and validate inputs
            field1 = payload.get("field1")
            if not field1:
                raise ValueError("field1 is required")

            # Perform agent logic
            result = self._process(field1)

            # Return structured result
            return {
                "success": True,
                "result_field": result,
                "message": f"Successfully processed: {field1}"
            }

        except Exception as e:
            logger.error(f"Agent failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to process: {e}"
            }

    def _process(self, data: str) -> str:
        """Private helper method for core logic."""
        # Implementation here
        return data.upper()


def build_agent() -> YourAgent:
    """Factory function to create agent instance."""
    return YourAgent()


__all__ = ["YourAgent", "build_agent"]
```

### Step 2: Register Agent

**File**: `src/agents/__init__.py`

```python
from src.agents.your_agent import build_agent as build_your_agent
from src.core.registry import register_agent

# Register with primary name
register_agent("your_agent", build_your_agent)

# Optional: Register aliases for flexibility
register_agent("your.alternate.name", build_your_agent)

# Add to __all__ export
__all__ = [..., "build_your_agent"]
```

### Step 3: Create Tests

**File**: `tests/unit/test_your_agent.py`

```python
import pytest
from src.agents.your_agent import YourAgent


def test_your_agent_success():
    """Test successful execution."""
    agent = YourAgent()
    result = agent.run({"field1": "test"})

    assert result["success"] is True
    assert result["result_field"] == "TEST"
    assert "Successfully processed" in result["message"]


def test_your_agent_missing_field():
    """Test error handling for missing required field."""
    agent = YourAgent()
    result = agent.run({})

    assert result["success"] is False
    assert "error" in result
    assert "field1 is required" in result["error"]


def test_your_agent_exception_handling():
    """Test graceful error handling."""
    agent = YourAgent()
    result = agent.run({"field1": None})

    assert result["success"] is False
    assert "error" in result
```

### Step 4: Create Documentation

**File**: `docs/agents/your_agent.md`

```markdown
# your_agent

## Purpose
Brief description of what this agent does and when to use it.

## Workflow Position
Describe where in the Kanban workflow this agent operates.

## Payload Schema
\`\`\`json
{
  "field1": "string (required)",
  "field2": "integer (optional)"
}
\`\`\`

## Result Schema
\`\`\`json
{
  "success": "boolean",
  "result_field": "any",
  "message": "string"
}
\`\`\`

## Usage Example
\`\`\`bash
# Queue a job via API
curl -X POST http://localhost:8080/jobs \\
  -H "Content-Type: application/json" \\
  -d '{
    "agent": "your_agent",
    "payload": {"field1": "test"}
  }'

# Run locally for testing
.venv/bin/python -m scripts.run_agent \\
  --agent your_agent \\
  --payload '{"field1":"test"}'
\`\`\`

## Configuration
Environment variables:
- `YOUR_AGENT_ENABLED` - Enable/disable agent (default: true)
- `YOUR_AGENT_TIMEOUT` - Execution timeout in seconds (default: 30)

## Dependencies
- External API credentials (if applicable)
- S3 bucket access (if storing artifacts)
- Redis connection (for state/locks)
```

---

## Agent Registry & Aliases

The agent registry (`src/core/registry.py`) provides flexible agent lookup via primary names and aliases.

### Registration Pattern

```python
# Primary registration
register_agent("agent_name", build_agent_function)

# Alias registration (multiple names for same agent)
register_agent("agent.alternate.name", build_agent_function)
register_agent("shortname", build_agent_function)
```

### Lookup Examples

```python
from src.core.registry import create_agent, available_agents

# Create agent by primary name
agent = create_agent("slack_notifier")

# Create agent by alias
agent = create_agent("slack.notify")

# List all registered agents
agents = available_agents()
# Returns: ['echo', 'slack_notifier', 'slack.notify', 'task_starter', ...]
```

---

## Agent Catalog

### Infrastructure Agents

#### echo
- **Purpose**: Test agent for queue connectivity and development
- **Payload**: `{"message": "string"}`
- **Returns**: `{"agent": "echo", "message": "...", "characters": N, "words": N}`
- **Use Cases**: Queue verification, integration testing, debugging

#### stripe_disputes
- **Purpose**: Process Stripe dispute CSV exports with S3 artifact storage
- **Payload**: `{"csv_url": "https://...", "export_type": "disputes"}`
- **Returns**: `{"success": true, "artifact_url": "s3://...", "record_count": N}`
- **Dependencies**: S3 bucket configured, Stripe export URL

#### slack_notifier
- **Purpose**: Send formatted notifications to Slack channels
- **Payload**: `{"message": "string", "channel": "#channel", "csv_url": "optional"}`
- **Returns**: `{"success": true, "channel": "#channel", "timestamp": "..."}`
- **Dependencies**: `SLACK_WEBHOOK_URL` environment variable

#### linear_connector
- **Purpose**: Sync workflow state with Linear issues
- **Payload**: `{"card": {...}, "action": "create|update|close"}`
- **Returns**: `{"success": true, "linear_id": "LIN-123", "url": "..."}`
- **Dependencies**: `LINEAR_API_KEY` environment variable

#### playbook_runner
- **Purpose**: Execute multi-step YAML workflows with conditional logic
- **Payload**: `{"playbook": "playbook_name", "variables": {...}}`
- **Returns**: `{"success": true, "steps_completed": N, "results": [...]}`
- **Use Cases**: Complex workflows, multi-agent orchestration

### Workflow Orchestration Agents

#### task_starter
- **Purpose**: Start work on cards (Backlog → In Progress), respect WIP limits
- **Payload**: `{"card": {...}, "action": "start_work"}`
- **Returns**: `{"success": true, "task_id": "...", "task_url": "..."}`
- **Side Effects**: Creates Linear/GitHub issue, sends Slack notification

#### review_requester
- **Purpose**: Request review/approval (In Progress → Waiting Approval)
- **Payload**: `{"card": {...}, "reviewers": ["user1", "user2"]}`
- **Returns**: `{"success": true, "review_url": "...", "notified": [...]}`

#### content_approver
- **Purpose**: Approve content for publication (Waiting Approval → Scheduled)
- **Payload**: `{"card": {...}, "approved_by": "user"}`
- **Returns**: `{"success": true, "scheduled_at": "2025-10-05T10:00:00Z"}`

#### content_publisher
- **Purpose**: Publish finalized content (Scheduled → Published)
- **Payload**: `{"card": {...}, "publish_now": true}`
- **Returns**: `{"success": true, "published_at": "...", "url": "..."}`
- **Side Effects**: Updates CMS, sends notifications, records metrics

#### metrics_reporter
- **Purpose**: Report KPIs and efficiency metrics (Published, no state change)
- **Payload**: `{"time_range": "24h|7d|30d", "metrics": ["throughput", "cycle_time"]}`
- **Returns**: `{"success": true, "metrics": {...}, "report_url": "..."}`

#### change_requester
- **Purpose**: Request changes with feedback (Waiting Approval → In Progress)
- **Payload**: `{"card": {...}, "feedback": "string", "requested_by": "user"}`
- **Returns**: `{"success": true, "feedback_added": true}`

#### scheduler
- **Purpose**: Reschedule tasks with new publication time (Scheduled column)
- **Payload**: `{"card": {...}, "scheduled_at": "2025-10-05T14:00:00Z"}`
- **Returns**: `{"success": true, "scheduled_at": "..."}`

#### task_pauser
- **Purpose**: Pause/resume tasks with pause metadata (any column)
- **Payload**: `{"card": {...}, "action": "pause|resume", "reason": "string"}`
- **Returns**: `{"success": true, "paused": true|false, "reason": "..."}`

### Autonomous Agents

#### planner_agent
- **Purpose**: Create cards from templates, progress workflow autonomously
- **Trigger**: APScheduler (default: every hour, configurable via `WF_PLANNER_INTERVAL`)
- **Payload**: `{"mode": "create|move|full", "template_name": "daily_content"}`
- **Returns**: `{"success": true, "created": N, "moved": M, "cards": [...]}`
- **Features**:
  - Creates cards from predefined templates
  - Moves cards from Backlog → In Progress (respects WIP limits)
  - Emits SSE events for real-time UI updates

#### board_operator
- **Purpose**: Chat integration for board operations (create, move, update, comment)
- **Trigger**: Chat LLM tool calls via `/chat` endpoint
- **Payload**: `{"action": "create_card|move_card|update_card|comment_card", ...}`
- **Returns**: `{"success": true, "card": {...}}`
- **Features**:
  - Async job execution (no direct API calls from chat)
  - Fuzzy search for card lookup by title
  - WIP limit enforcement with rollback on failure

---

## Payload & Result Schemas

### Standard Payload Fields

All agents should support these optional fields for consistency:

```python
{
    "agent": "agent_name",          # Agent identifier (auto-populated)
    "job_id": "uuid",               # Job tracking ID (auto-populated)
    "idempotency_key": "string",    # Prevent duplicate execution
    "timeout": 60,                  # Execution timeout (seconds)
    "retry": true,                  # Enable retry on failure
    "meta": {                       # Custom metadata
        "source": "api|cockpit|scheduler",
        "user": "username",
        "session_id": "uuid"
    }
}
```

### Standard Result Fields

All agents should return results with this structure:

```python
{
    "success": True|False,          # Execution status (required)
    "message": "string",            # Human-readable message (required)
    "error": "string",              # Error details if success=False
    "data": {...},                  # Agent-specific result data
    "artifacts": [                  # S3 artifact URLs
        {"type": "csv", "url": "s3://..."},
        {"type": "report", "url": "s3://..."}
    ],
    "metrics": {                    # Performance metrics
        "duration_ms": 1234,
        "records_processed": 100,
        "api_calls": 5
    }
}
```

### Error Handling Pattern

```python
def run(self, payload: Payload) -> Result:
    try:
        # Validate inputs
        required_field = payload.get("required_field")
        if not required_field:
            raise ValueError("required_field is mandatory")

        # Execute logic
        result = self._process(required_field)

        return {
            "success": True,
            "data": result,
            "message": "Operation completed successfully"
        }

    except ValueError as e:
        # Validation errors
        logger.warning(f"Validation error: {e}")
        return {"success": False, "error": str(e), "message": "Invalid input"}

    except Exception as e:
        # Unexpected errors
        logger.error(f"Agent failed: {e}", exc_info=True)
        return {"success": False, "error": str(e), "message": "Agent execution failed"}
```

---

## Testing Patterns

### Unit Testing

**Location**: `tests/unit/test_agent_name.py`

```python
import pytest
from unittest.mock import Mock, patch
from src.agents.your_agent import YourAgent


@pytest.fixture
def agent():
    """Fixture to create agent instance."""
    return YourAgent()


def test_successful_execution(agent):
    """Test normal execution path."""
    payload = {"field1": "value"}
    result = agent.run(payload)

    assert result["success"] is True
    assert "data" in result


def test_missing_required_field(agent):
    """Test validation error handling."""
    payload = {}
    result = agent.run(payload)

    assert result["success"] is False
    assert "required" in result["error"].lower()


@patch("src.agents.your_agent.external_api_call")
def test_external_api_integration(mock_api, agent):
    """Test external API integration with mocking."""
    mock_api.return_value = {"status": "ok"}

    payload = {"field1": "value"}
    result = agent.run(payload)

    assert result["success"] is True
    mock_api.assert_called_once()
```

### Integration Testing

**Location**: `tests/integration/test_agent_workflow.py`

```python
import pytest
from src.core.queue import get_default_queue, set_default_queue
from src.core.worker import Worker
from src.core.registry import create_agent


@pytest.fixture(autouse=True)
def reset_queue():
    """Reset queue between tests."""
    set_default_queue(None)
    yield
    set_default_queue(None)


def test_end_to_end_workflow():
    """Test complete workflow: enqueue → process → verify."""
    # Enqueue job
    queue = get_default_queue()
    job = {
        "job_id": "test-123",
        "agent": "echo",
        "payload": {"message": "test"}
    }
    queue.publish(job)

    # Process job
    worker = Worker(queue, poll_interval=0.1)
    result = worker.process_next_job()

    # Verify result
    assert result is not None
    assert result["success"] is True
    assert result["message"] == "test"
```

### Manual Testing

```bash
# Test agent locally with run_agent script
.venv/bin/python -m scripts.run_agent \
  --agent echo \
  --payload '{"message":"Hello, World!"}'

# Test via API
curl -X POST http://localhost:8080/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "agent": "echo",
    "payload": {"message": "Hello via API"}
  }'

# Monitor job execution
curl http://localhost:8080/queue/status

# View job history
curl http://localhost:8080/ledger/recent
```

---

## Integration Guide

### Queueing Jobs

**Via API** (recommended for external systems):
```bash
curl -X POST http://localhost:8080/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "agent": "agent_name",
    "payload": {"key": "value"},
    "idempotency_key": "unique-key"
  }'
```

**Via Python** (for internal workflows):
```python
from src.core.queue import get_default_queue
from uuid import uuid4

queue = get_default_queue()
job = {
    "job_id": str(uuid4()),
    "agent": "agent_name",
    "payload": {"key": "value"}
}
queue.publish(job)
```

**Via Cockpit** (for UI-triggered actions):
```python
# In cockpit endpoint handler
from src.core.queue import get_default_queue

@app.post("/agent/run")
def run_agent(request: AgentRequest):
    queue = get_default_queue()
    job = {
        "job_id": f"cockpit-{uuid4().hex[:8]}",
        "agent": request.agent,
        "payload": request.payload
    }
    queue.publish(job)
    return {"job_id": job["job_id"], "status": "queued"}
```

### S3 Artifact Storage

```python
from src.core.artifacts import store_artifact, get_artifact_url

# Store artifact (automatically uploads to S3)
artifact_key = store_artifact(
    content=data,
    filename="report.json",
    content_type="application/json"
)

# Generate presigned URL (1-hour expiry)
url = get_artifact_url(artifact_key)

# Return in agent result
return {
    "success": True,
    "artifacts": [{"type": "report", "url": url}]
}
```

### Emitting SSE Events

```python
# In cockpit-based agents
from playbooks.cockpit.wordflux_cockpit import emit_event

emit_event("board_update", {
    "action": "card_moved",
    "card_id": "c-12345",
    "from": "Backlog",
    "to": "In Progress"
})

# In standalone agents (requires Redis)
import redis
import json

r = redis.from_url(os.getenv("REDIS_URL"))
event_data = json.dumps({
    "type": "agent_completed",
    "agent": "your_agent",
    "timestamp": datetime.now(timezone.utc).isoformat()
})
r.publish("wf:events", event_data)
```

### Using Distributed Locks

```python
from src.core.locks import acquire_lock, release_lock

# Acquire lock (prevents concurrent execution)
lock_acquired = acquire_lock("resource:unique-key", timeout=30)
if not lock_acquired:
    return {"success": False, "error": "Resource locked"}

try:
    # Critical section
    result = process_resource()
finally:
    # Always release lock
    release_lock("resource:unique-key")
```

---

## Best Practices

### 1. **Idempotency**
Agents should produce the same result when called multiple times with the same inputs.

```python
def run(self, payload: Payload) -> Result:
    idempotency_key = payload.get("idempotency_key")

    if idempotency_key:
        # Check if already processed
        cached_result = self._get_cached_result(idempotency_key)
        if cached_result:
            return cached_result

    # Process and cache result
    result = self._process(payload)

    if idempotency_key:
        self._cache_result(idempotency_key, result)

    return result
```

### 2. **Graceful Degradation**
Agents should handle missing dependencies gracefully.

```python
def run(self, payload: Payload) -> Result:
    # Check if optional integration available
    linear_enabled = bool(os.getenv("LINEAR_API_KEY"))

    if linear_enabled:
        # Use Linear integration
        result = self._create_linear_issue(payload)
    else:
        # Fallback to internal task
        result = self._create_internal_task(payload)

    return result
```

### 3. **Structured Logging**
Use structured logging for easy debugging.

```python
logger.info(
    f"Processing job",
    extra={
        "agent": self.name,
        "job_id": payload.get("job_id"),
        "card_id": payload.get("card", {}).get("id")
    }
)
```

### 4. **Timeouts & Retries**
Implement timeouts for external API calls.

```python
import requests

response = requests.get(
    url,
    timeout=10,  # 10 second timeout
    headers={"Authorization": f"Bearer {api_key}"}
)

# Retry with exponential backoff
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_data(url):
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()
```

### 5. **Metrics & Observability**
Track agent performance metrics.

```python
import time

def run(self, payload: Payload) -> Result:
    start_time = time.time()

    try:
        result = self._process(payload)
        duration_ms = (time.time() - start_time) * 1000

        result["metrics"] = {
            "duration_ms": duration_ms,
            "records_processed": len(result.get("data", []))
        }

        return result
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        logger.error(f"Agent failed after {duration_ms}ms: {e}")
        raise
```

### 6. **Configuration via Environment**
Use environment variables for configuration.

```python
import os

class YourAgent(BaseAgent):
    def __init__(self):
        super().__init__("your_agent")

        # Load config from environment
        self.api_key = os.getenv("YOUR_AGENT_API_KEY")
        self.timeout = int(os.getenv("YOUR_AGENT_TIMEOUT", "30"))
        self.enabled = os.getenv("YOUR_AGENT_ENABLED", "true").lower() == "true"
```

### 7. **Type Hints**
Use type hints for clarity and IDE support.

```python
from typing import Dict, Any, List, Optional

def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Process payload and return result."""
    pass

def _process(self, data: List[str]) -> Optional[str]:
    """Helper method with type hints."""
    if not data:
        return None
    return data[0]
```

---

## Agent Development Checklist

When creating a new agent, verify:

- [ ] Inherits from `BaseAgent`
- [ ] Implements `run(payload) -> result` method
- [ ] Has factory function `build_agent() -> AgentClass`
- [ ] Registered in `src/agents/__init__.py`
- [ ] Returns standardized result format (`success`, `message`, `data`)
- [ ] Includes error handling with try/except
- [ ] Has docstring describing payload and result schemas
- [ ] Uses structured logging (`logger.info`, `logger.error`)
- [ ] Has unit tests in `tests/unit/test_agent_name.py`
- [ ] Has documentation in `docs/agents/agent_name.md`
- [ ] Listed in this AGENTS.md catalog
- [ ] Environment variables documented in `.env.example`
- [ ] Handles missing dependencies gracefully
- [ ] Implements idempotency if applicable
- [ ] Uses timeouts for external API calls

---

## Resources

- **Project Documentation**: [CLAUDE.md](CLAUDE.md)
- **Agent Template**: [docs/agents/agent-template.md](docs/agents/agent-template.md)
- **Architecture Guide**: [ARCHITECTURE_DIAGRAM.txt](ARCHITECTURE_DIAGRAM.txt)
- **Deployment Guide**: [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md)
- **API Reference**: [src/api/main.py](src/api/main.py)
- **Queue System**: [src/core/queue.py](src/core/queue.py)
- **Worker System**: [src/core/worker.py](src/core/worker.py)

---

## Questions?

For questions about agent development:
1. Check existing agent implementations in `src/agents/`
2. Review test patterns in `tests/unit/`
3. Consult [CLAUDE.md](CLAUDE.md) for development workflow
4. Open an issue on GitHub for architectural questions

---

**Last Updated**: 2025-10-01
**WordFlux Version**: 1.0.0
**Agent Count**: 15 (+ expanding)
