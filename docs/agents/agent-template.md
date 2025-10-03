# agent_name

## Purpose

[1-2 paragraphs describing what this agent does and why it exists. Be specific about the business problem it solves or the workflow it enables. Include use cases and when this agent should be invoked.]

Example:
```
The task_starter agent begins work on cards in the Backlog column by moving them to In Progress. It enforces WIP (Work-In-Progress) limits to prevent bottlenecks and ensure focused execution. This agent is typically triggered when a team member is ready to begin work on a new task, either manually via the cockpit UI or automatically via the planner agent.
```

---

## Classification

- **Type**: Core | Workflow Action | Autonomous
- **Category**: Integration | Notification | Data Processing | Workflow Management | Testing
- **State Changes**: [Column1 → Column2] | None | [Describe state change pattern]
- **WIP Aware**: Yes | No - [If yes, explain how it respects WIP limits]

---

## Triggers

List all methods for invoking this agent:

- **Manual CLI**:
  ```bash
  python -m scripts.run_agent --agent agent_name --payload '{...}'
  ```

- **Programmatic API** (Port 8080):
  ```bash
  curl -X POST http://wordflux.3-228-174-188.nip.io:8080/event \
    -H "Content-Type: application/json" \
    -d '{"action":"agent_name","payload":{...}}'
  ```

- **Cockpit UI** (Port 80):
  - [Button name or action location if applicable]
  - [Describe when the action appears]

- **Autopilot**: [Yes/No - If yes, describe when autopilot triggers it]

- **Scheduled**: [If APScheduler or cron triggers, describe schedule]

- **Event-Driven**: [If triggered by external webhooks or events]

---

## Payload Contract

```json
{
  "required_field_1": "string",
  "required_field_2": 123,
  "optional_field": "string (optional)"
}
```

### Field Descriptions

#### Required Fields

- **required_field_1** (`string`):
  - Description: [What this field represents]
  - Constraints: [Length, format, allowed values]
  - Example: `"example_value"`

- **required_field_2** (`number`):
  - Description: [What this field represents]
  - Constraints: [Min, max, units]
  - Example: `123`

#### Optional Fields

- **optional_field** (`string`, default: `"default_value"`):
  - Description: [What this field represents]
  - When to use: [Scenarios where this is needed]
  - Example: `"optional_value"`

### Payload Validation (Optional)

For complex payloads, consider using Pydantic for validation:

```python
from pydantic import BaseModel, Field, validator

class AgentPayload(BaseModel):
    """Pydantic model for agent payload validation."""

    required_field_1: str = Field(..., min_length=1, max_length=100)
    required_field_2: int = Field(..., ge=1, le=1000)
    optional_field: str = Field(default="default_value", max_length=50)

    @validator('required_field_1')
    def validate_field1(cls, v):
        if not v.isalnum():
            raise ValueError('must be alphanumeric')
        return v

# Usage in agent:
def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        validated = AgentPayload(**payload)
        # Access validated fields
        field1 = validated.required_field_1
        # ...
    except ValidationError as e:
        return {"success": False, "error": str(e)}
```

---

## Outputs

```json
{
  "status": "success",
  "message": "Human-readable result description",
  "data": {
    "output_field_1": "value",
    "output_field_2": 123
  }
}
```

### Output Field Descriptions

- **status** (`string`):
  - Values: `"success"` | `"failure"` | `"partial"`
  - Description: Execution outcome

- **message** (`string`):
  - Human-readable description of what happened
  - Examples: "Card moved to In Progress", "WIP limit exceeded, operation rejected"

- **data** (`object`, optional):
  - **output_field_1**: [Description of what this output represents]
  - **output_field_2**: [Description of what this output represents]

---

## Tool / Service Access

List all external services, APIs, or resources this agent interacts with:

### AWS Services

- **S3**:
  - `s3:PutObject` - Upload artifacts
  - `s3:GetObject` - Read artifacts
  - Bucket: `${ARTIFACT_BUCKET}`

