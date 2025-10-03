#!/usr/bin/env python3
"""Query tool for the WordFlux job ledger."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.ledger import get_ledger


def format_duration(seconds: float | None) -> str:
    """Format duration in human-readable form."""
    if seconds is None:
        return "N/A"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.1f}m"
    hours = minutes / 60
    return f"{hours:.1f}h"


def format_timestamp(timestamp: str | None) -> str:
    """Format timestamp for display."""
    if not timestamp:
        return "N/A"
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return timestamp


def print_job_table(jobs: list) -> None:
    """Print jobs in a table format."""
    if not jobs:
        print("No jobs found.")
        return

    # Print header
    print(f"{'Job ID':<36} {'Agent':<20} {'Status':<12} {'Created':<20} {'Duration':<10}")
    print("-" * 108)

    # Print jobs
    for job in jobs:
        job_id = job.get("job_id", "")[:36]
        agent = job.get("agent", "")[:20]
        status = job.get("status", "")[:12]
        created = format_timestamp(job.get("created_at"))
        duration = format_duration(job.get("duration_seconds"))

        # Add status indicator
        status_icon = {
            "completed": "✅",
            "failed": "❌",
            "processing": "⏳",
            "enqueued": "📋"
        }.get(status, "❓")

        print(f"{job_id:<36} {agent:<20} {status_icon} {status:<10} {created:<20} {duration:<10}")


def print_job_details(job: dict) -> None:
    """Print detailed job information."""
    print("\n" + "=" * 80)
    print(f"Job ID: {job.get('job_id')}")
    print(f"Agent: {job.get('agent')}")
    print(f"Status: {job.get('status')}")
    print(f"Created: {format_timestamp(job.get('created_at'))}")
    print(f"Started: {format_timestamp(job.get('started_at'))}")
    print(f"Ended: {format_timestamp(job.get('ended_at'))}")
    print(f"Duration: {format_duration(job.get('duration_seconds'))}")

    if job.get("error"):
        print(f"\n❌ Error: {job['error']}")

    if job.get("result"):
        try:
            result = json.loads(job["result"])
            print(f"\n✅ Result:")
            print(json.dumps(result, indent=2))
        except:
            print(f"\n✅ Result: {job['result']}")

    # Print events
    events = job.get("events", [])
    if events:
        print("\n📋 Events:")
        for event in events:
            timestamp = format_timestamp(event.get("created_at"))
            event_type = event.get("event_type")
            print(f"  {timestamp}: {event_type}")

    # Print artifacts
    artifacts = job.get("artifacts", [])
    if artifacts:
        print("\n📦 Artifacts:")
        for artifact in artifacts:
            artifact_type = artifact.get("artifact_type")
            url = artifact.get("artifact_url")
            size = artifact.get("file_size")
            size_str = f" ({size:,} bytes)" if size else ""
            print(f"  - {artifact_type}: {url}{size_str}")

    print("=" * 80 + "\n")


def print_stats(stats: dict) -> None:
    """Print job statistics."""
    print("\n📊 Job Statistics")
    print("=" * 40)

    # Status breakdown
    status_counts = stats.get("status_counts", {})
    total = stats.get("total_jobs", 0)

    print(f"Total Jobs: {total}")
    print("\nStatus Breakdown:")
    for status, count in status_counts.items():
        percentage = (count / total * 100) if total > 0 else 0
        status_icon = {
            "completed": "✅",
            "failed": "❌",
            "processing": "⏳",
            "enqueued": "📋"
        }.get(status, "❓")
        print(f"  {status_icon} {status}: {count} ({percentage:.1f}%)")

    # Duration stats
    duration_stats = stats.get("duration_stats", {})
    if duration_stats.get("avg_duration"):
        print("\nDuration Statistics:")
        print(f"  Average: {format_duration(duration_stats['avg_duration'])}")
        print(f"  Min: {format_duration(duration_stats['min_duration'])}")
        print(f"  Max: {format_duration(duration_stats['max_duration'])}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Query WordFlux job ledger")

    # Commands
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Recent jobs
    recent_parser = subparsers.add_parser("recent", help="Show recent jobs")
    recent_parser.add_argument(
        "-n", "--limit",
        type=int,
        default=10,
        help="Number of jobs to show (default: 10)"
    )

    # Job details
    details_parser = subparsers.add_parser("details", help="Show job details")
    details_parser.add_argument("job_id", help="Job ID to inspect")

    # Statistics
    stats_parser = subparsers.add_parser("stats", help="Show job statistics")
    stats_parser.add_argument(
        "-a", "--agent",
        help="Filter by agent name"
    )
    stats_parser.add_argument(
        "-d", "--days",
        type=int,
        default=7,
        help="Stats for last N days (default: 7)"
    )

    # SQL query
    sql_parser = subparsers.add_parser("sql", help="Run custom SQL query")
    sql_parser.add_argument("query", help="SQL query to execute")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        ledger = get_ledger()

        if args.command == "recent":
            jobs = ledger.get_recent_jobs(limit=args.limit)
            print_job_table(jobs)

        elif args.command == "details":
            job = ledger.get_job_details(args.job_id)
            if job:
                print_job_details(job)
            else:
                print(f"Job {args.job_id} not found.")
                sys.exit(1)

        elif args.command == "stats":
            since = None
            if args.days:
                since = datetime.now(timezone.utc) - timedelta(days=args.days)

            stats = ledger.get_job_stats(agent=args.agent, since=since)
            print_stats(stats)

        elif args.command == "sql":
            # Direct SQL query (careful!)
            import sqlite3
            conn = sqlite3.connect(ledger.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            try:
                cursor.execute(args.query)
                results = cursor.fetchall()

                if results:
                    # Print column names
                    columns = results[0].keys()
                    print("\t".join(columns))
                    print("-" * (len(columns) * 15))

                    # Print rows
                    for row in results:
                        values = [str(row[col]) if row[col] is not None else "NULL" for col in columns]
                        print("\t".join(values))
                else:
                    print("No results.")

            finally:
                conn.close()

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()