"""Worker loop that executes registered agents."""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import src.agents  # noqa: F401  # ensure agent registration side effects
from src.core.job import Job
from src.core.queue import JobQueue, load_default_queue
from src.core.registry import create_agent

logger = logging.getLogger(__name__)

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

ResultHandler = Callable[[Job, dict], None]
ErrorHandler = Callable[[Job, Exception], None]


class Worker:
    """Continuously consumes jobs and dispatches them to agents."""

    def __init__(
        self,
        queue: JobQueue | None = None,
        result_handler: Optional[ResultHandler] = None,
        error_handler: Optional[ErrorHandler] = None,
    ) -> None:
        self.queue = queue or load_default_queue()
        self.result_handler = result_handler
        self.error_handler = error_handler

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
        try:
            agent = create_agent(job.agent)
            result = agent.run(job.payload)
            if self.result_handler is not None:
                self.result_handler(job, result)
            # Acknowledge job after successful processing
            dequeued.ack()
            success = True
        except Exception as exc:  # noqa: BLE001 - propagate to handler
            logger.exception("worker_error", extra={"job_id": job.job_id, "agent": job.agent})
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

    def run_forever(self, poll_interval: float = 0.5) -> None:
        logger.info("worker_started", extra={"poll_interval": poll_interval})
        while True:
            processed = self.run_once(timeout=poll_interval)
            if not processed:
                time.sleep(poll_interval)


__all__ = ["Worker"]
