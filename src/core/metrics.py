#!/usr/bin/env python3
"""Prometheus metrics for WordFlux."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

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


def start_metrics_server(port: int = None) -> None:
    """Start Prometheus metrics HTTP server."""
    if port is None:
        port = int(os.getenv("METRICS_PORT", "9300"))

    try:
        start_http_server(port, registry=registry)
        logger.info(f"Metrics server started on port {port}")
    except Exception as e:
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


__all__ = [
    "start_metrics_server",
    "record_job_enqueued",
    "record_job_processed",
    "record_api_request",
    "record_idempotency_hit",
    "record_worker_error",
    "record_s3_upload",
    "update_queue_metrics",
    "get_redis_queue_metrics"
]