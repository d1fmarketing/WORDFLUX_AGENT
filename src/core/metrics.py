#!/usr/bin/env python3
"""Prometheus metrics for WordFlux."""
from __future__ import annotations

import errno
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from prometheus_client import Counter, Histogram, Gauge, start_http_server, CollectorRegistry

logger = logging.getLogger(__name__)

# Create custom registry to avoid conflicts
registry = CollectorRegistry()

# Job metrics
jobs_enqueued = Counter(
    'wordflux_jobs_enqueued_total',
    'Total number of jobs enqueued',
    ['agent'],
    registry=registry
)

jobs_processed = Counter(
    'wordflux_jobs_processed_total',
    'Total number of jobs processed',
    ['agent', 'status'],  # status: success/error
    registry=registry
)

job_processing_duration = Histogram(
    'wordflux_job_processing_duration_seconds',
    'Job processing duration in seconds',
    ['agent'],
    registry=registry
)

jobs_in_queue = Gauge(
    'wordflux_jobs_in_queue',
    'Number of jobs currently in queue',
    registry=registry
)

jobs_in_processing = Gauge(
    'wordflux_jobs_in_processing',
    'Number of jobs currently being processed',
    registry=registry
)

# API metrics
api_requests = Counter(
    'wordflux_api_requests_total',
    'Total API requests',
    ['endpoint', 'method', 'status_code'],
    registry=registry
)

api_request_duration = Histogram(
    'wordflux_api_request_duration_seconds',
    'API request duration in seconds',
    ['endpoint', 'method'],
    registry=registry
)

idempotency_hits = Counter(
    'wordflux_idempotency_hits_total',
    'Total idempotent requests served from cache',
    registry=registry
)

# Worker metrics
worker_errors = Counter(
    'wordflux_worker_errors_total',
    'Total worker errors',
    ['agent'],
    registry=registry
)

# S3 metrics
s3_uploads = Counter(
    'wordflux_s3_uploads_total',
    'Total S3 uploads',
    ['status'],  # success/error
    registry=registry
)

s3_upload_duration = Histogram(
    'wordflux_s3_upload_duration_seconds',
    'S3 upload duration in seconds',
    registry=registry
)

# Chat metrics
chat_requests = Counter(
    'wordflux_chat_requests_total',
    'Total chat API requests',
    ['provider', 'status'],  # provider: openai/anthropic/mock, status: success/error/pending
    registry=registry
)

chat_latency = Histogram(
    'wordflux_chat_latency_seconds',
    'Chat request latency in seconds',
    ['provider'],
    registry=registry
)

chat_cost_daily = Gauge(
    'wordflux_chat_cost_usd_daily',
    'Estimated daily chat cost in USD',
    ['provider'],
    registry=registry
)

chat_messages = Counter(
    'wordflux_chat_messages_total',
    'Total chat messages',
    ['role', 'provider'],  # role: user/assistant, provider: openai/anthropic/mock
    registry=registry
)

chat_tool_calls = Counter(
    'wordflux_chat_tool_calls_total',
    'Total chat tool invocations',
    ['tool', 'provider'],  # tool: suggest_actions/etc, provider: openai/anthropic/mock
    registry=registry
)

chat_pending_approvals = Gauge(
    'wordflux_chat_pending_approvals',
    'Number of pending approvals',
    registry=registry
)

summary_skipped_total = Counter(
    'wordflux_summary_skipped_total',
    'Resumo pulado por indisponibilidade do board',
    registry=registry
)

chat_rate_limit_hits = Counter(
    'wordflux_chat_rate_limit_hits_total',
    'Rate limit hits',
    registry=registry
)

sse_events_total = Counter(
    'wordflux_sse_events_total',
    'Total SSE events emitidos',
    ['kind'],
    registry=registry
)

pending_confirms_total = Counter(
    'wordflux_pending_confirms_total',
    'Total de solicitações de confirmação pendentes',
    registry=registry
)

confirmations_total = Counter(
    'wordflux_confirmations_total',
    'Total de confirmações processadas',
    ['decision'],
    registry=registry
)

chat_latency_ms = Histogram(
    'wordflux_chat_latency_ms',
    'Latência do chat (ms) até o primeiro SSE',
    buckets=[50, 100, 250, 500, 750, 1000, 2000, 5000, float('inf')],
    registry=registry
)

job_latency_ms = Histogram(
    'wordflux_job_latency_ms',
    'Latência de execução dos jobs (ms)',
    ['action'],
    buckets=[50, 100, 250, 500, 1000, 2000, 5000, 10000, float('inf')],
    registry=registry
)

