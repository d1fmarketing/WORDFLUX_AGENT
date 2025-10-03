#!/usr/bin/env python3
"""FastAPI application for WordFlux with idempotency support."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import redis
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

import src.agents  # noqa: F401  # Import to register agents
from src.core.job import Job
from src.core.queue import load_default_queue

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Redis client for idempotency
redis_client = None
IDEMPOTENCY_TTL = 3600  # 1 hour TTL for idempotency keys

# Event streaming configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
WF_EVENTS_CHANNEL = os.getenv("WF_EVENTS_CHANNEL", "wf:events")
WF_EVENTS_LIST = os.getenv("WF_EVENTS_LIST", "wf:events:recent")


def _r_sync(decode: bool = True) -> redis.Redis:
    """Get Redis client for sync operations."""
    return redis.Redis.from_url(REDIS_URL, decode_responses=decode)


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    redis: str
    queue_mode: str


class EventRequest(BaseModel):
    event_type: str
    payload: Dict[str, Any]
    idempotency_key: Optional[str] = None


class EventResponse(BaseModel):
    job_id: str
    status: str
    message: str
    duplicate: bool = False


def get_redis_client():
    """Get Redis client for idempotency."""
    global redis_client
    if redis_client is None:
        try:
            import redis
            url = os.getenv("REDIS_URL")
            if url:
                redis_client = redis.Redis.from_url(url, decode_responses=True)
            else:
                host = os.getenv("REDIS_HOST", "127.0.0.1")
                port = int(os.getenv("REDIS_PORT", "6379"))
                db = int(os.getenv("REDIS_DB", "0"))
                password = os.getenv("REDIS_PASSWORD") or None
                redis_client = redis.Redis(host=host, port=port, db=db, password=password, decode_responses=True)
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
    return redis_client


def generate_idempotency_key(request: EventRequest) -> str:
    """Generate idempotency key from request."""
    if request.idempotency_key:
        return f"idempotency:{request.idempotency_key}"

    # Generate key from request content
    content = f"{request.event_type}:{json.dumps(request.payload, sort_keys=True)}"
    hash_val = hashlib.sha256(content.encode()).hexdigest()
    return f"idempotency:auto:{hash_val}"


async def check_idempotency(key: str) -> Optional[Dict[str, Any]]:
    """Check if request was already processed."""
    client = get_redis_client()
    if not client:
        return None

    try:
        result = client.get(key)
        if result:
            return json.loads(result)
    except Exception as e:
        logger.warning(f"Idempotency check failed: {e}")

    return None


async def save_idempotency(key: str, response: Dict[str, Any]) -> None:
    """Save idempotency result."""
    client = get_redis_client()
    if not client:
        return

    try:
        # Use SET NX for atomic operation
        client.set(key, json.dumps(response), nx=True, ex=IDEMPOTENCY_TTL)
    except Exception as e:
        logger.warning(f"Failed to save idempotency key: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown."""
    # Startup
    logger.info("Starting WordFlux API")
    # Test Redis connection
    client = get_redis_client()
    if client:
        try:
            client.ping()
            logger.info("Redis connected for idempotency")
        except Exception as e:
            logger.warning(f"Redis not available for idempotency: {e}")

    # Start metrics server if available
    try:
        from src.core.metrics import start_metrics_server, record_api_request, start_redis_metrics_collection
        metrics_port = int(os.getenv("METRICS_PORT", "9300"))
        start_metrics_server(metrics_port)
        logger.info(f"Metrics server started on port {metrics_port}")

        # Start Redis metrics collection background thread
        start_redis_metrics_collection(interval=60)
        logger.info("Redis metrics collection started (60s interval)")

        app.state.metrics_enabled = True
    except ImportError:
        logger.info("Metrics disabled - prometheus_client not installed")
        app.state.metrics_enabled = False
    except Exception as e:
        logger.warning(f"Metrics server already running or failed to start: {e}")
        app.state.metrics_enabled = True  # Assume it's running

    yield

    # Shutdown
    logger.info("Shutting down WordFlux API")

    # Stop Redis metrics collection
    try:
        from src.core.metrics import stop_redis_metrics_collection
        stop_redis_metrics_collection()
        logger.info("Redis metrics collection stopped")
    except Exception as e:
        logger.warning(f"Failed to stop Redis metrics collection: {e}")

    global redis_client
    if redis_client:
        redis_client.close()


