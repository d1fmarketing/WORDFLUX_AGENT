from __future__ import annotations

import pytest
import fakeredis

from src.core.job import build_job
from src.core.queue import MemoryJobQueue, RedisJobQueue, load_default_queue


def test_memory_queue_publish_and_consume() -> None:
    queue = MemoryJobQueue()
    job = build_job(agent="echo", payload={"message": "hi"})

    queue.publish(job)
    retrieved = queue.consume(timeout=0.1)

    assert retrieved is not None
    assert retrieved.job.agent == job.agent
    assert retrieved.job.payload == job.payload
    queue.task_done()


def test_redis_queue_publish_and_consume_via_lpop() -> None:
    client = fakeredis.FakeStrictRedis(decode_responses=True)
    queue = RedisJobQueue(client=client, key="test-queue")
    job = build_job(agent="echo", payload={"message": "hello"})

    queue.publish(job)
    retrieved = queue.consume(timeout=0.01)

    assert retrieved is not None
    assert retrieved.job.agent == job.agent
    assert retrieved.job.payload == job.payload

    # Verify ack removes from processing list
    retrieved.ack()
    assert client.lrange("test-queue:processing", 0, -1) == []


def test_redis_queue_publish_and_consume_via_blpop() -> None:
    client = fakeredis.FakeStrictRedis(decode_responses=True)
    queue = RedisJobQueue(client=client, key="test-queue")
    job = build_job(agent="echo", payload={"message": "hello"})

    queue.publish(job)
    retrieved = queue.consume(timeout=2.4)

    assert retrieved is not None
    assert retrieved.job.agent == job.agent
    assert retrieved.job.payload == job.payload


def test_redis_queue_consume_empty_returns_none() -> None:
    client = fakeredis.FakeStrictRedis(decode_responses=True)
    queue = RedisJobQueue(client=client, key="test-queue")

    # Use a small timeout instead of 0 to avoid blocking
    assert queue.consume(timeout=0.01) is None


def test_redis_queue_invalid_payload_returns_none() -> None:
    client = fakeredis.FakeStrictRedis(decode_responses=True)
    queue = RedisJobQueue(client=client, key="test-queue")

    # Manually push invalid JSON to the queue
    client.rpush("test-queue", "not-json")

    assert queue.consume(timeout=0.01) is None


def test_load_default_queue_uses_redis(monkeypatch) -> None:
    from src.core import queue as queue_module

    monkeypatch.setenv("QUEUE_MODE", "redis")
    monkeypatch.setenv("REDIS_QUEUE_KEY", "wordflux:test")
    fake_client = fakeredis.FakeStrictRedis(decode_responses=True)
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
