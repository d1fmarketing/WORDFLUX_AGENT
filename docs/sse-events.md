# SSE Event System

## Overview

WordFlux Cockpit uses a unified Server-Sent Events (SSE) system for real-time updates across the API, Worker, and Cockpit services. All events are:
- **Type-safe** using Python dataclasses
- **Centrally emitted** via Redis pub/sub
- **Historically tracked** in a rolling 200-event list
- **Streamed** to clients via `/events/stream` endpoint

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Worker    │     │  Chat API   │     │   Cockpit   │
│   Service   │     │   Service   │     │   Service   │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                    │
       │   emit_job_started()                  │
       │   emit_job_succeeded()                │
       │   emit_job_failed()                   │
       │                   │   emit_chat_message()
       │                   │                    │
       │                                        │   emit_board_update()
       │                                        │   emit_wip_limit_exceeded()
       │                                        │
       └───────────────────┴────────────────────┘
                           │
                    ┌──────▼──────┐
                    │ SSEEmitter  │
                    │  (Redis)    │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │                         │
       ┌──────▼──────┐         ┌────────▼────────┐
       │  Pub/Sub    │         │ Rolling History │
       │  Channel    │         │   (200 events)  │
       │ wf:events   │         │ wf:events:recent│
       └──────┬──────┘         └─────────────────┘
              │
       ┌──────▼──────┐
       │ SSE Stream  │
       │ /events/st… │
       └─────────────┘
              │
       ┌──────▼──────┐
       │   Clients   │
       └─────────────┘
```

## Event Types

### 1. ChatMessageEvent

**Purpose**: Real-time chat messages from user or assistant

**Fields**:
- `kind`: `"chat_message"` (automatic)
- `role`: `"user"` | `"assistant"`
- `text`: Message content
- `session_id`: Chat session identifier (optional)
- `ts`: Unix timestamp in milliseconds (automatic)

**Example**:
```python
from src.core.events import emit_chat_message

# Emit user message
emit_chat_message(role="user", text="Hello", session_id="sess-123")

# Emit assistant response
emit_chat_message(role="assistant", text="Hi there!", session_id="sess-123")
```

**SSE Output**:
```
data: {"kind":"chat_message","role":"user","text":"Hello","session_id":"sess-123","ts":1704067200000}
```

### 2. JobStartedEvent

**Purpose**: Job execution started notification

**Fields**:
- `kind`: `"job_started"` (automatic)
- `job_id`: Unique job identifier
- `action`: Agent name executing the job
- `card_id`: Associated card ID (optional, extracted from payload)
- `ts`: Unix timestamp in milliseconds (automatic)

**Example**:
```python
from src.core.events import emit_job_started

# Worker service emits when job starts
card_id = extract_card_id(job)  # Extracts from job.payload["card_id"]
emit_job_started(job.job_id, job.agent, card_id)
```

**SSE Output**:
```
data: {"kind":"job_started","job_id":"job-abc123","action":"slack_notifier","card_id":"c-xyz","ts":1704067200000}
```

### 3. JobSucceededEvent

**Purpose**: Job execution completed successfully

**Fields**:
- `kind`: `"job_succeeded"` (automatic)
- `job_id`: Unique job identifier
- `action`: Agent name that executed
- `card_id`: Associated card ID (optional)
- `data`: Result data from job execution (optional)
- `ts`: Unix timestamp in milliseconds (automatic)

**Example**:
```python
from src.core.events import emit_job_succeeded

# Worker service emits on success
emit_job_succeeded(
    job.job_id,
    job.agent,
    card_id,
    data={"result": agent_result}
)
```

**SSE Output**:
```
data: {"kind":"job_succeeded","job_id":"job-abc123","action":"slack_notifier","card_id":"c-xyz","data":{"result":"ok"},"ts":1704067200000}
```

### 4. JobFailedEvent

**Purpose**: Job execution failed notification

**Fields**:
- `kind`: `"job_failed"` (automatic)
- `job_id`: Unique job identifier
- `action`: Agent name that failed
- `error`: Error message describing the failure
- `card_id`: Associated card ID (optional)
- `ts`: Unix timestamp in milliseconds (automatic)

**Example**:
```python
from src.core.events import emit_job_failed

# Worker service emits on failure
emit_job_failed(
    job.job_id,
    job.agent,
    str(exception),
    card_id
)
```

**SSE Output**:
```
data: {"kind":"job_failed","job_id":"job-abc123","action":"slack_notifier","error":"Connection timeout","card_id":"c-xyz","ts":1704067200000}
```

### 5. BoardUpdateEvent

**Purpose**: Board state changed (card moved/created/updated)

**Fields**:
- `kind`: `"board_update"` (automatic)
- `cards`: List of affected cards with their current state
- `ts`: Unix timestamp in milliseconds (automatic)

**Example**:
```python
from src.core.events import emit_board_update

