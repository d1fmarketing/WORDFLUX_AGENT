#!/usr/bin/env python3
"""Prometheus metrics for optimistic updates in WordFlux.

This module tracks the lifecycle of optimistic UI updates:
1. Job queued (chat.py) → card.pending SSE emitted
2. Worker processes job (worker.py)
3. Final event emitted (board_operator.py) → card.created/moved SSE
4. Reconciliation completed (pending → final)

Metrics enable detection of:
- Ghost cards (pending state never resolved)
- SSE emission failures
- Race conditions and out-of-order events
- Reconciliation latency issues
"""
from __future__ import annotations

import logging
from typing import Optional

from prometheus_client import Counter, Histogram, Gauge

# Re-use existing registry to keep all metrics together
from src.core.metrics import registry

logger = logging.getLogger(__name__)

# ============================================================================
# OPTIMISTIC UPDATE LIFECYCLE METRICS
# ============================================================================

# Job lifecycle tracking (deterministic IDs enable reconciliation)
optimistic_jobs_queued = Counter(
    'wordflux_optimistic_jobs_queued_total',
    'Jobs queued with optimistic updates',
    ['action', 'session_id_present'],  # action: create_card, move_card, update_card
    registry=registry
)

optimistic_jobs_reconciled = Counter(
    'wordflux_optimistic_jobs_reconciled_total',
    'Jobs successfully reconciled (pending → final)',
    ['action', 'outcome'],  # outcome: success, failed
    registry=registry
)

optimistic_jobs_orphaned = Counter(
    'wordflux_optimistic_jobs_orphaned_total',
    'Jobs with pending state but no final event (ghost cards)',
    ['action', 'reason'],  # reason: worker_crash, sse_failure, timeout
    registry=registry
)

# Card state tracking (optimistic UI updates)
optimistic_cards_pending = Gauge(
    'wordflux_optimistic_cards_pending',
    'Number of cards currently in pending state',
    ['list_name'],  # Espera, Produção, Espera Aprovação, Agendado, Publicado
    registry=registry
)

optimistic_cards_pending_age_seconds = Histogram(
    'wordflux_optimistic_cards_pending_age_seconds',
    'Age of pending cards before reconciliation',
    ['list_name', 'action'],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, float('inf')],  # Up to 60s for slow workers
    registry=registry
)

# SSE emission tracking (atomicity validation)
sse_emissions_total = Counter(
    'wordflux_sse_emissions_total',
    'SSE events emitted',
    ['event_kind', 'pipeline_mode'],  # pipeline_mode: atomic, standalone
    registry=registry
)

sse_emission_failures = Counter(
    'wordflux_sse_emission_failures_total',
    'SSE emission failures (Redis errors)',
    ['event_kind', 'error_type'],  # error_type: connection_error, timeout, etc.
    registry=registry
)

# Pipeline execution (atomicity guarantees)
redis_pipeline_executions = Counter(
    'wordflux_redis_pipeline_executions_total',
    'Redis pipeline executions for atomic operations',
    ['operation_type', 'status'],  # operation_type: queue_publish, status: success/error
    registry=registry
)

redis_pipeline_size = Histogram(
    'wordflux_redis_pipeline_size_commands',
    'Number of commands in Redis pipeline',
    ['operation_type'],
    buckets=[1, 2, 3, 5, 10, 20, 50, float('inf')],
    registry=registry
)

# Reconciliation latency (SLI metric)
optimistic_reconciliation_latency_seconds = Histogram(
    'wordflux_optimistic_reconciliation_latency_seconds',
    'Time from job.queued to job.succeeded/failed (end-to-end latency)',
    ['action', 'outcome'],  # outcome: success, failed
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, float('inf')],
    registry=registry
)

# Race condition detection
optimistic_race_conditions = Counter(
    'wordflux_optimistic_race_conditions_total',
    'Detected race conditions in optimistic updates',
    ['race_type'],  # race_type: duplicate_job_id, out_of_order_events, etc.
    registry=registry
)