- **KMS** (if encryption enabled):
  - `kms:Encrypt`, `kms:Decrypt`, `kms:GenerateDataKey`

### Third-Party APIs

- **Slack**:
  - Webhook POST to `${SLACK_WEBHOOK_URL}`
  - Rate limit: 1 message per second

- **Linear**:
  - GraphQL API at `https://api.linear.app/graphql`
  - Requires: `${LINEAR_API_KEY}`
  - Rate limit: 1000 requests per hour

### Redis Keys Accessed

- `wf:board:col:{column_name}` - Board state (read/write)
- `wf:jobs:pending` - Job queue (read)
- `wf:events:recent` - Event stream (write)
- `wf:agent:autopilot` - Autopilot mode flag (read)

---

## Configuration

### Required Environment Variables

- **`VAR_NAME`** (required):
  - Description: [What this configures]
  - Example: `"value"`
  - Where to obtain: [How to get this value]

### Optional Settings

- **`VAR_NAME`** (default: `"default_value"`):
  - Description: [What this configures]
  - When to change: [Scenarios for customization]

### IAM Permissions (for AWS agents)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject"
      ],
      "Resource": "arn:aws:s3:::${ARTIFACT_BUCKET}/*"
    }
  ]
}
```

---

## Examples

### Example 1: Basic Usage

[Describe a common, simple use case]

**Payload**:
```json
{
  "field": "value"
}
```

**cURL**:
```bash
curl -X POST http://wordflux.3-228-174-188.nip.io:8080/event \
  -H "Content-Type: application/json" \
  -d '{
    "action": "agent_name",
    "payload": {"field": "value"}
  }'
```

**Expected Output**:
```json
{
  "job_id": "abc123",
  "status": "enqueued",
  "duplicate": false
}
```

**Result** (after worker processes):
```json
{
  "status": "success",
  "message": "Operation completed successfully"
}
```

---

### Example 2: Advanced Usage

[Describe a more complex scenario with multiple fields or edge cases]

**Payload**:
```json
{
  "field1": "value1",
  "field2": "value2",
  "options": {
    "flag": true
  }
}
```

**Python**:
```python
import requests

payload = {
    "action": "agent_name",
    "payload": {
        "field1": "value1",
        "field2": "value2",
        "options": {"flag": True}
    }
}

response = requests.post(
    "http://wordflux.3-228-174-188.nip.io:8080/event",
    json=payload
)

print(response.json())
```

---

### Example 3: Cockpit Integration

[If this agent is triggered from cockpit UI, show the user workflow]

**User Workflow**:
1. Navigate to cockpit: http://wordflux.3-228-174-188.nip.io/
2. Select a card in [Column Name]
3. Click [Action Button Name]
4. Card moves to [New Column Name]
5. Real-time SSE update appears in UI

**API Call** (behind the scenes):
```bash
curl -X POST http://wordflux.3-228-174-188.nip.io/agent/act \
  -d '{"card_id":"c-abc123","action":"action_name"}'
