# WordFlux Agent Catalog

## Overview

WordFlux agents are specialized processors that execute specific tasks in the workflow system. Each agent implements the `BaseAgent` interface (`src/core/base_agent.py`) and is registered in the agent registry (`src/agents/__init__.py`).

**Total Agents**: 14
**Registration**: All agents support multiple aliases for API compatibility
**Execution**: Workers poll Redis queue and route jobs to appropriate agents

---

## Agent Categories

### Core Agents (5)

Foundation agents for system integration and testing.

| Agent | Purpose | Documentation | Status |
|-------|---------|---------------|--------|
| **echo** | Test agent for development and queue verification | [echo.md](echo.md) | ⏳ Pending |
| **slack_notifier** | Send Slack notifications with webhook integration | [slack_notifier.md](slack_notifier.md) | ⏳ Pending |
| **stripe_disputes** | Process Stripe dispute CSV exports | [stripe.export_disputes.md](stripe.export_disputes.md) | ✅ Documented |
| **linear_connector** | Update Linear issues with workflow synchronization | [linear_connector.md](linear_connector.md) | ⏳ Pending |
| **playbook_runner** | Execute multi-step YAML workflows | [playbook_runner.md](playbook_runner.md) | ⏳ Pending |

---

### Workflow Action Agents (8)

Agents that execute actions on workflow cards, often triggered from the Cockpit UI.

| Agent | Action | State Transition | WIP Aware | Documentation | Status |
|-------|--------|------------------|-----------|---------------|--------|
| **task_starter** | Start work | Backlog → In Progress | Yes | [task_starter.md](task_starter.md) | ⏳ Pending |
| **review_requester** | Request review | In Progress → Waiting Approval | No | [review_requester.md](review_requester.md) | ⏳ Pending |
| **content_approver** | Approve content | Waiting Approval → Scheduled | No | [content_approver.md](content_approver.md) | ⏳ Pending |
| **content_publisher** | Publish now | Scheduled → Published | No | [content_publisher.md](content_publisher.md) | ⏳ Pending |
| **metrics_reporter** | Report KPIs | Published (no change) | No | [metrics_reporter.md](metrics_reporter.md) | ⏳ Pending |
| **change_requester** | Request changes | Waiting Approval → In Progress | No | [change_requester.md](change_requester.md) | ⏳ Pending |
| **scheduler** | Reschedule | Scheduled (update time) | No | [scheduler.md](scheduler.md) | ⏳ Pending |
| **task_pauser** | Pause/resume | Any (add metadata) | No | [task_pauser.md](task_pauser.md) | ⏳ Pending |

---

### Autonomous Agents (1)

Agents that run on schedules or autonomously without direct user trigger.

| Agent | Purpose | Scheduling | Documentation | Status |
|-------|---------|------------|---------------|--------|
| **planner_agent** | Create cards from templates and progress workflows | APScheduler (every hour) | [planner_agent.md](planner_agent.md) | ⏳ Pending |

---

## Agent Registry

All agents are registered in `src/agents/__init__.py` with the following pattern:

```python
from src.agents.agent_name import build_agent as build_agent_name
register_agent("agent_name", build_agent_name)
register_agent("alias", build_agent_name)  # Optional aliases
```

### Registered Aliases

Some agents have multiple aliases for API compatibility:

- `stripe_disputes` / `stripe.export_disputes`
- `slack_notifier` / `slack.notify`
- `linear_connector` / `linear.update`
- `playbook_runner` / `playbook`
- `planner_agent` / `planner`

---

## Quick Reference

### By Type

**Core Agents**: Development, testing, external integrations
**Workflow Action Agents**: Cockpit UI actions, state transitions
**Autonomous Agents**: Scheduled execution, no direct user trigger

### By State Changes

**Cards Move Between Columns**:
- `task_starter`: Backlog → In Progress
- `review_requester`: In Progress → Waiting Approval
- `content_approver`: Waiting Approval → Scheduled
- `content_publisher`: Scheduled → Published
- `change_requester`: Waiting Approval → In Progress