# Board operation metrics
board_writer_total = Counter(
    'wordflux_board_writer_total',
    'Total board write operations',
    ['column', 'operation'],  # column: PT name, operation: create/move/update
    registry=registry
)

board_writer_rejected_total = Counter(
    'wordflux_board_writer_rejected_total',
    'Total rejected board write operations',
    ['reason'],  # reason: invalid_column, wip_limit, card_not_found
    registry=registry
)

board_column_cards = Gauge(
    'wordflux_board_column_cards',
    'Number of cards per column',
    ['column'],  # column: PT name
    registry=registry
)

llm_fallback_total = Counter(
    'wordflux_llm_fallback_total',
    'LLM fallback occurrences',
    ['from_provider', 'to_provider', 'reason'],  # reason: asl3_block, runtime_error, timeout, etc
    registry=registry
)

circuit_breaker_trips = Counter(
    'wordflux_circuit_breaker_trips_total',
    'Circuit breaker trip events',
    ['provider', 'reason'],  # provider: anthropic/openai, reason: rate_limit/timeout/error
    registry=registry
)

# Redis infrastructure metrics
redis_memory_used_bytes = Gauge(
    'wordflux_redis_memory_used_bytes',
    'Redis memory usage in bytes',
    registry=registry
)

redis_memory_max_bytes = Gauge(
    'wordflux_redis_memory_max_bytes',
    'Redis max memory limit in bytes (maxmemory config)',
    registry=registry
)

redis_keyspace_size = Gauge(
    'wordflux_redis_keyspace_size',
    'Number of keys in Redis keyspace by pattern',
    ['pattern'],  # pattern: wf:chat:*, wf:cb:*, wf:jobs:*, etc
    registry=registry
)

# Token estimation accuracy
llm_token_estimation_accuracy = Histogram(
    'wordflux_llm_token_estimation_accuracy_ratio',
    'Ratio of estimated tokens to actual tokens (estimated/actual)',
    ['provider'],  # provider: openai/anthropic/mock
    buckets=[0.5, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 2.0, float('inf')],
    registry=registry
)


def start_metrics_server(port: int = None) -> None:
    """Start Prometheus metrics HTTP server."""
    if port is None:
        port = int(os.getenv("METRICS_PORT", "9300"))

    try:
        start_http_server(port, registry=registry)
        logger.info(f"Metrics server started on port {port}")
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            logger.info("Metrics server already running on port %s", port)
        else:
            logger.error("Failed to start metrics server: %s", exc)
    except Exception as e:  # pragma: no cover - unexpected errors
        logger.error(f"Failed to start metrics server: {e}")


def record_job_enqueued(agent: str) -> None:
    """Record job enqueued metric."""
    jobs_enqueued.labels(agent=agent).inc()


def record_job_processed(agent: str, success: bool, duration: float) -> None:
    """Record job processed metrics."""
    status = "success" if success else "error"
    jobs_processed.labels(agent=agent, status=status).inc()
    job_processing_duration.labels(agent=agent).observe(duration)


def record_api_request(endpoint: str, method: str, status_code: int, duration: float) -> None:
    """Record API request metrics."""
    api_requests.labels(endpoint=endpoint, method=method, status_code=str(status_code)).inc()
    api_request_duration.labels(endpoint=endpoint, method=method).observe(duration)


def record_idempotency_hit() -> None:
    """Record idempotency cache hit."""
    idempotency_hits.inc()


def record_worker_error(agent: str) -> None:
    """Record worker error."""
    worker_errors.labels(agent=agent).inc()


def record_s3_upload(success: bool, duration: float) -> None:
    """Record S3 upload metrics."""
    status = "success" if success else "error"
    s3_uploads.labels(status=status).inc()
    s3_upload_duration.observe(duration)


def update_queue_metrics(queue_size: int, processing_size: int) -> None:
    """Update queue size metrics."""
    jobs_in_queue.set(queue_size)
    jobs_in_processing.set(processing_size)


def record_chat_message(role: str, provider: str = "unknown") -> None:
    """
    Record chat message metric.

    Args:
        role: Message role (user/assistant)
        provider: LLM provider (openai/anthropic/mock)
    """
    chat_messages.labels(role=role, provider=provider).inc()


def record_chat_tool_call(tool: str, provider: str = "unknown") -> None:
    """
    Record chat tool call metric.

    Args:
        tool: Tool name (suggest_actions/propose_move/etc)
        provider: LLM provider (openai/anthropic/mock)
    """
    chat_tool_calls.labels(tool=tool, provider=provider).inc()