app = FastAPI(
    title="WordFlux API",
    description="Event-driven agent orchestration API",
    version="1.0.0",
    lifespan=lifespan
)

# Register chat router
from src.api.chat import router as chat_router
app.include_router(chat_router, prefix="/chat", tags=["chat"])
logger.info("Chat router registered at /chat")

# Register audit router
from src.api.audit import router as audit_router
app.include_router(audit_router, prefix="/audit", tags=["audit"])
logger.info("Audit router registered at /audit")


# Exception handlers
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Custom validation error handler for Portuguese error messages.

    Returns 422 with format:
    {
        "erro": "Requisição inválida",
        "falta": ["field1", "field2"]
    }
    """
    errors = exc.errors()
    missing_fields = []
    invalid_fields = []

    for error in errors:
        field_path = ".".join(str(loc) for loc in error["loc"] if loc != "body")
        error_type = error["type"]

        if "missing" in error_type:
            missing_fields.append(field_path)
        else:
            # For value_error from validators, extract message
            msg = error.get("msg", "")
            if "Campo 'text' é obrigatório" in msg:
                missing_fields.append("text")
            else:
                invalid_fields.append(field_path)

    # Build response
    response = {"erro": "Requisição inválida"}

    if missing_fields:
        response["falta"] = missing_fields

    if invalid_fields:
        response["invalidos"] = invalid_fields

    # Add details for debugging (optional, can be removed in production)
    if not missing_fields and not invalid_fields:
        # Generic validation error
        response["detalhes"] = errors[0].get("msg", "Erro de validação") if errors else "Erro de validação"

    return JSONResponse(
        status_code=422,
        content=response
    )


@app.get("/")
async def index() -> HTMLResponse:
    """Serve the operator cockpit UI."""
    html_content = INDEX_HTML
    return HTMLResponse(content=html_content)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    redis_status = "unavailable"
    client = get_redis_client()
    if client:
        try:
            client.ping()
            redis_status = "connected"
        except Exception:
            redis_status = "error"

    return HealthResponse(
        status="healthy",
        timestamp=datetime.now(timezone.utc).isoformat(),
        redis=redis_status,
        queue_mode=os.getenv("QUEUE_MODE", "memory")
    )


@app.get("/skills")
async def get_skills():
    """Return list of available skills/agents."""
    from src.core.registry import available_agents
    agents = available_agents()
    return [{"id": agent} for agent in agents]


@app.get("/events/recent")
async def recent_events():
    """Get recent events for UI bootstrap."""
    try:
        r = _r_sync(True)
        items = r.lrange(WF_EVENTS_LIST, 0, 49)  # newest first
        return [json.loads(x) for x in items]
    except Exception as e:
        logger.error(f"Failed to get recent events: {e}")
        return []


@app.get("/events/stream")
async def event_stream():
    """SSE endpoint for live event streaming."""
    async def generate():
        r = _r_sync(True)
        pubsub = r.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(WF_EVENTS_CHANNEL)
        q: asyncio.Queue[str] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        stop = False

        def reader():
            try:
                for msg in pubsub.listen():
                    if stop:
                        break
                    if msg and msg.get("type") == "message":
                        data = msg.get("data")
                        asyncio.run_coroutine_threadsafe(q.put(data), loop)
            except Exception:
                pass

        th = threading.Thread(target=reader, daemon=True)
        th.start()

        try:
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=10.0)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    # Send heartbeat to keep connection alive
                    yield f": ping {int(time.time())}\n\n"
        finally:
            stop = True
            try:
                pubsub.close()
            except Exception:
                pass

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/metrics", response_class=PlainTextResponse)
async def get_metrics():
    """
    Prometheus metrics endpoint.

    Returns metrics in Prometheus text format.
    """
    try:
        from prometheus_client import generate_latest
        from src.core.metrics import registry

        # Generate metrics in Prometheus format
        metrics_output = generate_latest(registry)
        return PlainTextResponse(content=metrics_output, media_type="text/plain; version=0.0.4")
    except ImportError:
        raise HTTPException(status_code=503, detail="Metrics not available - prometheus_client not installed")
    except Exception as e:
        logger.error(f"Failed to generate metrics: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate metrics")


@app.post("/event", response_model=EventResponse)
async def handle_event(app_request: Request, request: EventRequest | Dict[str, Any] = Body(...)):
    """
    Handle incoming events with idempotency.

    Events are converted to jobs and enqueued for processing.
    """
    start_time = datetime.now(timezone.utc)

    # Handle both EventRequest model and dict from UI
    if isinstance(request, dict):
        # Convert dict to EventRequest
        event_type = request.get("action", request.get("event_type", ""))
        payload = request.get("payload", {})
        idempotency_key = request.get("job_id", request.get("idempotency_key"))
        request = EventRequest(
            event_type=event_type,
            payload=payload,
            idempotency_key=idempotency_key
        )

    # Check idempotency
    idempotency_key = generate_idempotency_key(request)
    cached_response = await check_idempotency(idempotency_key)

    if cached_response:
        logger.info(f"Idempotent request served from cache: {idempotency_key}")
        # Record metrics
        if hasattr(app_request.app.state, 'metrics_enabled') and app_request.app.state.metrics_enabled:
            from src.core.metrics import record_idempotency_hit, record_api_request
            record_idempotency_hit()
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            record_api_request("/event", "POST", 200, duration)
        # Add duplicate flag to cached response
        cached_response["duplicate"] = True
        return EventResponse(**cached_response)

    try:
        # Check if event_type is already a registered agent
        from src.core.registry import available_agents
        registered_agents = available_agents()

        if request.event_type in registered_agents:
            # Direct agent name from UI
            agent = request.event_type
        else:
            # Map event type to agent
            agent_mapping = {
                "stripe.dispute": "stripe_disputes",
                "board.webhook": "board_webhook",
                "pipeline.trigger": "pipeline_trigger",
                "slack.notify": "slack_notifier",
                "linear.update": "linear_connector",
                "echo": "echo",
                # Add more mappings as needed
            }
            agent = agent_mapping.get(request.event_type)

        if not agent:
            raise HTTPException(status_code=400, detail=f"Unknown event type: {request.event_type}")

        # Create job
        job = Job(
            agent=agent,
            payload={
                "event_type": request.event_type,
                **request.payload
            }
        )

        # Enqueue job
        queue = load_default_queue()
        queue.publish(job)

        # Record in ledger if available
        try:
            from src.core.ledger import get_ledger
            ledger = get_ledger()
            ledger.record_job_enqueued(job.job_id, agent, job.payload)
        except Exception as e:
            logger.debug(f"Failed to record job in ledger: {e}")

        response = {
            "job_id": job.job_id,
            "status": "enqueued",
            "message": f"Job {job.job_id} enqueued for agent {agent}",
            "duplicate": False
        }

        # Save idempotency result
        await save_idempotency(idempotency_key, response)

        logger.info(f"Event processed: type={request.event_type}, job_id={job.job_id}")

        # Record metrics
        if hasattr(app_request.app.state, 'metrics_enabled') and app_request.app.state.metrics_enabled:
            from src.core.metrics import record_job_enqueued, record_api_request
            record_job_enqueued(agent)
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            record_api_request("/event", "POST", 200, duration)

        return EventResponse(**response)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to process event: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )


# HTML cockpit UI
INDEX_HTML = """<!doctype html>
<html lang="pt-BR" data-theme="dark"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WordFlux — AI pronta para comandar o fluxo</title>
<style>
:root {
  /* Brand colors (from PDF spec) */
  --wf-pink-500: #FF227A;
  --wf-orange-500: #FF7A00;
  --wf-grad: linear-gradient(135deg, var(--wf-pink-500), var(--wf-orange-500));

  /* Dark theme */
  --wf-bg-950: #0B0F17;
  --wf-bg-900: #111827;
  --wf-card-800: #1A2233;
  --wf-text-100: #F8FAFC;
  --wf-text-400: #9CA3AF;
  --wf-border: #243044;

  /* Light theme (toggle support) */
  --wf-bg-50: #F6F7FB;
  --wf-card-0: #FFFFFF;
  --wf-text-900: #0B0F17;
  --wf-text-600: #4B5563;
}