# Cockpit service emits when card is pushed to column
emit_board_update(cards=[{
    "id": "c-123",
    "title": "New Feature",
    "list": "in_progress",
    "assignee": "dev-team"
}])
```

**SSE Output**:
```
data: {"kind":"board_update","cards":[{"id":"c-123","title":"New Feature","list":"in_progress"}],"ts":1704067200000}
```

### 6. WIPLimitExceededEvent

**Purpose**: Work-in-progress limit reached, card cannot be added

**Fields**:
- `kind`: `"wip_limit_exceeded"` (automatic)
- `column`: Column name where limit was exceeded
- `card_id`: ID of card that couldn't be added
- `card_title`: Title of card that couldn't be added
- `current_count`: Current number of cards in column
- `limit`: Maximum allowed cards in column
- `ts`: Unix timestamp in milliseconds (automatic)

**Example**:
```python
from src.core.events import emit_wip_limit_exceeded

# Cockpit service emits when WIP limit blocks card addition
emit_wip_limit_exceeded(
    column="in_progress",
    card_id="c-456",
    card_title="Blocked Card",
    current_count=2,
    limit=2
)
```

**SSE Output**:
```
data: {"kind":"wip_limit_exceeded","column":"in_progress","card_id":"c-456","card_title":"Blocked Card","current_count":2,"limit":2,"ts":1704067200000}
```

## Custom Events

For events that don't have a dedicated dataclass (e.g., `job_queued`, `agent_mode`), use the `emit_raw()` method:

```python
from src.core.events import get_default_emitter

emitter = get_default_emitter()
emitter.emit_raw("custom_event", {
    "foo": "bar",
    "count": 42
})
```

**Backward Compatibility**: The old `emit_event()` function in cockpit service now wraps `emit_raw()` internally.

## SSE Stream Format

### Standard Event Format

```
event: message
data: {"kind":"chat_message","role":"user","text":"Hello","ts":1704067200000}

```

### Heartbeat Format

Every 15 seconds (when no events are sent):
```
: hb

```

### Connection Established

On initial connection:
```
: connected

```

## Client-Side Integration

### JavaScript EventSource Example

```javascript
class SSEManager {
    constructor(url) {
        this.url = url;
        this.eventSource = null;
        this.handlers = {};
    }

    connect() {
        this.eventSource = new EventSource(this.url);

        this.eventSource.onmessage = (e) => {
            const event = JSON.parse(e.data);
            const handler = this.handlers[event.kind];
            if (handler) handler(event);
        };

        this.eventSource.onerror = (e) => {
            console.error('SSE connection error', e);
            setTimeout(() => this.connect(), 5000); // Reconnect
        };
    }

    on(kind, handler) {
        this.handlers[kind] = handler;
    }
}

// Usage
const sse = new SSEManager('/events/stream');

sse.on('chat_message', (event) => {
    console.log(`${event.role}: ${event.text}`);
});

sse.on('job_started', (event) => {
    console.log(`Job ${event.job_id} started: ${event.action}`);
});

sse.on('board_update', (event) => {
    console.log(`Board updated: ${event.cards.length} cards`);
});

sse.connect();
```

## Server-Side Implementation Details

### SSEEmitter Class

**Location**: `src/core/events.py`

**Responsibilities**:
- Lazy Redis connection initialization
- Event serialization to JSON
- Publishing to Redis pub/sub channel (`wf:events`)
- Maintaining rolling history list (`wf:events:recent`, 200 items)
- Graceful degradation when Redis unavailable

**Configuration** (via environment variables):
- `REDIS_URL`: Redis connection string (default: `redis://localhost:6379/0`)
- `WF_EVENTS_CHANNEL`: Pub/sub channel name (default: `wf:events`)
- `WF_EVENTS_LIST`: History list key (default: `wf:events:recent`)
- `WF_RECENT_EVENTS_KEEP`: History size (default: `200`)

### Event History

Recent events are stored in a Redis list for client bootstrapping:
```bash
# Fetch last 50 events
redis-cli lrange wf:events:recent 0 49
```

**Cockpit Endpoint**:
```bash
curl http://localhost:8081/events/recent
```

Returns:
```json
[
  {"kind":"chat_message","role":"user","text":"Hello","ts":1704067200000},
  {"kind":"job_started","job_id":"job-123","action":"echo","ts":1704067201000}
]
```