```

---

## State Transitions (for Workflow Agents)

[If this agent changes workflow state, document the transition]

### Prerequisites

Before this action can execute:
- [ ] Card must be in [Source Column]
- [ ] [Other requirements, e.g., "Required fields must be populated"]
- [ ] WIP limit must not be exceeded (if applicable)
- [ ] [Other business rules]

### State Changes

**Before**:
- Column: `Backlog`
- Status: `pending`
- Metadata: `{"assigned_to": null}`

**After**:
- Column: `In Progress`
- Status: `in_progress`
- Metadata: `{"assigned_to": "user-123", "started_at": "2025-09-29T20:00:00Z"}`

### Side Effects

- Event emitted: `{"kind":"board_update","card_id":"...","action":"start_work"}`
- Redis pub/sub: `PUBLISH wf:events '{"kind":"card_moved","from":"Backlog","to":"In Progress"}'`
- Slack notification: [If configured]
- Linear issue updated: [If integrated]

### WIP Limit Enforcement

[If this agent respects WIP limits]

**Limit**: Configurable via `WF_WIP_LIMIT` (default: 2)

**Behavior**:
- If `LLEN wf:board:col:In Progress >= WF_WIP_LIMIT`:
  - Action rejected
  - Card remains in source column
  - Event emitted: `{"kind":"wip_limit_exceeded","limit":2,"current":2}`
  - Error returned to user

**Bypass**: Set `bypass_wip=true` in payload (admin only)

---

## Error Handling

### Common Errors

#### 1. Error: "Required field missing"

**Symptom**:
```json
{"status":"failure","error":"Missing required field: card_id"}
```

**Cause**: Payload missing required field

**Solution**:
```bash
# Ensure all required fields are present
curl -X POST ... -d '{"card_id":"c-123","field":"value"}'
```

---

#### 2. Error: "WIP limit exceeded"

**Symptom**:
```json
{"status":"failure","error":"WIP limit of 2 exceeded, cannot move card"}
```

**Cause**: Too many cards in "In Progress" column

**Solution**:
- Complete or move existing cards to unblock
- Or increase WIP limit: `export WF_WIP_LIMIT=3`
- Or bypass (admin): `{"bypass_wip": true}`

---

#### 3. Error: "External API failure"

**Symptom**:
```json
{"status":"failure","error":"Slack webhook returned 500"}
```

**Cause**: Third-party service unavailable

**Solution**:
- Check external service status
- Verify credentials/tokens
- Job will retry up to 3 times automatically
- Check DLQ: `redis-cli LRANGE wf:jobs:dlq 0 10`

---

### Retry Behavior

- **Automatic Retry**: Yes, up to 3 attempts
- **Backoff Strategy**: Exponential (1s, 2s, 4s)
- **Dead Letter Queue**: Failed jobs moved to `wf:jobs:dlq` after max retries

---

## Performance Considerations

- **Execution Time**: Typical duration [e.g., "< 100ms for local operations, 1-2s for API calls"]
- **Rate Limits**: [If interacting with external APIs, note limits]
- **Concurrency**: Can multiple instances run in parallel? [Yes/No, explain constraints]
- **Resource Usage**: [Memory, CPU, network - if notable]

---

## Related Agents

- [link to related agent 1](agent1.md): [Why related - e.g., "Often follows this agent in workflow"]
- [link to related agent 2](agent2.md): [Why related - e.g., "Complementary action"]
- [link to related agent 3](agent3.md): [Why related - e.g., "Alternative approach"]

---

## Testing

### Unit Test Location

`tests/unit/test_agents/test_agent_name.py`

### Example Unit Test

```python
import pytest
from src.agents.agent_name import AgentName

def test_agent_name_success():
    agent = AgentName()
    payload = {"field": "value"}

    result = agent.run(payload)

    assert result["status"] == "success"
    assert "data" in result

def test_agent_name_missing_field():
    agent = AgentName()
    payload = {}  # Missing required field

    result = agent.run(payload)

    assert result["status"] == "failure"
    assert "error" in result
```

### Integration Test

[Describe end-to-end test approach]

```python
def test_agent_name_integration(queue_manager, redis_client):
    # Submit job
    job = Job(agent="agent_name", payload={"field": "value"})
    queue_manager.enqueue(job)

    # Process
    worker = Worker(queue_manager)
    worker.process_one()

    # Verify
    result = redis_client.get(f"job:{job.job_id}:result")
    assert json.loads(result)["status"] == "success"
```

### Manual Testing

```bash
# 1. Start worker in debug mode
export LOG_LEVEL=DEBUG
python -m scripts.run_worker --continuous

# 2. Submit test job (separate terminal)
curl -X POST http://localhost:8080/event \
  -d '{"action":"agent_name","payload":{...}}'

# 3. Watch logs
tail -f /var/log/wordflux-worker.log | grep agent_name

# 4. Check queue
redis-cli LLEN wf:jobs:pending
redis-cli LRANGE wf:jobs:pending 0 10

