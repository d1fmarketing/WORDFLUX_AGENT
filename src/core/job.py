"""Job definitions for the WordFlux worker."""
from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any, Dict
from uuid import uuid4

Payload = Dict[str, Any]


@dataclass(slots=True)
class Job:
    """Unit of work processed by the worker loop."""

    agent: str
    payload: Payload
    job_id: str = field(default_factory=lambda: uuid4().hex)
    enqueued_at: float = field(default_factory=time)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "agent": self.agent,
            "payload": self.payload,
            "enqueued_at": self.enqueued_at,
        }


def build_job(agent: str, payload: Payload, job_id: str | None = None) -> Job:
    return Job(agent=agent, payload=payload, job_id=job_id or uuid4().hex)


__all__ = ["Job", "Payload", "build_job"]