**No Column Change**:
- `metrics_reporter`: Remains in Published
- `scheduler`: Remains in Scheduled (updates time)
- `task_pauser`: Remains in current column (adds metadata)

### By WIP Awareness

Agents that respect WIP limits:
- `task_starter` - Cannot move card if "In Progress" column at limit (default: 2)
- `planner_agent` - Respects WIP when auto-progressing cards

---

## Agent Implementation Pattern

All agents follow this structure:

```python
from src.core.base_agent import BaseAgent
from typing import Dict, Any

class MyAgent(BaseAgent):
    def __init__(self):
        super().__init__("my_agent")

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute agent logic.

        Args:
            payload: Input data for agent execution

        Returns:
            Result dictionary with status and output
        """
        # Agent logic here
        return {
            "status": "success",
            "message": "Agent executed successfully",
            "data": {...}
        }

def build_agent():
    return MyAgent()
```

---

## Triggering Agents

### Via API (Port 8080)

```bash
curl -X POST http://wordflux.3-228-174-188.nip.io:8080/event \
  -H "Content-Type: application/json" \
  -d '{
    "action": "agent_name",
    "payload": {...},
    "idempotency_key": "optional-unique-key"
  }'
```

### Via Cockpit UI (Port 80)

```bash
# Execute action on a card
curl -X POST http://wordflux.3-228-174-188.nip.io/agent/act \
  -H "Content-Type: application/json" \
  -d '{
    "card_id": "c-abc123",
    "action": "start_work"
  }'
```

### Via CLI (Local Testing)

```bash
# Run agent locally
python -m scripts.run_agent --agent echo --message "Test"

# With JSON payload
python -m scripts.run_agent --agent slack_notifier \
  --payload '{"message":"Test notification","channel":"#general"}'
```

---

## Creating New Agents

See the comprehensive guide: [Agent Development Guide](../guides/agent-development.md)

### Quick Steps

1. **Create Agent File**: `src/agents/my_agent.py`
2. **Implement BaseAgent**: Inherit and implement `run(payload)` method
3. **Register Agent**: Add to `src/agents/__init__.py`
4. **Write Tests**: Create `tests/unit/test_agents/test_my_agent.py`
5. **Document Agent**: Use [agent-template.md](agent-template.md) as guide

---

## Agent Documentation Template

All agent documentation follows a standard structure defined in [agent-template.md](agent-template.md).

### Required Sections

- **Purpose**: What the agent does and why
- **Classification**: Type, category, state changes
- **Triggers**: How the agent is invoked
- **Payload Contract**: Input schema with field descriptions
- **Outputs**: Return value schema
- **Tool/Service Access**: External dependencies
- **Configuration**: Environment variables
- **Examples**: Concrete usage examples (curl, Python)
- **State Transitions**: For workflow agents
- **Error Handling**: Common errors and solutions

---

## Agent Responsibilities

### Core Agents

**Purpose**: External system integration and development utilities

**Examples**:
- `echo`: Verify queue connectivity
- `slack_notifier`: Send notifications to Slack
- `stripe_disputes`: Process financial data exports
- `linear_connector`: Sync workflow state to issue tracker
- `playbook_runner`: Orchestrate multi-step workflows

### Workflow Action Agents

**Purpose**: Execute cockpit UI actions with state management

**Examples**:
- `task_starter`: Begin work on backlog items
- `review_requester`: Submit work for approval
- `content_approver`: Approve content for publication
- `content_publisher`: Publish approved content
- `metrics_reporter`: Generate KPI reports

**Common Patterns**:
- Read card from Redis board state
- Validate prerequisites (WIP limits, required fields)
- Execute business logic
- Update card metadata
- Move card to new column (if applicable)
- Emit event via Redis pub/sub
- Return success/failure status

### Autonomous Agents

**Purpose**: Background automation without direct user trigger

**Examples**:
- `planner_agent`: Creates cards from templates, progresses stalled workflows

**Characteristics**:
- Scheduled execution (APScheduler, cron, etc.)
- No direct user trigger
- Operates on system state (board, queue, external APIs)
- Respects WIP limits and business rules
- Emits events for observability

