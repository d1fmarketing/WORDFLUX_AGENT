#!/usr/bin/env python3
"""FastAPI application for WordFlux with idempotency support."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.core.job import Job
from src.core.queue import load_default_queue

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Redis client for idempotency
redis_client = None
IDEMPOTENCY_TTL = 3600  # 1 hour TTL for idempotency keys


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
        from src.core.metrics import start_metrics_server, record_api_request
        metrics_port = int(os.getenv("METRICS_PORT", "9300"))
        start_metrics_server(metrics_port)
        logger.info(f"Metrics server started on port {metrics_port}")
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
    global redis_client
    if redis_client:
        redis_client.close()


app = FastAPI(
    title="WordFlux API",
    description="Event-driven agent orchestration API",
    version="1.0.0",
    lifespan=lifespan
)


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


@app.post("/event", response_model=EventResponse)
async def handle_event(request: EventRequest, app_request: Request):
    """
    Handle incoming events with idempotency.

    Events are converted to jobs and enqueued for processing.
    """
    start_time = datetime.now(timezone.utc)

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
        # Map event type to agent
        agent_mapping = {
            "stripe.dispute": "stripe_disputes",
            "board.webhook": "board_webhook",
            "pipeline.trigger": "pipeline_trigger",
            "slack.notify": "slack_notifier",
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


def main():
    """Run the FastAPI application."""
    port = int(os.getenv("API_PORT", "8000"))
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