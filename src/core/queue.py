"""Queue abstractions with pluggable implementations."""
from __future__ import annotations

import json
import logging
import math
import os
import queue
from abc import ABC, abstractmethod
from time import time
from typing import Any, Optional, Protocol
from uuid import uuid4

from src.core.job import Job

logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional dependency
    import redis
except ModuleNotFoundError:  # pragma: no cover - handled at runtime
    redis = None


class RedisLike(Protocol):
    """Minimal protocol for the Redis client used by :class:`RedisJobQueue`."""

    def rpush(self, key: str, value: str) -> Any:
        ...

    def lpop(self, key: str) -> str | None:
        ...

    def blpop(self, key: str, timeout: int | None = ...) -> tuple[str, str] | None:
        ...


class JobQueue(ABC):
    """Abstract job queue."""

    @abstractmethod
    def publish(self, job: Job) -> None:
        """Push a job onto the queue."""

    @abstractmethod
    def consume(self, timeout: float | None = None) -> Optional[Job]:
        """Pop a job, blocking up to timeout seconds."""

    @abstractmethod
    def task_done(self) -> None:
        """Signal that a consumed job has been handled."""


class MemoryJobQueue(JobQueue):
    """Process-local queue backed by :class:`queue.Queue`."""

    def __init__(self) -> None:
        self._queue: queue.Queue[Job] = queue.Queue()

    def publish(self, job: Job) -> None:
        self._queue.put(job)

    def consume(self, timeout: float | None = None) -> Optional[Job]:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def task_done(self) -> None:
        self._queue.task_done()


class RedisJobQueue(JobQueue):
    """Redis-backed queue that stores jobs as JSON blobs."""

    def __init__(self, client: RedisLike, key: str = "wordflux:jobs") -> None:
        if redis is None:  # pragma: no cover - guarded by import
            raise RuntimeError("redis package is not installed")
        self._client = client
        self._key = key

    def publish(self, job: Job) -> None:
        payload = json.dumps(job.as_dict())
        self._client.rpush(self._key, payload)

    def consume(self, timeout: float | None = None) -> Optional[Job]:
        if timeout is None:
            result = self._client.blpop(self._key)
        elif timeout <= 0:
            result = self._client.lpop(self._key)
            if result is None:
                return None
            payload = result
            return self._decode_job(payload)
        else:
            block_timeout = max(1, int(math.ceil(timeout)))
            result = self._client.blpop(self._key, timeout=block_timeout)

        if result is None:
            return None

        if isinstance(result, tuple):
            _, payload = result
        else:
            payload = result

        return self._decode_job(payload)

    def task_done(self) -> None:
        # Redis streams/lists acknowledge via pop, so nothing further is required.
        return None

    def _decode_job(self, payload: str) -> Job | None:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            logger.warning("redis_queue_decode_error", extra={"error": str(exc)})
            return None

        if not isinstance(data, dict):
            logger.warning("redis_queue_payload_not_mapping")
            return None

        agent = data.get("agent")
        if not agent:
            logger.warning("redis_queue_missing_agent")
            return None

        payload_data = data.get("payload")
        if payload_data is None:
            payload_data = {}

        return Job(
            agent=agent,
            payload=payload_data,
            job_id=data.get("job_id") or uuid4().hex,
            enqueued_at=data.get("enqueued_at") or time(),
        )


_default_queue: JobQueue | None = None


def _build_redis_client() -> RedisLike:
    if redis is None:  # pragma: no cover - guarded by import
        raise RuntimeError("redis package is required for QUEUE_MODE=redis")

    url = os.getenv("REDIS_URL")
    if url:
        return redis.Redis.from_url(url, decode_responses=True)

    host = os.getenv("REDIS_HOST", "127.0.0.1")
    port = int(os.getenv("REDIS_PORT", "6379"))
    db = int(os.getenv("REDIS_DB", "0"))
    password = os.getenv("REDIS_PASSWORD") or None
    return redis.Redis(host=host, port=port, db=db, password=password, decode_responses=True)


def load_default_queue() -> JobQueue:
    global _default_queue
    if _default_queue is not None:
        return _default_queue

    mode = os.getenv("QUEUE_MODE", "memory").lower()
    if mode == "memory":
        _default_queue = MemoryJobQueue()
        return _default_queue

    if mode == "redis":
        key = os.getenv("REDIS_QUEUE_KEY", "wordflux:jobs")
        client = _build_redis_client()
        _default_queue = RedisJobQueue(client=client, key=key)
        return _default_queue

    raise ValueError(f"Unsupported QUEUE_MODE '{mode}'. Available options: memory, redis.")


def set_default_queue(queue_impl: JobQueue) -> None:
    global _default_queue
    _default_queue = queue_impl


__all__ = [
    "JobQueue",
    "MemoryJobQueue",
    "RedisJobQueue",
    "load_default_queue",
    "set_default_queue",
]
