"""CLI entrypoint for running the worker loop."""
from __future__ import annotations

import argparse
import logging
from typing import List

import src.agents  # noqa: F401
from src.core.queue import load_default_queue
from src.core.worker import Worker


logger = logging.getLogger(__name__)


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the WordFlux worker loop.")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.5,
        help="Polling interval (seconds) when the queue is idle.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process a single job and exit.",
    )
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    # Start metrics server if available
    try:
        from src.core.metrics import start_metrics_server
        import os
        metrics_port = int(os.getenv("METRICS_PORT", "9300"))
        start_metrics_server(metrics_port)
        logger.info(f"Metrics server started on port {metrics_port}")
    except ImportError:
        logger.info("Metrics disabled - prometheus_client not installed")
    except Exception as e:
        logger.warning(f"Metrics server already running or failed to start: {e}")

    queue = load_default_queue()
    logger.info("worker_queue_selected", extra={"queue_class": queue.__class__.__name__})
    worker = Worker(queue=queue)

    if args.once:
        worker.run_once(timeout=args.poll_interval)
    else:
        try:
            worker.run_forever(poll_interval=args.poll_interval)
        except KeyboardInterrupt:  # pragma: no cover - interactive usage
            pass
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
