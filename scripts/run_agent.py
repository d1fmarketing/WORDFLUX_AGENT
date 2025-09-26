"""Command-line entrypoint for executing registered agents."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List

# Importing agents ensures registration side effects execute.
import src.agents  # noqa: F401
from src.core.job import build_job
from src.core.queue import load_default_queue
from src.core.registry import available_agents, create_agent

Payload = Dict[str, Any]


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a WordFlux agent with a JSON payload.")
    parser.add_argument("--agent", required=True, help="Agent identifier to execute.")
    parser.add_argument(
        "--payload",
        help="JSON string payload. Overrides --message if provided.",
    )
    parser.add_argument(
        "--message",
        default="",
        help="Convenience flag to supply a simple message payload.",
    )
    parser.add_argument(
        "--enqueue",
        action="store_true",
        help="Enqueue the job onto the worker queue instead of running immediately.",
    )
    return parser.parse_args(argv)


def build_payload(args: argparse.Namespace) -> Payload:
    if args.payload:
        try:
            data = json.loads(args.payload)
            if not isinstance(data, dict):
                raise ValueError("Payload JSON must represent an object.")
            return data
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON payload: {exc.msg}") from exc
    return {"message": args.message}


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    payload = build_payload(args)

    agent_name = args.agent
    if agent_name not in available_agents():
        avail = ", ".join(available_agents()) or "<none>"
        print(f"Unknown agent '{agent_name}'. Available: {avail}.", file=sys.stderr)
        return 1

    if args.enqueue:
        queue = load_default_queue()
        job = build_job(agent=agent_name, payload=payload)
        queue.publish(job)
        print(json.dumps({"status": "enqueued", "job_id": job.job_id, "agent": job.agent}, indent=2))
    else:
        agent = create_agent(agent_name)
        result = agent.run(payload)
        print(json.dumps(result, indent=2))

    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via CLI
    raise SystemExit(main())
