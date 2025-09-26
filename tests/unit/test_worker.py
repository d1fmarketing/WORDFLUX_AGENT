from __future__ import annotations

from typing import Any, Dict, List, Tuple

import src.agents  # noqa: F401
from src.core.job import build_job
from src.core.queue import MemoryJobQueue
from src.core.registry import register_agent, unregister_agent
from src.core.worker import Worker


class TrackingAgent:
    def __init__(self, response: Dict[str, Any]) -> None:
        self.response = response

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(self.response)
        out["payload"] = payload
        return out


def test_worker_processes_job_and_invokes_handler() -> None:
    queue = MemoryJobQueue()
    results: List[Tuple[str, Dict[str, Any]]] = []

    def factory() -> TrackingAgent:
        return TrackingAgent({"status": "ok"})

    register_agent("tracking", factory)
    try:
        job = build_job(agent="tracking", payload={"key": "value"})
        queue.publish(job)
        worker = Worker(queue=queue, result_handler=lambda j, res: results.append((j.job_id, res)))

        processed = worker.run_once(timeout=0.1)

        assert processed is True
        assert results[0][0] == job.job_id
        assert results[0][1]["payload"] == {"key": "value"}
    finally:
        unregister_agent("tracking")


def test_worker_calls_error_handler_on_exception() -> None:
    queue = MemoryJobQueue()
    errors: List[str] = []

    class FailingAgent:
        def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:  # pragma: no cover - raised in test
            raise RuntimeError("boom")

    register_agent("failing", FailingAgent)
    try:
        job = build_job(agent="failing", payload={})
        queue.publish(job)
        worker = Worker(queue=queue, error_handler=lambda j, exc: errors.append(j.job_id))

        worker.run_once(timeout=0.1)

        assert errors == [job.job_id]
    finally:
        unregister_agent("failing")