# Idempotency effectiveness
idempotency_deduplication = Counter(
    'wordflux_idempotency_deduplication_total',
    'Jobs deduplicated via deterministic IDs',
    ['action', 'dedup_stage'],  # dedup_stage: queue_publish, confirmation
    registry=registry
)

# Card operation metrics (board_operator level)
card_operations_completed = Counter(
    'wordflux_card_operations_completed_total',
    'Card operations completed (create/move/update)',
    ['operation', 'list_name', 'status'],  # operation: create/move/update, status: success/error
    registry=registry
)

# Clock skew detection (P0 fix: track Redis TIME() failures)
clock_skew_fallback = Counter(
    'wordflux_clock_skew_fallback_total',
    'Redis TIME() failures requiring fallback to local time',
    ['location'],  # location: create_card/move_card/update_card in execute_tool_call or execute_tool_call_with_id
    registry=registry
)

# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================


def record_optimistic_job_queued(action: str, session_id_present: bool) -> None:
    """
    Record optimistic job queued.

    Args:
        action: Action name (create_card, move_card, update_card)
        session_id_present: Whether session_id was provided (boolean to avoid cardinality explosion)
    """
    optimistic_jobs_queued.labels(
        action=action,
        session_id_present='true' if session_id_present else 'false'
    ).inc()


def record_optimistic_reconciliation(action: str, outcome: str, latency_seconds: float) -> None:
    """
    Record successful reconciliation (pending → final).

    Args:
        action: Action name (create_card, move_card, update_card)
        outcome: Reconciliation outcome (success, failed)
        latency_seconds: Time from job.queued to job.succeeded/failed
    """
    optimistic_jobs_reconciled.labels(action=action, outcome=outcome).inc()
    optimistic_reconciliation_latency_seconds.labels(action=action, outcome=outcome).observe(latency_seconds)


def record_optimistic_job_orphaned(action: str, reason: str) -> None:
    """
    Record orphaned job (ghost card never reconciled).

    Args:
        action: Action name (create_card, move_card, update_card)
        reason: Orphan reason (worker_crash, sse_failure, timeout)
    """
    optimistic_jobs_orphaned.labels(action=action, reason=reason).inc()


def record_optimistic_card_pending_start(list_name: str, action: str) -> None:
    """
    Record pending card creation (increment gauge).

    Args:
        list_name: Destination column (Espera, Produção, etc.)
        action: Action name (create_card, move_card)
    """
    optimistic_cards_pending.labels(list_name=list_name).inc()


def record_optimistic_card_pending_end(list_name: str, action: str) -> None:
    """
    Record pending card resolution (decrement gauge).

    Args:
        list_name: Destination column (Espera, Produção, etc.)
        action: Action name (create_card, move_card)
    """
    optimistic_cards_pending.labels(list_name=list_name).dec()


def record_pending_card_age(list_name: str, action: str, age_seconds: float) -> None:
    """
    Record pending card age before reconciliation.

    Args:
        list_name: Destination column
        action: Action name (create_card, move_card)
        age_seconds: Time from pending creation to reconciliation
    """
    optimistic_cards_pending_age_seconds.labels(list_name=list_name, action=action).observe(age_seconds)


def record_sse_emission(event_kind: str, pipeline_mode: str) -> None:
    """
    Record successful SSE emission.

    Args:
        event_kind: Event type (job.queued, card.pending, card.created, etc.)
        pipeline_mode: Emission mode (atomic, standalone)
    """
    sse_emissions_total.labels(event_kind=event_kind, pipeline_mode=pipeline_mode).inc()


def record_sse_emission_failure(event_kind: str, error_type: str) -> None:
    """
    Record SSE emission failure.

    Args:
        event_kind: Event type (job.queued, card.pending, etc.)
        error_type: Error class name (ConnectionError, TimeoutError, etc.)
    """
    sse_emission_failures.labels(event_kind=event_kind, error_type=error_type).inc()