# 5. Verify result
redis-cli GET job:<job_id>:result
```

---

## Version History

- **v1.0** (2025-09-29): Initial implementation
- [Future versions listed here]

---

## See Also

- [Agent Catalog](README.md) - All agents
- [Agent Development Guide](../guides/agent-development.md) - Creating new agents
- [API Reference](../api/api-service.md) - API endpoints
- [Cockpit User Guide](../guides/cockpit-user-guide.md) - UI usage
- [Troubleshooting Guide](../guides/troubleshooting.md) - Common issues

---

## Complete Implementation Example

Here's a fully working agent implementation following all best practices:

```python
#!/usr/bin/env python3
"""Example Agent - Complete implementation with all patterns."""

import logging
import time
from typing import Dict, Any, Optional
from src.core.base_agent import BaseAgent, Payload, Result

logger = logging.getLogger(__name__)


class ExampleAgent(BaseAgent):
    """
    Example agent demonstrating all patterns:
    - Payload validation
    - Error handling
    - External API calls
    - Metrics tracking
    - Idempotency
    - Graceful degradation
    """

    def __init__(self):
        super().__init__("example_agent")
        # Load config from environment
        self.api_key = os.getenv("EXAMPLE_API_KEY")
        self.timeout = int(os.getenv("EXAMPLE_TIMEOUT", "10"))

    def run(self, payload: Payload) -> Result:
        """
        Execute agent logic with comprehensive error handling.

        Expected payload:
        {
            "card": {...},           # Required: Card object
            "action": "process",     # Required: Action to perform
            "idempotency_key": "..."  # Optional: For deduplication
        }
        """
        start_time = time.time()

        try:
            # 1. Extract and validate inputs
            card = payload.get("card")
            action = payload.get("action")
            idempotency_key = payload.get("idempotency_key")

            if not card:
                raise ValueError("card is required")
            if not action:
                raise ValueError("action is required")

            # 2. Check idempotency
            if idempotency_key:
                cached = self._check_cache(idempotency_key)
                if cached:
                    logger.info(f"Returning cached result for {idempotency_key}")
                    return cached

            # 3. Perform main logic
            result = self._process_card(card, action)

            # 4. Track metrics
            duration_ms = (time.time() - start_time) * 1000

            response = {
                "success": True,
                "message": f"Successfully processed card {card.get('id')}",
                "data": result,
                "metrics": {
                    "duration_ms": duration_ms,
                    "records_processed": 1
                }
            }

            # 5. Cache result if idempotency key provided
            if idempotency_key:
                self._cache_result(idempotency_key, response)

            return response

        except ValueError as e:
            # Validation errors (user error)
            logger.warning(f"Validation error: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": "Invalid input provided"
            }

        except ConnectionError as e:
            # Transient errors (retry recommended)
            logger.error(f"Connection error: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": "External service unavailable",
                "retry": True
            }

        except Exception as e:
            # Unexpected errors
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                f"Agent failed after {duration_ms}ms: {e}",
                exc_info=True,
                extra={
                    "agent": self.name,
                    "card_id": payload.get("card", {}).get("id"),
                    "action": payload.get("action")
                }
            )
            return {
                "success": False,
                "error": str(e),
                "message": "Agent execution failed"
            }

    def _process_card(self, card: Dict[str, Any], action: str) -> Dict[str, Any]:
        """
        Core processing logic.

        Args:
            card: Card object
            action: Action to perform

        Returns:
            Processing result
        """
        card_id = card.get("id", "unknown")

        # Check if external API available
        if self.api_key:
            return self._process_with_api(card, action)
        else:
            # Graceful degradation
            logger.warning("API key not configured, using fallback mode")
            return self._process_locally(card, action)

    def _process_with_api(self, card: Dict[str, Any], action: str) -> Dict[str, Any]:
        """Process using external API."""
        import requests

        try:
            response = requests.post(
                "https://api.example.com/process",
                json={"card": card, "action": action},
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()

        except requests.Timeout:
            raise ConnectionError("API request timed out")
        except requests.RequestException as e:
            raise ConnectionError(f"API request failed: {e}")

    def _process_locally(self, card: Dict[str, Any], action: str) -> Dict[str, Any]:
        """Fallback processing without external API."""
        return {
            "processed": True,
            "method": "local",
            "card_id": card.get("id"),
            "action": action
        }

    def _check_cache(self, key: str) -> Optional[Dict[str, Any]]:
        """Check if result already cached."""
        try:
            import redis
            r = redis.from_url(os.getenv("REDIS_URL"))
            cached = r.get(f"idempotency:{key}")
            if cached:
                return json.loads(cached)
        except Exception as e:
            logger.warning(f"Cache check failed: {e}")
        return None

    def _cache_result(self, key: str, result: Dict[str, Any]) -> None:
        """Cache result for idempotency."""
        try:
            import redis
            import json
            r = redis.from_url(os.getenv("REDIS_URL"))
            # Cache for 1 hour
            r.setex(f"idempotency:{key}", 3600, json.dumps(result))
        except Exception as e:
            logger.warning(f"Cache write failed: {e}")


def build_agent() -> ExampleAgent:
    """Factory function to create agent instance."""
    return ExampleAgent()


__all__ = ["ExampleAgent", "build_agent"]
```

### Corresponding Test File

```python
import pytest
from unittest.mock import Mock, patch, MagicMock
from src.agents.example_agent import ExampleAgent


@pytest.fixture
def agent():
    """Create agent instance for testing."""
    return ExampleAgent()


@pytest.fixture
def sample_card():
    """Sample card data."""
    return {
        "id": "c-test123",
        "title": "Test Card",
        "column": "In Progress"
    }


def test_successful_processing(agent, sample_card):
    """Test successful card processing."""
    payload = {
        "card": sample_card,
        "action": "process"
    }

    result = agent.run(payload)

    assert result["success"] is True
    assert "data" in result
    assert "metrics" in result
    assert result["metrics"]["duration_ms"] > 0


def test_missing_required_field(agent):
    """Test validation of missing required field."""
    payload = {"action": "process"}  # Missing card

    result = agent.run(payload)

    assert result["success"] is False
    assert "card is required" in result["error"]


def test_idempotency(agent, sample_card):
    """Test idempotent execution."""
    payload = {
        "card": sample_card,
        "action": "process",
        "idempotency_key": "test-key-123"
    }

    with patch.object(agent, '_check_cache', return_value=None):
        with patch.object(agent, '_cache_result') as mock_cache:
            result1 = agent.run(payload)
            assert result1["success"] is True
            mock_cache.assert_called_once()


@patch('requests.post')
def test_api_call_success(mock_post, agent, sample_card):
    """Test successful external API call."""
    mock_response = Mock()
    mock_response.json.return_value = {"processed": True}
    mock_response.raise_for_status = Mock()
    mock_post.return_value = mock_response

    agent.api_key = "test-key"

    payload = {"card": sample_card, "action": "process"}
    result = agent.run(payload)

    assert result["success"] is True
    mock_post.assert_called_once()


@patch('requests.post')
def test_api_call_timeout(mock_post, agent, sample_card):
    """Test API timeout handling."""
    import requests
    mock_post.side_effect = requests.Timeout("Connection timeout")

    agent.api_key = "test-key"

    payload = {"card": sample_card, "action": "process"}
    result = agent.run(payload)

    assert result["success"] is False
    assert "retry" in result
    assert result["retry"] is True


def test_graceful_degradation(agent, sample_card):
    """Test fallback when API not configured."""
    agent.api_key = None  # No API key

    payload = {"card": sample_card, "action": "process"}
    result = agent.run(payload)

    assert result["success"] is True
    assert result["data"]["method"] == "local"
```

---

**Maintained By**: WordFlux Team
**Last Updated**: 2025-10-01
**Questions?**: [Open an issue](../../issues) or see [troubleshooting](../guides/troubleshooting.md)