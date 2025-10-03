#!/usr/bin/env python3
"""Redis-based distributed locking for resource coordination."""
from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)


class LockError(Exception):
    """Base exception for lock-related errors."""
    pass


class LockAcquisitionError(LockError):
    """Failed to acquire lock."""
    pass


class LockReleaseError(LockError):
    """Failed to release lock."""
    pass


class DistributedLock:
    """
    Redis-based distributed lock using SET NX with TTL.

    Implements the Redlock algorithm for distributed locking.
    """

    def __init__(
        self,
        resource: str,
        ttl: int = 30,
        client=None,
        retry_count: int = 3,
        retry_delay: float = 0.1
    ):
        """
        Initialize a distributed lock.

        Args:
            resource: Resource identifier to lock (e.g., "deploy:production")
            ttl: Lock timeout in seconds (default: 30)
            client: Redis client (will create one if not provided)
            retry_count: Number of acquisition retries (default: 3)
            retry_delay: Delay between retries in seconds (default: 0.1)
        """
        self.resource = resource
        self.ttl = ttl
        self.retry_count = retry_count
        self.retry_delay = retry_delay
        self.lock_key = f"lock:{resource}"
        self.lock_value = uuid.uuid4().hex  # Unique value for this lock instance
        self.client = client or self._get_redis_client()
        self._acquired = False

    def _get_redis_client(self):
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
                return redis.Redis(
                    host=host,
                    port=port,
                    db=db,
                    password=password,
                    decode_responses=True
                )
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise LockError(f"Cannot connect to Redis: {e}")

    def acquire(self, blocking: bool = True, timeout: Optional[float] = None) -> bool:
        """
        Acquire the lock.

        Args:
            blocking: Whether to wait for lock availability (default: True)
            timeout: Maximum time to wait for lock in seconds (None = infinite)

        Returns:
            True if lock was acquired, False otherwise
        """
        start_time = time.time()
        attempt = 0

        while True:
            attempt += 1

            # Try to acquire lock with SET NX
            acquired = self.client.set(
                self.lock_key,
                self.lock_value,
                nx=True,  # Only set if doesn't exist
                ex=self.ttl  # Expire after TTL seconds
            )

            if acquired:
                self._acquired = True
                logger.info(f"Acquired lock for {self.resource} (attempt {attempt})")
                return True

            # Check if we should retry
            if not blocking:
                logger.debug(f"Failed to acquire lock for {self.resource} (non-blocking)")
                return False

            if timeout is not None and (time.time() - start_time) >= timeout:
                logger.warning(f"Timeout acquiring lock for {self.resource} after {timeout}s")
                return False

            if attempt >= self.retry_count:
                logger.warning(f"Failed to acquire lock for {self.resource} after {attempt} attempts")
                return False

            # Wait before retrying
            time.sleep(self.retry_delay)

    def release(self) -> bool:
        """
        Release the lock.

        Uses Lua script to ensure atomic check-and-delete.

        Returns:
            True if lock was released, False if not held or error
        """
        if not self._acquired:
            logger.debug(f"Cannot release lock for {self.resource} - not acquired")
            return False

        # Lua script for atomic check-and-delete
        # Only deletes if the value matches (prevents releasing someone else's lock)
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """

        try:
            result = self.client.eval(lua_script, 1, self.lock_key, self.lock_value)
            if result:
                self._acquired = False
                logger.info(f"Released lock for {self.resource}")
                return True
            else:
                logger.warning(f"Lock for {self.resource} was not held or already expired")
                return False
        except Exception as e:
            logger.error(f"Error releasing lock for {self.resource}: {e}")
            raise LockReleaseError(f"Failed to release lock: {e}")

    def extend(self, additional_ttl: int) -> bool:
        """
        Extend the lock TTL.

        Args:
            additional_ttl: Additional seconds to add to TTL

        Returns:
            True if extended, False otherwise
        """
        if not self._acquired:
            return False

        try:
            # Check if we still own the lock
            current_value = self.client.get(self.lock_key)
            if current_value == self.lock_value:
                # Extend the TTL
                self.client.expire(self.lock_key, self.ttl + additional_ttl)
                logger.info(f"Extended lock for {self.resource} by {additional_ttl}s")
                return True
            else:
                logger.warning(f"Cannot extend lock for {self.resource} - not owner")
                self._acquired = False
                return False
        except Exception as e:
            logger.error(f"Error extending lock for {self.resource}: {e}")
            return False

    def is_locked(self) -> bool:
        """Check if the resource is currently locked (by anyone)."""
        return self.client.exists(self.lock_key) > 0

    def __enter__(self):
        """Context manager entry."""
        if not self.acquire():
            raise LockAcquisitionError(f"Failed to acquire lock for {self.resource}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.release()


@contextmanager
def acquire_lock(
    resource: str,
    ttl: int = 30,
    blocking: bool = True,
    timeout: Optional[float] = None,
    client=None
):
    """
    Convenience context manager for acquiring locks.

    Usage:
        with acquire_lock("deploy:production", ttl=300):
            # Critical section
            deploy_to_production()

    Args:
        resource: Resource identifier to lock
        ttl: Lock timeout in seconds
        blocking: Whether to wait for lock
        timeout: Maximum wait time
        client: Redis client (optional)

    Raises:
        LockAcquisitionError: If lock cannot be acquired
    """
    lock = DistributedLock(resource, ttl=ttl, client=client)

    if not lock.acquire(blocking=blocking, timeout=timeout):
        raise LockAcquisitionError(f"Failed to acquire lock for {resource}")

    try:
        yield lock
    finally:
        lock.release()


def try_acquire_lock(
    resource: str,
    ttl: int = 30,
    client=None
) -> Optional[DistributedLock]:
    """
    Try to acquire a lock without blocking.

    Returns:
        Lock instance if acquired, None otherwise
    """
    lock = DistributedLock(resource, ttl=ttl, client=client)

    if lock.acquire(blocking=False):
        return lock
    return None


# Common lock patterns
def deployment_lock(environment: str, ttl: int = 300):
    """Lock for deployment to a specific environment."""
    return acquire_lock(f"deploy:{environment}", ttl=ttl)


def repository_lock(repo_name: str, ttl: int = 60):
    """Lock for repository operations."""
    return acquire_lock(f"repo:{repo_name}", ttl=ttl)


def resource_lock(resource_type: str, resource_id: str, ttl: int = 30):
    """Generic resource lock."""
    return acquire_lock(f"resource:{resource_type}:{resource_id}", ttl=ttl)


__all__ = [
    "DistributedLock",
    "acquire_lock",
    "try_acquire_lock",
    "deployment_lock",
    "repository_lock",
    "resource_lock",
    "LockError",
    "LockAcquisitionError",
    "LockReleaseError",
]