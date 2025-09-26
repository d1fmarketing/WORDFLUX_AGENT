from __future__ import annotations

import pytest

from src.core.job import build_job
from src.core.queue import MemoryJobQueue, RedisJobQueue, load_default_queue


class _FakeRedisClient:
    def __init__(self) -> None:
        self.items: list[str] = []

    def rpush(self, key: str, value: str) -> None:
        self.items.append(value)

    def lpop(self, key: str) -> str | None:
        if not self.items:
            return None
        return self.items.pop(0)

    def blpop(self, key: str, timeout: int | None = None) -> tuple[str, str] | None:
        if not self.items:
            return None
        return key, self.items.pop(0)


def test_memory_queue_publish_and_consume() -> None:
    queue = MemoryJobQueue()
    job = build_job(agent="echo", payload={"message": "hi"})

    queue.publish(job)
    retrieved = queue.consume(timeout=0.1)

    assert retrieved is job
    queue.task_done()


def test_redis_queue_publish_and_consume_via_lpop() -> None:
    queue = RedisJobQueue(client=_FakeRedisClient(), key="test-queue")
    job = build_job(agent="echo", payload={"message": "hello"})

    queue.publish(job)
    retrieved = queue.consume(timeout=0)

    assert retrieved is not None
    assert retrieved.agent == job.agent
    assert retrieved.payload == job.payload


def test_redis_queue_publish_and_consume_via_blpop() -> None:
    fake_client = _FakeRedisClient()
    queue = RedisJobQueue(client=fake_client, key="test-queue")
    job = build_job(agent="echo", payload={"message": "hello"})

    queue.publish(job)
    retrieved = queue.consume(timeout=2.4)

    assert retrieved is not None
    assert retrieved.agent == job.agent


def test_redis_queue_consume_empty_returns_none() -> None:
    queue = RedisJobQueue(client=_FakeRedisClient(), key="test-queue")

    assert queue.consume(timeout=0) is None


def test_redis_queue_invalid_payload_returns_none() -> None:
    fake_client = _FakeRedisClient()
    fake_client.items.append("not-json")
    queue = RedisJobQueue(client=fake_client, key="test-queue")

    assert queue.consume(timeout=0) is None


def test_load_default_queue_uses_redis(monkeypatch) -> None:
    from src.core import queue as queue_module

    monkeypatch.setenv("QUEUE_MODE", "redis")
    monkeypatch.setenv("REDIS_QUEUE_KEY", "wordflux:test")
    fake_client = _FakeRedisClient()
    monkeypatch.setattr(queue_module, "_build_redis_client", lambda: fake_client)
    monkeypatch.setattr(queue_module, "_default_queue", None, raising=False)

    loaded = load_default_queue()

    assert isinstance(loaded, RedisJobQueue)


def test_load_default_queue_returns_cached_instance(monkeypatch) -> None:
    from src.core import queue as queue_module

    monkeypatch.delenv("QUEUE_MODE", raising=False)
    monkeypatch.setattr(queue_module, "_default_queue", None, raising=False)

    first = load_default_queue()

    def _unexpected() -> None:
        pytest.fail("_build_redis_client should not be called once queue is cached")

    monkeypatch.setenv("QUEUE_MODE", "redis")
    monkeypatch.setattr(queue_module, "_build_redis_client", _unexpected)

    second = load_default_queue()

    assert second is first