---

## Testing Agents

### Unit Tests

Location: `tests/unit/test_agents/`

```python
import pytest
from src.agents.my_agent import MyAgent

def test_my_agent_success():
    agent = MyAgent()
    result = agent.run({"field": "value"})

    assert result["status"] == "success"
    assert "data" in result
```

### Integration Tests

Location: `tests/integration/`

```python
def test_agent_end_to_end(queue_manager, redis_client):
    # Submit job
    job = Job(agent="my_agent", payload={...})
    queue_manager.enqueue(job)

    # Execute worker
    worker = Worker(queue_manager)
    worker.process_one()

    # Verify result
    assert redis_client.get(f"job:{job.job_id}:status") == "completed"
```

### Manual Testing

```bash
# Run agent locally with debug output
export LOG_LEVEL=DEBUG
python -m scripts.run_agent --agent my_agent --payload '{...}'

# Watch logs
tail -f /var/log/wordflux-worker.log

# Monitor queue
redis-cli LLEN wf:jobs:pending
redis-cli LRANGE wf:jobs:pending 0 10
```

---

## Troubleshooting

### Agent Not Found

**Symptom**: `AgentNotFoundError: Agent 'my_agent' not registered`

**Solution**:
1. Verify agent imported in `src/agents/__init__.py`
2. Check `register_agent("my_agent", build_agent)` called
3. Restart worker service: `sudo systemctl restart wordflux-worker`

### Agent Failing

**Symptom**: Jobs stuck in queue or moved to DLQ

**Diagnosis**:
```bash
# Check worker logs
sudo journalctl -u wordflux-worker -n 50

# Check dead letter queue
redis-cli LRANGE wf:jobs:dlq 0 10

# Query ledger for job history
python scripts/query_ledger.py --job-id <job_id>
```

**Common Causes**:
- Missing environment variables (check `wordflux.env`)
- Redis connection issues
- External API failures (Slack, Linear, etc.)
- Invalid payload schema

### Agent Performance

**Symptom**: Slow agent execution, queue backing up

**Diagnosis**:
```bash
# Check queue depth
redis-cli LLEN wf:jobs:pending

# Monitor agent execution time
grep "Agent.*completed in" /var/log/wordflux-worker.log | tail -20

# Check metrics
curl http://localhost:8080/metrics | grep agent_execution_duration
```

**Optimization**:
- Add caching for external API calls
- Batch operations where possible
- Add concurrency (multiple workers)
- Profile slow operations

---

## Related Documentation

- [Agent Development Guide](../guides/agent-development.md) - Step-by-step agent creation
- [Agent Template](agent-template.md) - Documentation template
- [API Reference](../api/api-service.md) - API endpoints for job submission
- [Worker Architecture](../../src/core/worker.py) - Worker processing logic
- [Queue Documentation](../queue.md) - Queue architecture
- [Testing Guide](../../tests/README.md) - Testing patterns

---

## Agent Statistics

| Metric | Value |
|--------|-------|
| Total Agents | 14 |
| Core Agents | 5 |
| Workflow Action Agents | 8 |
| Autonomous Agents | 1 |
| Documented Agents | 1 (7%) |
| Pending Documentation | 13 (93%) |
| Registered Aliases | 10 |
| Lines of Agent Code | ~3,500 |

---

## Roadmap

### Short Term
- [ ] Document all 13 pending agents
- [ ] Create agent template with examples
- [ ] Add agent testing guide
- [ ] Implement agent metrics collection

### Medium Term
- [ ] Agent configuration via YAML
- [ ] Agent discovery UI in cockpit
- [ ] Agent execution history visualization
- [ ] Performance profiling per agent

### Long Term
- [ ] Dynamic agent loading (plugins)
- [ ] Agent composition (chaining)
- [ ] Agent versioning and rollback
- [ ] Agent marketplace/registry

---

**Last Updated**: 2025-09-29
**Maintained By**: WordFlux Team
**Questions?**: See [docs/guides/troubleshooting.md](../guides/troubleshooting.md)