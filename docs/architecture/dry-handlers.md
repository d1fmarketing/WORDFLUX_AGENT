# Dry Handler Pattern

## Overview

The **Dry Handler Pattern** is a core architectural principle in WordFlux that ensures all tool handlers remain stateless and side-effect-free. Handlers **never** directly manipulate the board state, call external APIs, or touch the DOM. Instead, they follow a strict **Job → Queue → SSE** pattern.

## Principles

### 1. Handlers Are Dry (Side-Effect-Free)

Tool handlers in the chat API:
- ✅ Create Job objects with structured payloads
- ✅ Publish jobs to Redis queue (`wf:jobs`)
- ✅ Emit SSE events via Redis pub/sub (`wf:events`)
- ❌ **NEVER** call board HTTP APIs (`/board/card`, `/board/move`)
- ❌ **NEVER** manipulate Redis lists directly (LPUSH to card queues)
- ❌ **NEVER** make outbound HTTP requests
- ❌ **NEVER** mutate DOM or browser state

### 2. Agents Execute (Side-Effects in Workers)

Agents (like `board_operator`) handle actual execution:
- ✅ Consume jobs from Redis queue
- ✅ Use cockpit helpers (`push_to`, `find_and_remove`, `emit_event`)
- ✅ Manipulate board state via Redis primitives
- ✅ Emit SSE events on completion
- ❌ **NEVER** call external HTTP APIs for board operations

### 3. SSE for UI Updates (Not HTTP Polling)

UI updates flow through SSE:
- ✅ Redis pub/sub (`wf:events` channel)
- ✅ Clients subscribe via `/events/stream`
- ✅ Real-time board updates without polling
- ❌ **NEVER** poll `/board/state` for changes

## Architecture Flow

```
┌─────────────────────────────────────────────────────────────┐
│                        DRY HANDLER                          │
│                                                             │
│  User Message → LLM → Tool Call → Validation               │
│                                       ↓                     │
│                          Job(agent="board_operator",        │
│                              action="move_card",            │
│                              payload={...})                 │
│                                       ↓                     │
│                          queue.publish(job)  ←─── REDIS    │
│                                       ↓          wf:jobs    │
│                          emit_sse_event()  ←─── REDIS      │
│                                                  wf:events  │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                      WORKER/AGENT                           │
│                                                             │
│  Worker → queue.consume() → board_operator.run(payload)    │
│                                       ↓                     │
│                          push_to(column, card)             │
│                          find_and_remove(card_ref)         │
│                          emit_event("board_update")        │
│                                       ↓                     │
│                          Redis LPUSH/LREM/LRANGE           │
│                                       ↓                     │
│                          Redis PUBLISH wf:events           │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                      FRONTEND (SSE)                         │
│                                                             │
│  EventSource("/events/stream") → Redis SUBSCRIBE           │
│                                       ↓                     │
│                          board_update event                │
│                                       ↓                     │
│                          React setState (re-render)        │
└─────────────────────────────────────────────────────────────┘
```

## Code Examples

### ✅ GOOD: Dry Handler Pattern

**File: `src/api/chat.py` - execute_tool_call()**

```python
def execute_tool_call(tool_call: Dict[str, Any], session_id: str) -> Optional[str]:
    """Execute tool call by queueing job (DRY HANDLER)."""
    func_name = tool_call["name"]
    func_args = tool_call["input"]

    # Import queue dependencies
    from src.core.queue import load_default_queue
    from src.core.job import Job

    queue = load_default_queue()

    if func_name == "move_card":
        # ✅ Create job object (no side effects)
        job = Job(
            agent="board_operator",
            payload={
                "action": "move_card",
                "card_ref": func_args.get("card_ref", ""),
                "to_column": func_args.get("to", ""),
                "from_chat": True,
                "session_id": session_id
            },
            job_id=f"chat-{uuid.uuid4().hex[:8]}"
        )

        # ✅ Queue for async execution
        queue.publish(job)
        job_id = job.job_id

        # ✅ Emit SSE event (notification only, no state change)
        emit_sse_event("job_queued", {
            "job_id": job_id,
            "agent": "board_operator",
            "from_chat": True,
            "session_id": session_id
        })

        return job_id
```

