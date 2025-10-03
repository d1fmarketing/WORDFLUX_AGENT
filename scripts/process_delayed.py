#!/usr/bin/env python3
"""Process delayed jobs and move them back to the main queue when ready."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

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


def process_delayed_jobs(client, delayed_key: str, queue_key: str, batch_size: int = 10) -> int:
    """
    Process delayed jobs that are ready to be retried.

    Uses Redis sorted set to track jobs by their retry timestamp.
    """
    count = 0
    now = time.time()

    # Get jobs that are ready (score <= now)
    ready_jobs = client.zrangebyscore(
        delayed_key,
        '-inf',
        now,
        start=0,
        num=batch_size,
        withscores=True
    )

    for job_json, score in ready_jobs:
        try:
            # Parse the job
            job_data = json.loads(job_json)

            # Remove from delayed queue
            removed = client.zrem(delayed_key, job_json)
            if removed == 0:
                logger.warning(f"Job already removed from delayed queue: {job_data.get('job_id')}")
                continue

            # Add back to main queue
            client.rpush(queue_key, job_json)

            retry_count = job_data.get('metadata', {}).get('retry_count', 0)
            logger.info(f"Moved job {job_data.get('job_id')} from delayed to main queue (retry #{retry_count})")
            count += 1

        except Exception as e:
            logger.error(f"Failed to process delayed job: {e}")

    return count


def show_delayed_jobs(client, delayed_key: str, limit: int = 10) -> None:
    """Show jobs currently in the delayed queue."""
    now = time.time()

    # Get all delayed jobs with scores
    jobs = client.zrange(delayed_key, 0, limit - 1, withscores=True)

    if not jobs:
        print("No delayed jobs")
        return

    print(f"\n{'Job ID':<36} {'Agent':<20} {'Retry':<6} {'Ready In':<12}")
    print("-" * 80)

    for job_json, score in jobs:
        try:
            job_data = json.loads(job_json)
            job_id = job_data.get('job_id', 'UNKNOWN')[:36]
            agent = job_data.get('agent', '')[:20]
            retry_count = job_data.get('metadata', {}).get('retry_count', 0)

            # Calculate time until ready
            if score <= now:
                ready_in = "READY"
            else:
                seconds_left = int(score - now)
                if seconds_left < 60:
                    ready_in = f"{seconds_left}s"
                else:
                    minutes_left = seconds_left // 60
                    ready_in = f"{minutes_left}m {seconds_left % 60}s"

            print(f"{job_id:<36} {agent:<20} {retry_count:<6} {ready_in:<12}")

        except Exception as e:
            logger.error(f"Failed to parse delayed job: {e}")


def run_continuous(client, delayed_key: str, queue_key: str, interval: int = 5) -> None:
    """Continuously process delayed jobs."""
    logger.info(f"Starting delayed job processor (interval={interval}s)")

    while True:
        try:
            count = process_delayed_jobs(client, delayed_key, queue_key)
            if count > 0:
                logger.info(f"Processed {count} delayed job(s)")
        except KeyboardInterrupt:
            logger.info("Shutting down delayed job processor")
            break
        except Exception as e:
            logger.error(f"Error processing delayed jobs: {e}")

        time.sleep(interval)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Process delayed jobs for retry"
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Process once
    once_parser = subparsers.add_parser("once", help="Process delayed jobs once")
    once_parser.add_argument(
        "-n", "--batch-size",
        type=int,
        default=10,
        help="Number of jobs to process (default: 10)"
    )

    # Continuous processing
    continuous_parser = subparsers.add_parser("continuous", help="Continuously process delayed jobs")
    continuous_parser.add_argument(
        "-i", "--interval",
        type=int,
        default=5,
        help="Check interval in seconds (default: 5)"
    )

    # List delayed jobs
    list_parser = subparsers.add_parser("list", help="List delayed jobs")
    list_parser.add_argument(
        "-n", "--limit",
        type=int,
        default=20,
        help="Number of jobs to show (default: 20)"
    )

    # Count delayed jobs
    count_parser = subparsers.add_parser("count", help="Count delayed jobs")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Get Redis client
    client = get_redis_client()
    delayed_key = os.getenv("DELAYED_QUEUE_KEY", "wordflux:jobs:delayed")
    queue_key = os.getenv("REDIS_QUEUE_KEY", "wordflux:jobs")

    # Test connection
    try:
        client.ping()
    except Exception as e:
        logger.error(f"Cannot connect to Redis: {e}")
        sys.exit(1)

    if args.command == "once":
        count = process_delayed_jobs(client, delayed_key, queue_key, args.batch_size)
        print(f"Processed {count} delayed job(s)")

    elif args.command == "continuous":
        run_continuous(client, delayed_key, queue_key, args.interval)

    elif args.command == "list":
        show_delayed_jobs(client, delayed_key, args.limit)

    elif args.command == "count":
        count = client.zcard(delayed_key)
        now = time.time()
        ready_count = client.zcount(delayed_key, '-inf', now)
        print(f"Total delayed jobs: {count}")
        print(f"Ready for retry: {ready_count}")
        if count > ready_count:
            print(f"Waiting: {count - ready_count}")


if __name__ == "__main__":
    main()