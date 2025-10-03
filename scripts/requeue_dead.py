#!/usr/bin/env python3
"""Requeue jobs from the dead letter queue back to the main queue."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_redis_client():
    """Get Redis client."""
    try:
        import redis
        url = os.getenv("REDIS_URL")
        if url:
            return redis.Redis.from_url(url, decode_responses=True)
        else:
            host = os.getenv("REDIS_HOST", "127.0.0.1")
            port = int(os.getenv("REDIS_PORT", "6379"))
            db = int(os.getenv("REDIS_DB", "0"))
            password = os.getenv("REDIS_PASSWORD") or None
            return redis.Redis(host=host, port=port, db=db, password=password, decode_responses=True)
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        sys.exit(1)


def list_dead_jobs(client, dlq_key: str, limit: int = 10) -> list:
    """List jobs in the DLQ."""
    jobs = []
    items = client.lrange(dlq_key, 0, limit - 1)

    for item in items:
        try:
            job = json.loads(item)
            jobs.append(job)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in DLQ: {item[:100]}")

    return jobs


def requeue_job(client, dlq_key: str, queue_key: str, job_data: dict) -> bool:
    """Requeue a single job from DLQ to main queue."""
    try:
        # Recreate the original job format
        from src.core.job import Job

        # Create new job with original payload
        job = Job(
            agent=job_data.get("agent"),
            payload=job_data.get("payload", {}),
            job_id=job_data.get("job_id")  # Keep original ID for tracking
        )

        # Serialize for queue
        job_json = json.dumps(job.as_dict())

        # Remove from DLQ
        removed = client.lrem(dlq_key, 1, json.dumps(job_data))
        if removed == 0:
            logger.warning(f"Job not found in DLQ: {job_data.get('job_id')}")
            return False

        # Add back to main queue
        client.rpush(queue_key, job_json)

        logger.info(f"Requeued job {job.job_id} from DLQ to main queue")
        return True

    except Exception as e:
        logger.error(f"Failed to requeue job: {e}")
        return False


def requeue_all(client, dlq_key: str, queue_key: str) -> int:
    """Requeue all jobs from DLQ to main queue."""
    count = 0

    while True:
        # Get first item from DLQ
        item = client.lpop(dlq_key)
        if not item:
            break

        try:
            job_data = json.loads(item)
            from src.core.job import Job

            # Create new job with original payload
            job = Job(
                agent=job_data.get("agent"),
                payload=job_data.get("payload", {}),
                job_id=job_data.get("job_id")
            )

            # Add to main queue
            client.rpush(queue_key, json.dumps(job.as_dict()))
            count += 1

            logger.info(f"Requeued job {job.job_id}")

        except Exception as e:
            logger.error(f"Failed to requeue job: {e}")
            # Put it back at the end of DLQ
            client.rpush(dlq_key, item)

    return count


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Requeue jobs from dead letter queue"
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # List command
    list_parser = subparsers.add_parser("list", help="List jobs in DLQ")
    list_parser.add_argument(
        "-n", "--limit",
        type=int,
        default=10,
        help="Number of jobs to show (default: 10)"
    )

    # Requeue one command
    one_parser = subparsers.add_parser("one", help="Requeue specific job")
    one_parser.add_argument("job_id", help="Job ID to requeue")

    # Requeue all command
    all_parser = subparsers.add_parser("all", help="Requeue all jobs from DLQ")
    all_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm requeuing all jobs"
    )

    # Count command
    count_parser = subparsers.add_parser("count", help="Count jobs in DLQ")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Get Redis client
    client = get_redis_client()
    dlq_key = os.getenv("DLQ_KEY", "wordflux:jobs:dead")
    queue_key = os.getenv("REDIS_QUEUE_KEY", "wordflux:jobs")

    # Test connection
    try:
        client.ping()
    except Exception as e:
        logger.error(f"Cannot connect to Redis: {e}")
        sys.exit(1)

    if args.command == "list":
        jobs = list_dead_jobs(client, dlq_key, args.limit)

        if not jobs:
            print("No jobs in DLQ")
            return

        print(f"\nFound {len(jobs)} job(s) in DLQ:\n")
        print(f"{'Job ID':<36} {'Agent':<20} {'Error':<30} {'Retries':<8}")
        print("-" * 100)

        for job in jobs:
            job_id = job.get("job_id", "UNKNOWN")[:36]
            agent = job.get("agent", "")[:20]
            error = job.get("error_type", "Unknown")[:30]
            retries = job.get("retry_count", 0)

            print(f"{job_id:<36} {agent:<20} {error:<30} {retries:<8}")

    elif args.command == "one":
        # Find the specific job
        jobs = list_dead_jobs(client, dlq_key, 1000)  # Search more jobs
        job_to_requeue = None

        for job in jobs:
            if job.get("job_id") == args.job_id:
                job_to_requeue = job
                break

        if not job_to_requeue:
            logger.error(f"Job {args.job_id} not found in DLQ")
            sys.exit(1)

        if requeue_job(client, dlq_key, queue_key, job_to_requeue):
            print(f"✅ Successfully requeued job {args.job_id}")
        else:
            print(f"❌ Failed to requeue job {args.job_id}")
            sys.exit(1)

    elif args.command == "all":
        dlq_size = client.llen(dlq_key)

        if dlq_size == 0:
            print("No jobs in DLQ")
            return

        if not args.confirm:
            print(f"⚠️  This will requeue {dlq_size} job(s) from DLQ to main queue")
            print("Run with --confirm to proceed")
            sys.exit(1)

        count = requeue_all(client, dlq_key, queue_key)
        print(f"✅ Requeued {count} job(s) from DLQ to main queue")

    elif args.command == "count":
        count = client.llen(dlq_key)
        print(f"Jobs in DLQ: {count}")


if __name__ == "__main__":
    main()