def record_summary_skipped() -> None:
    """Increment skipped summary counter."""
    summary_skipped_total.inc()


def update_pending_approvals(count: int) -> None:
    """Update pending approvals gauge."""
    chat_pending_approvals.set(count)


def record_rate_limit_hit() -> None:
    """Record rate limit hit."""
    chat_rate_limit_hits.inc()


def record_sse_event(kind: str) -> None:
    """Increment SSE event counter."""
    sse_events_total.labels(kind=kind).inc()


def record_pending_confirmation() -> None:
    """Record when a pending confirmation is created."""
    pending_confirms_total.inc()


def record_confirmation(decision: str) -> None:
    """Record confirmation outcome."""
    confirmations_total.labels(decision=decision).inc()


def record_chat_latency_ms(latency_ms: float) -> None:
    """Observe chat latency in milliseconds."""
    chat_latency_ms.observe(latency_ms)


def record_job_latency_ms(action: str, latency_ms: float) -> None:
    """Observe job latency in milliseconds."""
    job_latency_ms.labels(action=action).observe(latency_ms)


def record_llm_fallback(from_provider: str, to_provider: str, reason: str) -> None:
    """
    Record LLM fallback occurrence.

    Args:
        from_provider: Original provider that failed (openai/anthropic)
        to_provider: Fallback provider used (openai/anthropic/mock)
        reason: Failure reason (asl3_block/runtime_error/timeout/etc)
    """
    llm_fallback_total.labels(
        from_provider=from_provider,
        to_provider=to_provider,
        reason=reason
    ).inc()


def record_circuit_breaker_trip(provider: str, reason: str) -> None:
    """
    Record circuit breaker trip event.

    Args:
        provider: Provider that tripped (anthropic/openai)
        reason: Trip reason (rate_limit/timeout/runtime_error/unknown_error)
    """
    circuit_breaker_trips.labels(provider=provider, reason=reason).inc()


def record_chat_request(provider: str, status: str, latency: float) -> None:
    """
    Record chat request with latency.

    Args:
        provider: LLM provider (openai/anthropic/mock)
        status: Request status (success/error/pending)
        latency: Request latency in seconds
    """
    chat_requests.labels(provider=provider, status=status).inc()
    chat_latency.labels(provider=provider).observe(latency)


def update_chat_cost(provider: str, cost: float) -> None:
    """
    Update daily chat cost gauge (additive).

    Args:
        provider: LLM provider (openai/anthropic/mock)
        cost: Cost to add in USD
    """
    # Use inc() to add to gauge (Gauge supports additive operations)
    chat_cost_daily.labels(provider=provider).inc(cost)


def reset_daily_costs() -> None:
    """Reset daily cost gauges (call this daily via cron)."""
    for provider in ['openai', 'anthropic', 'mock']:
        chat_cost_daily.labels(provider=provider).set(0)


def record_token_estimation_accuracy(provider: str, estimated_tokens: int, actual_tokens: int) -> None:
    """
    Record token estimation accuracy.

    Args:
        provider: LLM provider (openai/anthropic/mock)
        estimated_tokens: Estimated token count (before LLM call)
        actual_tokens: Actual token count (from LLM response)
    """
    if actual_tokens <= 0:
        # Cannot calculate ratio if actual is 0 or missing
        logger.warning(f"Cannot record token estimation accuracy: actual_tokens={actual_tokens}")
        return

    ratio = estimated_tokens / actual_tokens
    llm_token_estimation_accuracy.labels(provider=provider).observe(ratio)


# Metrics collection utilities
def get_redis_queue_metrics() -> Dict[str, int]:
    """Get queue metrics from Redis."""
    try:
        import redis
        url = os.getenv("REDIS_URL")
        if url:
            client = redis.Redis.from_url(url, decode_responses=True)
        else:
            host = os.getenv("REDIS_HOST", "127.0.0.1")
            port = int(os.getenv("REDIS_PORT", "6379"))
            db = int(os.getenv("REDIS_DB", "0"))
            password = os.getenv("REDIS_PASSWORD") or None
            client = redis.Redis(host=host, port=port, db=db, password=password, decode_responses=True)

        queue_key = os.getenv("REDIS_QUEUE_KEY", "wordflux:jobs")
        processing_key = f"{queue_key}:processing"

        queue_size = client.llen(queue_key)
        processing_size = client.llen(processing_key)

        return {
            "queue_size": queue_size,
            "processing_size": processing_size
        }
    except Exception as e:
        logger.error(f"Failed to get Redis metrics: {e}")
        return {"queue_size": 0, "processing_size": 0}


