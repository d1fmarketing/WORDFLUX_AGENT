"""Worker loop that executes registered agents."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, Dict, Optional

import redis
import src.agents  # noqa: F401  # ensure agent registration side effects
from src.core.job import Job
from src.core.queue import JobQueue, load_default_queue
from src.core.registry import create_agent
from src.core.events import emit_job_started, emit_job_succeeded, emit_job_failed

# Import optimistic update metrics (conditional, depends on prometheus_client)
try:
    from src.core.metrics_optimistic import (
        record_optimistic_reconciliation,
        record_optimistic_card_pending_start,
        record_optimistic_card_pending_end,
        record_pending_card_age,
    )
    OPTIMISTIC_METRICS_ENABLED = True
except ImportError:
    OPTIMISTIC_METRICS_ENABLED = False
    # Define no-op functions if metrics unavailable
    def record_optimistic_reconciliation(*args, **kwargs): pass
    def record_optimistic_card_pending_start(*args, **kwargs): pass
    def record_optimistic_card_pending_end(*args, **kwargs): pass
    def record_pending_card_age(*args, **kwargs): pass

logger = logging.getLogger(__name__)

if not OPTIMISTIC_METRICS_ENABLED:
    logger.info("Optimistic update metrics disabled - prometheus_client not installed")

# Optional metrics support
try:
    from src.core.metrics import (
        record_job_processed,
        record_worker_error,
        start_metrics_server,
        update_queue_metrics,
        get_redis_queue_metrics
    )
    METRICS_ENABLED = True
except ImportError:
    METRICS_ENABLED = False
    logger.info("Metrics disabled - prometheus_client not installed")

# Optional ledger support
try:
    from src.core.ledger import get_ledger
    LEDGER_ENABLED = True
    ledger = get_ledger()
except Exception as e:
    LEDGER_ENABLED = False
    ledger = None
    logger.info(f"Ledger disabled: {e}")

ResultHandler = Callable[[Job, dict], None]
ErrorHandler = Callable[[Job, Exception], None]


def extract_card_id(job: Job) -> Optional[str]:
    """Extract card_id from job payload if present.

    Args:
        job: Job instance to extract card_id from

    Returns:
        card_id string if present in payload, None otherwise
    """
    if isinstance(job.payload, dict):
        return job.payload.get("card_id")
    return None


class Worker:
    """Continuously consumes jobs and dispatches them to agents."""

    def __init__(
        self,
        queue: JobQueue | None = None,
        result_handler: Optional[ResultHandler] = None,
        error_handler: Optional[ErrorHandler] = None,
        max_retries: int = 3,
    ) -> None:
        self.queue = queue or load_default_queue()
        self.result_handler = result_handler
        self.error_handler = error_handler
        self.max_retries = max_retries
        self.retry_counts = {}  # Track retries per job

    def run_once(self, timeout: float | None = 1.0) -> bool:
        # Update queue metrics if enabled
        if METRICS_ENABLED:
            metrics = get_redis_queue_metrics()
            update_queue_metrics(metrics['queue_size'], metrics['processing_size'])

        dequeued = self.queue.consume(timeout=timeout)
        if dequeued is None:
            return False
        job = dequeued.job
        start_time = time.time()
        success = False

        # Record job started in ledger
        if LEDGER_ENABLED and ledger:
            try:
                ledger.record_job_started(job.job_id)
            except Exception as e:
                logger.warning(f"Failed to record job start in ledger: {e}")

        # Emit job started event
        card_id = extract_card_id(job)
        emit_job_started(job.job_id, job.agent, card_id)

        # Track pending card creation for optimistic updates (if applicable)
        pending_start_time = time.time()

        # Extract queued_at with proper validation (P0 fix: prevent AttributeError)
        job_queued_at = None
        if hasattr(job, 'metadata') and job.metadata and 'queued_at' in job.metadata:
            job_queued_at = job.metadata['queued_at']
        elif hasattr(job, 'enqueued_at') and job.enqueued_at:
            job_queued_at = job.enqueued_at
        else:
            # Last resort fallback: use current time (latency metric will be near-zero)
            job_queued_at = time.time()
            logger.warning(f"Job {job.job_id} missing queued_at metadata, using current time for metrics")

        action = job.payload.get('action') if isinstance(job.payload, dict) else None
        from_chat = job.payload.get('from_chat', False) if isinstance(job.payload, dict) else False

        # P0 fix: Only track pending gauge for chat-originated optimistic updates
        if action in ('create_card', 'move_card') and from_chat:
            # Extract list_name from payload
            list_name = (
                job.payload.get('column') or  # create_card uses 'column'
                job.payload.get('to_column') or  # move_card uses 'to_column'
                'unknown'
            )
            # Record pending card start (increment gauge) - P0 fix: skip if list_name unknown
            if list_name != 'unknown':
                record_optimistic_card_pending_start(
                    list_name=list_name,
                    action=action
                )
            else:
                logger.warning(f"Job {job.job_id} has unknown list_name, skipping pending gauge metrics")

        try:
            agent = create_agent(job.agent)
            result = agent.run(job.payload)
            if self.result_handler is not None:
                self.result_handler(job, result)

            # Record successful completion in ledger
            duration = time.time() - start_time
            if LEDGER_ENABLED and ledger:
                try:
                    ledger.record_job_completed(job.job_id, result, duration)
                except Exception as e:
                    logger.warning(f"Failed to record job completion in ledger: {e}")

            # Acknowledge job after successful processing
            dequeued.ack()
            success = True

            # Emit job succeeded event
            emit_job_succeeded(job.job_id, job.agent, card_id, data={"result": result})

            # Record successful reconciliation for optimistic updates
            if action in ('create_card', 'move_card', 'update_card'):
                # Calculate reconciliation latency (queued → succeeded)
                reconciliation_latency = time.time() - job_queued_at

                # Record successful reconciliation
                record_optimistic_reconciliation(
                    action=action,
                    outcome='success',
                    latency_seconds=reconciliation_latency
                )

                # Record pending card age and decrement gauge (for create_card and move_card)
                # P0 fix: Only decrement for chat-originated jobs (matches increment logic)
                if action in ('create_card', 'move_card') and from_chat:
                    # P0 fix: skip if list_name='unknown' (gauge was never incremented)
                    if list_name != 'unknown':
                        # Calculate pending age (started → reconciled)
                        pending_age = time.time() - pending_start_time

                        record_pending_card_age(
                            list_name=list_name,
                            action=action,
                            age_seconds=pending_age
                        )

                        # Decrement pending cards gauge
                        record_optimistic_card_pending_end(
                            list_name=list_name,
                            action=action
                        )
        except Exception as exc:  # noqa: BLE001 - propagate to handler
            logger.exception("worker_error", extra={"job_id": job.job_id, "agent": job.agent})

            # Track retry count
            retry_count = self.retry_counts.get(job.job_id, 0) + 1
            self.retry_counts[job.job_id] = retry_count

            # Record failure in ledger
            duration = time.time() - start_time
            if LEDGER_ENABLED and ledger:
                try:
                    ledger.record_job_failed(job.job_id, str(exc), duration)
                except Exception as e:
                    logger.warning(f"Failed to record job failure in ledger: {e}")

            # Emit job failed event with error code/hint if available
            error_message = str(exc)
            error_code = getattr(exc, 'code', None)
            error_hint = getattr(exc, 'hint', None)

            emit_job_failed(
                job.job_id,
                job.agent,
                error_message,
                card_id=card_id,
                code=error_code,
                hint=error_hint
            )

            # Record failed reconciliation for optimistic updates
            if action in ('create_card', 'move_card', 'update_card'):
                # Calculate reconciliation latency (queued → failed)
                reconciliation_latency = time.time() - job_queued_at

                # Record failed reconciliation
                record_optimistic_reconciliation(
                    action=action,
                    outcome='failed',
                    latency_seconds=reconciliation_latency
                )

                # Decrement pending cards gauge (failed reconciliation still resolves pending state)
                # P0 fix: Only decrement for chat-originated jobs (matches increment logic)
                if action in ('create_card', 'move_card') and from_chat:
                    # P0 fix: skip if list_name='unknown' (gauge was never incremented)
                    if list_name != 'unknown':
                        record_optimistic_card_pending_end(
                            list_name=list_name,
                            action=action
                        )

            # Check if we should send to DLQ
            if retry_count >= self.max_retries:
                self._send_to_dlq(job, exc, retry_count)
                # Clean up retry count
                del self.retry_counts[job.job_id]
            else:
                # Re-queue for retry with exponential backoff
                logger.warning(f"Job {job.job_id} failed (attempt {retry_count}/{self.max_retries}), will retry")
                self._requeue_with_backoff(job, retry_count)

            if self.error_handler is not None:
                self.error_handler(job, exc)
            if METRICS_ENABLED:
                record_worker_error(job.agent)
            # Always ack to remove from processing list, even on error
            dequeued.ack()
        finally:
            duration = time.time() - start_time
            if METRICS_ENABLED:
                record_job_processed(job.agent, success, duration)
            self.queue.task_done()
        return True

    def _requeue_with_backoff(self, job: Job, retry_count: int) -> None:
        """Re-queue job with exponential backoff."""
        try:
            import json
            import os

            # Calculate backoff delay: 1s, 2s, 4s, 8s, 16s, 32s (max 5 minutes)
            delay_seconds = min(2 ** (retry_count - 1), 300)

            # Update job metadata
            job.metadata['retry_count'] = retry_count
            job.metadata['next_retry_at'] = time.time() + delay_seconds
            job.metadata['last_error_at'] = time.time()

            # Get Redis client for delayed queue
            import redis
            redis_url = os.getenv("REDIS_URL")
            if redis_url:
                client = redis.Redis.from_url(redis_url, decode_responses=True)
            else:
                host = os.getenv("REDIS_HOST", "127.0.0.1")
                port = int(os.getenv("REDIS_PORT", "6379"))
                client = redis.Redis(host=host, port=port, decode_responses=True)

            # Push to delayed queue with score as timestamp when it should be processed
            delayed_key = os.getenv("DELAYED_QUEUE_KEY", "wordflux:jobs:delayed")
            score = time.time() + delay_seconds

            # Use ZADD to add with score (timestamp)
            client.zadd(delayed_key, {json.dumps(job.as_dict()): score})

            logger.info(f"Job {job.job_id} scheduled for retry in {delay_seconds}s (attempt {retry_count}/{self.max_retries})")

        except Exception as e:
            logger.error(f"Failed to requeue job {job.job_id} with backoff: {e}")
            # If requeue fails, send to DLQ immediately
            self._send_to_dlq(job, e, retry_count)

    def _send_to_dlq(self, job: Job, error: Exception, retry_count: int) -> None:
        """Send failed job to dead letter queue."""
        try:
            # Try to use Redis if available
            import redis
            import json
            import os

            redis_url = os.getenv("REDIS_URL")
            if redis_url:
                client = redis.Redis.from_url(redis_url, decode_responses=True)
            else:
                host = os.getenv("REDIS_HOST", "127.0.0.1")
                port = int(os.getenv("REDIS_PORT", "6379"))
                client = redis.Redis(host=host, port=port, decode_responses=True)

            # Create DLQ payload with error info
            dlq_payload = {
                "job_id": job.job_id,
                "agent": job.agent,
                "payload": job.payload,
                "error": str(error),
                "error_type": error.__class__.__name__,
                "retry_count": retry_count,
                "failed_at": time.time(),
                "enqueued_at": job.enqueued_at
            }

            # Push to DLQ
            dlq_key = os.getenv("DLQ_KEY", "wordflux:jobs:dead")
            client.rpush(dlq_key, json.dumps(dlq_payload))

            logger.error(f"Job {job.job_id} sent to DLQ after {retry_count} retries")

        except Exception as e:
            logger.error(f"Failed to send job {job.job_id} to DLQ: {e}")

    def run_forever(self, poll_interval: float = 0.5) -> None:
        logger.info("worker_started", extra={"poll_interval": poll_interval})
        while True:
            processed = self.run_once(timeout=poll_interval)
            if not processed:
                time.sleep(poll_interval)


__all__ = ["Worker"]