def record_pipeline_execution(operation_type: str, status: str, pipeline_size: int) -> None:
    """
    Record Redis pipeline execution.

    Args:
        operation_type: Operation type (queue_publish_with_sse, sse_emission, etc.)
        status: Execution status (success, error)
        pipeline_size: Number of commands in pipeline
    """
    redis_pipeline_executions.labels(operation_type=operation_type, status=status).inc()
    redis_pipeline_size.labels(operation_type=operation_type).observe(pipeline_size)


def record_race_condition(race_type: str) -> None:
    """
    Record detected race condition.

    Args:
        race_type: Race condition type (duplicate_job_id, out_of_order_events, etc.)
    """
    optimistic_race_conditions.labels(race_type=race_type).inc()


def record_idempotency_dedup(action: str, dedup_stage: str) -> None:
    """
    Record idempotency deduplication.

    Args:
        action: Action name (create_card, move_card, update_card)
        dedup_stage: Deduplication stage (queue_publish, confirmation)
    """
    idempotency_deduplication.labels(action=action, dedup_stage=dedup_stage).inc()


def record_card_operation_completed(operation: str, list_name: str, success: bool, error_code: Optional[str] = None) -> None:
    """
    Record card operation completion.

    Args:
        operation: Operation type (create, move, update, comment)
        list_name: Target column
        success: Whether operation succeeded
        error_code: Error code if failed (wip_limit, card_not_found, etc.)
    """
    status = 'success' if success else 'error'
    card_operations_completed.labels(operation=operation, list_name=list_name, status=status).inc()


def record_clock_skew_fallback(location: str) -> None:
    """
    Record Redis TIME() failure requiring fallback to local time.

    Args:
        location: Location where fallback occurred (e.g., 'create_card_execute', 'move_card_with_id')
    """
    clock_skew_fallback.labels(location=location).inc()


def reconcile_pending_cards_gauge(redis_client) -> None:
    """
    Reconcile pending cards gauge with Redis reality (run periodically to fix gauge drift).

    This function queries Redis for actual pending card counts and resets the gauge.
    Should be called every 60 seconds to prevent gauge drift from worker crashes.

    Args:
        redis_client: Redis client instance
    """
    try:
        # Note: This requires implementing a Redis key pattern for pending cards
        # For now, this is a placeholder for future implementation
        # When implemented, it should:
        # 1. Scan Redis for wf:pending:* keys
        # 2. Count pending cards by list_name
        # 3. Set gauge to actual count
        logger.debug("Pending cards gauge reconciliation not yet implemented")
    except Exception as e:
        logger.error(f"Failed to reconcile pending cards gauge: {e}")


__all__ = [
    # Metric objects (for direct access if needed)
    "optimistic_jobs_queued",
    "optimistic_jobs_reconciled",
    "optimistic_jobs_orphaned",
    "optimistic_cards_pending",
    "optimistic_cards_pending_age_seconds",
    "sse_emissions_total",
    "sse_emission_failures",
    "redis_pipeline_executions",
    "redis_pipeline_size",
    "optimistic_reconciliation_latency_seconds",
    "optimistic_race_conditions",
    "idempotency_deduplication",
    "card_operations_completed",
    "clock_skew_fallback",
    # Convenience functions (recommended API)
    "record_optimistic_job_queued",
    "record_optimistic_reconciliation",
    "record_optimistic_job_orphaned",
    "record_optimistic_card_pending_start",
    "record_optimistic_card_pending_end",
    "record_pending_card_age",
    "record_sse_emission",
    "record_sse_emission_failure",
    "record_pipeline_execution",
    "record_race_condition",
    "record_idempotency_dedup",
    "record_card_operation_completed",
    "record_clock_skew_fallback",
    "reconcile_pending_cards_gauge",
]