# Redis infrastructure metrics collection
_redis_metrics_thread: Optional[threading.Thread] = None
_redis_metrics_stop = threading.Event()


def collect_redis_metrics_once() -> None:
    """
    Collect Redis infrastructure metrics once.

    This function queries Redis INFO command and updates Prometheus gauges.
    Safe to call even if Redis is unavailable (metrics will show 0).
    """
    try:
        import redis

        # Get Redis client
        url = os.getenv("REDIS_URL")
        if url:
            client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2.0)
        else:
            host = os.getenv("REDIS_HOST", "127.0.0.1")
            port = int(os.getenv("REDIS_PORT", "6379"))
            db = int(os.getenv("REDIS_DB", "0"))
            password = os.getenv("REDIS_PASSWORD") or None
            client = redis.Redis(
                host=host, port=port, db=db, password=password,
                decode_responses=True, socket_timeout=2.0
            )

        # Get memory info
        info = client.info('memory')
        redis_memory_used_bytes.set(info.get('used_memory', 0))

        # Get maxmemory (0 means unlimited)
        maxmemory = int(info.get('maxmemory', 0))
        redis_memory_max_bytes.set(maxmemory)

        # Get keyspace size by pattern
        key_patterns = [
            'wf:chat:*',     # Chat sessions, proposals, history
            'wf:cb:*',       # Circuit breaker keys
            'wf:jobs:*',     # Job queue keys
            'wf:approval:*', # Approval idempotency keys
            'wf:cost:*',     # Cost tracking keys
        ]

        for pattern in key_patterns:
            try:
                # Use SCAN for efficiency (don't block Redis)
                cursor = 0
                count = 0
                while True:
                    cursor, keys = client.scan(cursor, match=pattern, count=100)
                    count += len(keys)
                    if cursor == 0:
                        break

                redis_keyspace_size.labels(pattern=pattern).set(count)
            except Exception as scan_error:
                logger.warning(f"Failed to scan pattern {pattern}: {scan_error}")
                redis_keyspace_size.labels(pattern=pattern).set(0)

    except Exception as e:
        logger.error(f"Failed to collect Redis metrics: {e}")
        # Set metrics to 0 on failure
        redis_memory_used_bytes.set(0)
        redis_memory_max_bytes.set(0)


def start_redis_metrics_collection(interval: int = 60) -> None:
    """
    Start background thread to collect Redis metrics periodically.

    Args:
        interval: Collection interval in seconds (default: 60)
    """
    global _redis_metrics_thread

    if _redis_metrics_thread is not None and _redis_metrics_thread.is_alive():
        logger.warning("Redis metrics collection already running")
        return

    def collection_loop():
        logger.info(f"Redis metrics collection started (interval: {interval}s)")
        while not _redis_metrics_stop.is_set():
            try:
                collect_redis_metrics_once()
            except Exception as e:
                logger.error(f"Error in Redis metrics collection loop: {e}")

            # Wait for interval or stop signal
            _redis_metrics_stop.wait(interval)

        logger.info("Redis metrics collection stopped")

    _redis_metrics_stop.clear()
    _redis_metrics_thread = threading.Thread(target=collection_loop, daemon=True, name="RedisMetricsCollector")
    _redis_metrics_thread.start()


def stop_redis_metrics_collection() -> None:
    """Stop background Redis metrics collection thread."""
    global _redis_metrics_thread

    if _redis_metrics_thread is None:
        return

    _redis_metrics_stop.set()
    _redis_metrics_thread.join(timeout=5)
    _redis_metrics_thread = None


__all__ = [
    "registry",
    "start_metrics_server",
    "record_job_enqueued",
    "record_job_processed",
    "record_api_request",
    "record_idempotency_hit",
    "record_worker_error",
    "record_s3_upload",
    "update_queue_metrics",
    "get_redis_queue_metrics",
    "record_chat_message",
    "record_chat_tool_call",
    "update_pending_approvals",
    "record_rate_limit_hit",
    "record_sse_event",
    "record_pending_confirmation",
    "record_confirmation",
    "record_chat_latency_ms",
    "record_job_latency_ms",
    "record_llm_fallback",
    "record_circuit_breaker_trip",
    "record_chat_request",
    "update_chat_cost",
    "reset_daily_costs",
    "record_token_estimation_accuracy",
    "collect_redis_metrics_once",
    "start_redis_metrics_collection",
    "stop_redis_metrics_collection"
]
