#!/usr/bin/env python3
"""Requeue stuck jobs from processing list back to main queue."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from time import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

try:
    import redis
except ImportError:
    logger.error("redis package not installed. Run: pip install redis")
    sys.exit(1)


def build_redis_client():
    """Build Redis client from environment configuration."""
    url = os.getenv("REDIS_URL")
    if url:
        return redis.Redis.from_url(url, decode_responses=True)

    host = os.getenv("REDIS_HOST", "127.0.0.1")
    port = int(os.getenv("REDIS_PORT", "6379"))
    db = int(os.getenv("REDIS_DB", "0"))
    password = os.getenv("REDIS_PASSWORD") or None
    return redis.Redis(host=host, port=port, db=db, password=password, decode_responses=True)


def requeue_processing_jobs(client, main_key="wordflux:jobs", processing_key="wordflux:jobs:processing",
                            max_age_seconds=300, dry_run=False):
    """
    Move jobs from processing list back to main queue.

    Args:
        client: Redis client
        main_key: Main job queue key
        processing_key: Processing list key
        max_age_seconds: Only requeue jobs older than this (default 5 minutes)
        dry_run: If True, only show what would be requeued

    Returns:
        Number of jobs requeued
    """
    # Get all jobs in processing list
    processing_jobs = client.lrange(processing_key, 0, -1)

    if not processing_jobs:
        logger.info("No jobs in processing list")
        return 0

    logger.info(f"Found {len(processing_jobs)} jobs in processing list")

    requeued_count = 0
    current_time = time()

    for job_data in processing_jobs:
        try:
            job = json.loads(job_data)
            enqueued_at = job.get("enqueued_at", 0)
            age_seconds = current_time - enqueued_at

            # Check if job is old enough to requeue
            if age_seconds < max_age_seconds:
                logger.debug(f"Job {job.get('job_id', 'unknown')} age {age_seconds:.1f}s < {max_age_seconds}s, skipping")
                continue

            agent = job.get("agent", "unknown")
            job_id = job.get("job_id", "unknown")

            if dry_run:
                logger.info(f"[DRY RUN] Would requeue job {job_id} (agent={agent}, age={age_seconds:.1f}s)")
            else:
                # Move from processing back to main queue
                # Use transaction to ensure atomicity
                with client.pipeline() as pipe:
                    pipe.lrem(processing_key, 1, job_data)
                    pipe.rpush(main_key, job_data)
                    pipe.execute()
                logger.info(f"Requeued job {job_id} (agent={agent}, age={age_seconds:.1f}s)")

            requeued_count += 1

        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in processing list, removing: {job_data[:100]}")
            if not dry_run:
                client.lrem(processing_key, 1, job_data)
        except Exception as e:
            logger.error(f"Error processing job: {e}")

    return requeued_count


def main():
    """Main entry point for requeue script."""
    parser = argparse.ArgumentParser(description="Requeue stuck jobs from processing list")
    parser.add_argument("--main-key", default="wordflux:jobs",
                        help="Main job queue key (default: wordflux:jobs)")
    parser.add_argument("--processing-key", default="wordflux:jobs:processing",
                        help="Processing list key (default: wordflux:jobs:processing)")
    parser.add_argument("--max-age", type=int, default=300,
                        help="Only requeue jobs older than this many seconds (default: 300)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be requeued without actually doing it")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable verbose logging")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        client = build_redis_client()
        # Test connection
        client.ping()
        logger.info("Connected to Redis")
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        sys.exit(1)

    count = requeue_processing_jobs(
        client,
        main_key=args.main_key,
        processing_key=args.processing_key,
        max_age_seconds=args.max_age,
        dry_run=args.dry_run
    )

    if args.dry_run:
        logger.info(f"[DRY RUN] Would have requeued {count} jobs")
    else:
        logger.info(f"Requeued {count} jobs")

    return 0


if __name__ == "__main__":
    sys.exit(main())