## Migration Guide

### From Old emit_event() to New System

**Before** (Worker Service):
```python
emit_event("job_started", job, status="running")
```

**After**:
```python
from src.core.events import emit_job_started

card_id = extract_card_id(job)
emit_job_started(job.job_id, job.agent, card_id)
```

**Before** (Chat API):
```python
emit_sse_event("chat_message", {
    "session_id": session_id,
    "role": "assistant",
    "message": text
})
```

**After**:
```python
from src.core.events import emit_chat_message

emit_chat_message(role="assistant", text=text, session_id=session_id)
```

**Before** (Cockpit Service):
```python
emit_event("board_update", {"column": column, "card": card})
```

**After**:
```python
from src.core.events import emit_board_update

emit_board_update(cards=[card])
```

## Testing

### Unit Tests

**Location**: `tests/unit/test_events.py`

**Coverage**: 97% of `src/core/events.py`

**Run tests**:
```bash
.venv/bin/pytest tests/unit/test_events.py -v
```

### Testing Event Emission

```python
from src.core.events import SSEEmitter, set_default_emitter
from unittest.mock import MagicMock

def test_my_feature():
    # Create mock Redis client
    mock_redis = MagicMock()
    mock_redis.pipeline.return_value = mock_redis
    mock_redis.execute.return_value = [1, 1, True]

    # Inject mock emitter
    emitter = SSEEmitter()
    emitter._redis_client = mock_redis
    set_default_emitter(emitter)

    # Test code that emits events
    emit_job_started("job-1", "agent-1", "card-1")

    # Verify Redis calls
    assert mock_redis.publish.called

    # Cleanup
    set_default_emitter(None)
```

## Performance Considerations

### Event Emission Overhead

Each `emit()` call performs a Redis pipeline with 3 operations:
1. `PUBLISH wf:events <json>`
2. `LPUSH wf:events:recent <json>`
3. `LTRIM wf:events:recent 0 199`

**Latency**: ~1-2ms per event (local Redis)

### Redis Unavailability

When Redis is unavailable:
- Events are **silently dropped** (logged as warning)
- Functions return `False` instead of `True`
- **No exceptions raised** (graceful degradation)
- Services continue operating normally

### Event Frequency

Current system handles:
- ~100 events/second without issues
- 200-event history = ~2 seconds at peak load
- Increase `WF_RECENT_EVENTS_KEEP` if needed

## Troubleshooting

### Events Not Appearing in Stream

1. **Check Redis connection**:
   ```bash
   redis-cli ping  # Should return PONG
   ```

2. **Verify channel subscription**:
   ```bash
   redis-cli subscribe wf:events
   ```

3. **Check recent events list**:
   ```bash
   redis-cli lrange wf:events:recent 0 10
   ```

4. **Enable debug logging**:
   ```python
   import logging
   logging.getLogger('src.core.events').setLevel(logging.DEBUG)
   ```

### Client Not Receiving Events

1. **Verify SSE endpoint is accessible**:
   ```bash
   curl -N http://localhost:8081/events/stream
   ```
   Should show: `: connected`

2. **Check heartbeat (15s interval)**:
   Wait 15 seconds, should see: `: hb`

3. **Nginx buffering** (if using reverse proxy):
   Ensure `proxy_buffering off;` for SSE endpoints

### Type Errors

If you see dataclass field ordering errors:
- Ensure all required fields come **before** optional fields
- BaseEvent is intentionally NOT a dataclass (avoids inheritance issues)

## Future Enhancements

Potential improvements (not yet implemented):

1. **Event Batching**: Batch multiple events in single Redis pipeline for high-throughput scenarios
2. **Event Persistence**: Optional PostgreSQL storage for long-term event history
3. **Event Filtering**: Client-side filtering by event kind or session_id
4. **Event Replay**: Replay events from a specific timestamp
5. **WebSocket Support**: Alternative to SSE for bidirectional communication
6. **Event Compression**: Gzip compression for large payloads

## References

- **SSE Specification**: [HTML Living Standard](https://html.spec.whatwg.org/multipage/server-sent-events.html)
- **Redis Pub/Sub**: [Redis Documentation](https://redis.io/docs/manual/pubsub/)
- **Python Dataclasses**: [PEP 557](https://peps.python.org/pep-0557/)

## Support

For questions or issues:
1. Check server logs: `sudo journalctl -u wordflux-cockpit -f`
2. Review Redis logs: `sudo journalctl -u redis -f`
3. Enable debug logging in `src/core/events.py`
4. Verify event emission with `emit()` return value