**Why this works:**
- Handler is **stateless** (no side effects)
- Job is **serializable** (can be retried, logged, monitored)
- Execution is **asynchronous** (doesn't block chat response)
- Updates via **SSE** (real-time, no polling)

### ❌ BAD: Direct Board Mutation

```python
# ❌ ANTI-PATTERN - DO NOT DO THIS
def execute_tool_call_wrong(tool_call: Dict[str, Any]) -> Optional[str]:
    """WRONG: Direct HTTP call to board API."""
    func_name = tool_call["name"]
    func_args = tool_call["input"]

    if func_name == "move_card":
        # ❌ BAD: Direct HTTP POST to board
        response = requests.post(
            "http://localhost:8081/board/move",
            json={
                "card_id": func_args["card_ref"],
                "to": func_args["to"]
            }
        )

        # ❌ BAD: Synchronous, blocking, no queue, no retry
        return response.json().get("card_id")
```

**Why this is wrong:**
- **Synchronous** - blocks chat response waiting for board operation
- **No queue** - can't retry on failure, no visibility
- **HTTP overhead** - unnecessary network roundtrip
- **Tight coupling** - chat depends on board HTTP API availability
- **No SSE** - frontend doesn't get notified of change

### ❌ BAD: Direct Redis Mutation

```python
# ❌ ANTI-PATTERN - DO NOT DO THIS
def execute_tool_call_wrong_redis(tool_call: Dict[str, Any]) -> Optional[str]:
    """WRONG: Direct Redis manipulation in handler."""
    func_name = tool_call["name"]
    func_args = tool_call["input"]

    if func_name == "create_card":
        # ❌ BAD: Direct Redis LPUSH in handler
        import redis
        r = redis.from_url(os.getenv("REDIS_URL"))

        card = {"id": "c-123", "title": func_args["title"]}
        r.lpush("wf:board:Espera", json.dumps(card))

        # ❌ BAD: No job queue, no audit, no retry
        return card["id"]
```

**Why this is wrong:**
- **Bypasses queue** - no audit trail, no retry logic
- **Tight coupling** - chat needs to know Redis schema
- **WIP limits** - can't enforce work-in-progress constraints
- **No validation** - card might be malformed
- **No SSE** - frontend doesn't update

## Agent Implementation

### ✅ GOOD: Board Operator Agent

**File: `src/agents/board_operator.py`**

```python
class BoardOperatorAgent:
    """Agent that consumes jobs and executes board operations."""

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute board operation from job payload."""
        action = payload.get("action")

        if action == "move_card":
            return self._move_card(payload)

    def _move_card(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Move card using cockpit helpers (not HTTP)."""
        # ✅ Use cockpit helpers (Redis-based)
        from wordflux_cockpit import find_and_remove, push_to, emit_event

        card_ref = payload.get("card_ref")
        to_column = payload.get("to_column")

        # Find card in any column (Redis LRANGE)
        card, from_column = find_and_remove(card_ref)

        if not card:
            raise ValueError(f"Card not found: {card_ref}")

        # Move to target column (Redis LPUSH)
        if not push_to(to_column, card):
            # WIP limit exceeded - revert
            push_to(from_column, card, bypass_wip=True)
            raise RuntimeError(f"WIP limit exceeded in {to_column}")

        # ✅ Emit SSE event for UI update
        emit_event("board_update", {
            "action": "move",
            "card_id": card["id"],
            "from": from_column,
            "to": to_column
        })

        return {"success": True, "card_id": card["id"]}
```

## Prohibited Patterns

### 1. Direct HTTP Board APIs

```python
# ❌ NEVER DO THIS
requests.post("http://localhost:8081/board/card", json={...})
requests.put(f"http://localhost:8081/board/card/{card_id}", json={...})
requests.delete(f"http://localhost:8081/board/card/{card_id}")
```

### 2. Direct Redis Board Manipulation

```python
# ❌ NEVER DO THIS in chat.py
r.lpush("wf:board:Espera", json.dumps(card))
r.lrem("wf:board:Produção", 1, raw_card)
```

### 3. Synchronous Board Operations

```python
# ❌ NEVER DO THIS
def create_card(title):
    card = create_card_in_board(title)  # Blocks until done
    return card  # Chat response delayed
```

## Verification

Run the verification script to ensure compliance:

```bash
./test_dry_handlers.sh
```

This script checks:
1. ✅ Zero `/board/write` API calls in handlers
2. ✅ Zero HTTP requests in `chat.py`
3. ✅ Zero HTTP requests in `board_operator.py`
4. ✅ All handlers use `queue.publish()`
5. ✅ SSE events via Redis pub/sub
6. ✅ Board operations via cockpit helpers
7. ✅ Job creation follows standard pattern
8. ✅ No direct Redis board mutations in chat

## Benefits of Dry Handlers

1. **Testability**: Handlers can be unit tested without side effects
2. **Observability**: All operations logged in job queue
3. **Reliability**: Jobs can be retried on failure
4. **Scalability**: Workers can scale independently
5. **Decoupling**: Chat doesn't depend on board implementation
6. **Auditability**: Full history of operations in ledger
7. **Real-time**: SSE provides instant UI updates

## Related Documentation

- [Job Queue Architecture](../core/queue.md)
- [SSE Implementation](../api/sse.md)
- [Board Operator Agent](../../src/agents/board_operator.py)
- [Cockpit Helpers](../../playbooks/cockpit/wordflux_cockpit.py)

## Checklist for New Tool Handlers

When adding a new tool handler:

- [ ] Create Job object with `agent` and `payload`
- [ ] Publish job using `queue.publish(job)`
- [ ] Emit SSE event using `emit_sse_event(kind, payload)`
- [ ] Return job ID (not result)
- [ ] NO HTTP requests to board APIs
- [ ] NO direct Redis list mutations
- [ ] NO synchronous blocking operations
- [ ] Add corresponding agent implementation
- [ ] Run `./test_dry_handlers.sh` to verify

## FAQ

**Q: Why can't handlers just call the board HTTP API directly?**

A: Direct HTTP calls create tight coupling, block the chat response, have no retry logic, no audit trail, and don't integrate with the job queue for monitoring.

**Q: Can agents make HTTP calls?**

A: Agents can make external HTTP calls (e.g., GitHub API, Slack webhook), but **never** to internal board APIs. Board operations must use cockpit helpers.

**Q: What if I need an immediate response?**

A: Handlers are still fast (~10ms) because queueing is asynchronous. For truly immediate operations, use SSE to update the UI as soon as the job is queued.

**Q: How do I debug job execution?**

A: Check the job ledger in Redis (`wf:ledger:*`), view worker logs, or monitor the `/queue/status` endpoint.