[data-theme="light"] {
  --wf-bg-950: #F6F7FB;
  --wf-bg-900: #F2F4F8;
  --wf-card-800: #FFFFFF;
  --wf-text-100: #0B0F17;
  --wf-text-400: #4B5563;
  --wf-border: #E5E7EB;
}

* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  background: var(--wf-bg-900);
  color: var(--wf-text-100);
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* Header */
.header {
  background: var(--wf-card-800);
  border-bottom: 1px solid var(--wf-border);
  padding: 12px 20px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.brand {
  font-size: 18px;
  font-weight: 600;
  background: var(--wf-grad);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}
.status { font-size: 12px; color: var(--wf-text-400); }

/* Main layout: chat (left) + board (right) */
.main { display: flex; flex: 1; overflow: hidden; }

/* Chat panel (left, 360px) */
.chat-panel {
  width: 360px;
  background: var(--wf-bg-950);
  border-right: 1px solid var(--wf-border);
  display: flex;
  flex-direction: column;
}
.chat-header {
  padding: 16px;
  border-bottom: 1px solid var(--wf-border);
}
.chat-header h2 {
  font-size: 14px;
  margin-bottom: 12px;
  color: var(--wf-text-100);
}
.chips {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.chip {
  background: var(--wf-grad);
  color: white;
  border: none;
  border-radius: 16px;
  padding: 6px 12px;
  font-size: 11px;
  cursor: pointer;
  font-weight: 500;
  transition: transform 0.1s;
}
.chip:hover { transform: scale(1.05); }
.chip:active { transform: scale(0.98); }

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.message {
  padding: 10px 12px;
  border-radius: 12px;
  font-size: 13px;
  line-height: 1.5;
  max-width: 90%;
}
.message.user {
  background: var(--wf-card-800);
  border: 1px solid var(--wf-border);
  align-self: flex-end;
  margin-left: auto;
}
.message.assistant {
  background: var(--wf-bg-900);
  border: 1px solid var(--wf-border);
  align-self: flex-start;
}
.message pre {
  background: rgba(0,0,0,0.2);
  padding: 8px;
  border-radius: 6px;
  overflow-x: auto;
  font-size: 11px;
  margin-top: 8px;
}

.chat-input {
  border-top: 1px solid var(--wf-border);
  padding: 12px;
  display: flex;
  gap: 8px;
}
.chat-input input {
  flex: 1;
  background: var(--wf-card-800);
  border: 1px solid var(--wf-border);
  border-radius: 8px;
  padding: 8px 12px;
  color: var(--wf-text-100);
  font-size: 13px;
}
.chat-input input:focus { outline: none; border-color: var(--wf-pink-500); }
.chat-input button {
  background: var(--wf-grad);
  color: white;
  border: none;
  border-radius: 8px;
  padding: 8px 16px;
  font-weight: 500;
  cursor: pointer;
  font-size: 13px;
}
.chat-input button:hover { filter: brightness(1.1); }
.chat-input button:active { filter: brightness(0.95); }

/* Board panel (right) */
.board-panel {
  flex: 1;
  overflow-x: auto;
  padding: 20px;
}
.board {
  display: flex;
  gap: 16px;
  min-width: max-content;
}
.column {
  background: var(--wf-card-800);
  border-radius: 12px;
  padding: 16px;
  min-width: 280px;
  max-width: 320px;
}
.column-header {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 2px solid var(--wf-border);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.card {
  background: var(--wf-bg-900);
  border: 1px solid var(--wf-border);
  border-radius: 10px;
  padding: 12px;
  margin-bottom: 10px;
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.2s, transform 0.1s;
}
.card:hover {
  border-color: var(--wf-pink-500);
  transform: translateY(-2px);
}
.card-title { font-weight: 600; margin-bottom: 6px; }
.card-meta {
  font-size: 11px;
  color: var(--wf-text-400);
  margin-top: 6px;
  display: flex;
  gap: 8px;
  align-items: center;
}
.card-tag {
  background: rgba(255, 34, 122, 0.1);
  color: var(--wf-pink-500);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 10px;
}

/* Loading states */
.loading {
  color: var(--wf-text-400);
  font-size: 12px;
  text-align: center;
  padding: 20px;
}

/* Empty states */
.empty {
  color: var(--wf-text-400);
  font-size: 12px;
  text-align: center;
  padding: 40px 20px;
}
</style>
</head><body>
  <div class="header">
    <div class="brand">WordFlux AI</div>
    <div class="status" id="status">Conectando...</div>
  </div>

  <div class="main">
    <!-- Chat Panel (Left) -->
    <div class="chat-panel">
      <div class="chat-header">
        <h2>A IA pronta para comandar o fluxo</h2>
        <div class="chips">
          <button class="chip" data-prompt="Planeje o amanhã">Planeje o amanhã</button>
          <button class="chip" data-prompt="Mostrar minhas tarefas">Mostrar minhas tarefas</button>
          <button class="chip" data-prompt="Limpar concluído">Limpar Concluído</button>
        </div>
      </div>
      <div class="chat-messages" id="chatMessages">
        <div class="empty">Digite uma mensagem ou clique em um chip para começar</div>
      </div>
      <div class="chat-input">
        <input type="text" id="chatInput" placeholder="Digite sua mensagem..." />
        <button id="chatSend">Enviar</button>
      </div>
    </div>

    <!-- Board Panel (Right) -->
    <div class="board-panel">
      <div class="board" id="board">
        <div class="loading">Carregando board...</div>
      </div>
    </div>
  </div>

<script>
// Generate unique session ID
const sessionId = `sess-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

// Elements
const statusEl = document.getElementById('status');
const chatMessagesEl = document.getElementById('chatMessages');
const chatInputEl = document.getElementById('chatInput');
const chatSendBtn = document.getElementById('chatSend');
const boardEl = document.getElementById('board');

// State
let sseConnected = false;
const columns = ['Espera', 'Produção', 'Aprovação', 'Agendado', 'Finalizado'];
const boardState = {};
columns.forEach(col => boardState[col] = []);

// Init
async function init() {
  // Health check
  try {
    const r = await fetch('/health');
    const data = await r.json();
    statusEl.textContent = data.status === 'healthy' ? 'Conectado' : 'Erro de conexão';
  } catch (e) {
    statusEl.textContent = 'Offline';
  }

  // Setup SSE
  setupSSE();

  // Setup event handlers
  chatSendBtn.onclick = sendMessage;
  chatInputEl.onkeypress = (e) => { if (e.key === 'Enter') sendMessage(); };

  // Chip clicks
  document.querySelectorAll('.chip').forEach(chip => {
    chip.onclick = () => {
      const prompt = chip.dataset.prompt;
      chatInputEl.value = prompt;
      sendMessage();
    };
  });

  // Load initial board state
  fetchBoardState();
}

function setupSSE() {
  const es = new EventSource('/events/stream');

  es.onopen = () => {
    sseConnected = true;
    statusEl.textContent = 'Conectado • SSE ativo';
  };

  es.onmessage = (e) => {
    try {
      const event = JSON.parse(e.data);
      handleSSEEvent(event);
    } catch (err) {
      console.error('SSE parse error:', err);
    }
  };

  es.onerror = () => {
    sseConnected = false;
    statusEl.textContent = 'Conectado • SSE desconectado';
    setTimeout(setupSSE, 5000); // Retry
  };
}

function handleSSEEvent(event) {
  const { kind } = event;

  // Chat message events
  if (kind === 'chat_message') {
    addMessage(event.role, event.text);
    return;
  }

  // Board update events
  if (kind === 'board_update') {
    console.log('Board update:', event);
    fetchBoardState();  // Refresh entire board
    return;
  }

  // Job events
  if (kind === 'job_succeeded' || kind === 'job_failed') {
    console.log('Job event:', event);
    fetchBoardState();  // Refresh board after job completion
    return;
  }

  // Other events
  console.log('SSE event:', kind, event);
}

async function sendMessage() {
  const text = chatInputEl.value.trim();
  if (!text) return;

  // Clear input
  chatInputEl.value = '';

  // Add user message
  addMessage('user', text);

  // Send to API
  try {
    const r = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, session_id: sessionId })
    });

    if (!r.ok) {
      addMessage('assistant', `Erro: ${r.status} ${r.statusText}`);
      return;
    }

    const data = await r.json();
    addMessage('assistant', data.message);

    // Handle approval flow
    if (data.requires_approval) {
      const confirmBtn = document.createElement('button');
      confirmBtn.className = 'chip';
      confirmBtn.textContent = 'Confirmar';
      confirmBtn.style.marginTop = '8px';
      confirmBtn.onclick = () => {
        chatInputEl.value = 'sim';
        sendMessage();
      };
      chatMessagesEl.lastChild.appendChild(confirmBtn);
    }
  } catch (err) {
    addMessage('assistant', `Erro de conexão: ${err.message}`);
  }
}

function addMessage(role, text) {
  // Remove empty state
  const emptyEl = chatMessagesEl.querySelector('.empty');
  if (emptyEl) emptyEl.remove();

  const msgEl = document.createElement('div');
  msgEl.className = `message ${role}`;
  msgEl.textContent = text;
  chatMessagesEl.appendChild(msgEl);

  // Scroll to bottom
  chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
}

async function fetchBoardState() {
  try {
    const r = await fetch('/board/state');
    if (!r.ok) {
      console.error('Failed to fetch board state:', r.status);
      renderBoard({});  // Render empty board
      return;
    }

    const data = await r.json();
    renderBoard(data.columns || {});
  } catch (err) {
    console.error('Error fetching board state:', err);
    renderBoard({});  // Render empty board
  }
}

function renderBoard(columnsData) {
  // Render columns with cards
  boardEl.innerHTML = columns.map(col => {
    const cards = columnsData[col] || [];
    const cardEls = cards.length > 0
      ? cards.map(card => `
          <div class="card" data-card-id="${card.id}">
            <div class="card-title">${escapeHtml(card.title)}</div>
            ${card.meta && card.meta.assignee ? `<div class="card-meta">👤 ${escapeHtml(card.meta.assignee)}</div>` : ''}
            ${card.meta && card.meta.labels && card.meta.labels.length > 0 ? `<div class="card-meta">${card.meta.labels.map(l => `<span class="card-tag">${escapeHtml(l)}</span>`).join(' ')}</div>` : ''}
            ${card.meta && card.meta.due ? `<div class="card-meta">📅 ${card.meta.due}</div>` : ''}
          </div>
        `).join('')
      : '<div class="empty">Nenhum card</div>';

    return `
      <div class="column">
        <div class="column-header">${col} (${cards.length})</div>
        ${cardEls}
      </div>
    `;
  }).join('');
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// Bootstrap
init();
</script>
</body></html>
"""


def main():
    """Run the FastAPI application."""
    port = int(os.getenv("API_PORT", "8080"))  # Changed to 8080 as per CLAUDE.md
    host = os.getenv("API_HOST", "0.0.0.0")

    uvicorn.run(
        "src.api.main:app",
        host=host,
        port=port,
        reload=os.getenv("API_RELOAD", "false").lower() == "true",
        log_level="info"
    )


if __name__ == "__main__":
    main()