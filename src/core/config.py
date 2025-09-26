"""Configuration loader with ENV > .env precedence."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

try:  # python-dotenv is optional
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - dotenv missing
    def load_dotenv(*_args: object, **_kwargs: object) -> bool:  # type: ignore
        return False


@dataclass
class Config:
    """Runtime configuration (ENV > .env > defaults)."""

    aws_region: str = "us-east-1"
    artifact_bucket: Optional[str] = None
    artifact_kms_key: Optional[str] = None
    artifact_url_ttl: int = 3600
    s3_endpoint_url: Optional[str] = None
    artifact_encryption: str = "auto"
    artifact_fallback_sse_s3: bool = True

    queue_mode: str = "memory"
    redis_url: Optional[str] = None
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: Optional[str] = None
    redis_queue_key: str = "wordflux:jobs"

    environment: str = "development"
    debug: bool = False


def _from_env(env: dict[str, str]) -> Config:
    return Config(
        aws_region=env.get("AWS_REGION", "us-east-1"),
        artifact_bucket=env.get("ARTIFACT_BUCKET"),
        artifact_kms_key=env.get("ARTIFACT_SSE_KMS_KEY"),
        artifact_url_ttl=int(env.get("ARTIFACT_URL_TTL", "3600")),
        s3_endpoint_url=env.get("ARTIFACT_S3_ENDPOINT_URL"),
        artifact_encryption=env.get("ARTIFACT_ENCRYPTION", "auto"),
        artifact_fallback_sse_s3=env.get("ARTIFACT_FALLBACK_SSE_S3", "true").lower() in {"1", "true", "yes"},
        queue_mode=env.get("QUEUE_MODE", "memory"),
        redis_url=env.get("REDIS_URL"),
        redis_host=env.get("REDIS_HOST", "127.0.0.1"),
        redis_port=int(env.get("REDIS_PORT", "6379")),
        redis_db=int(env.get("REDIS_DB", "0")),
        redis_password=env.get("REDIS_PASSWORD"),
        redis_queue_key=env.get("REDIS_QUEUE_KEY", "wordflux:jobs"),
        environment=env.get("ENVIRONMENT", "development"),
        debug=env.get("DEBUG", "false").lower() in {"true", "1", "yes"},
    )


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Load .env without overriding existing os.environ, then read env."""
    load_dotenv(override=False)
    return _from_env(dict(os.environ))


def reload_config() -> Config:
    """Clear cache and re-read environment variables."""
    get_config.cache_clear()  # type: ignore[attr-defined]
    return get_config()


def clear_config_cache() -> None:
    """Alias for clearing cached configuration (tests)."""
    get_config.cache_clear()  # type: ignore[attr-defined]


__all__ = ["Config", "get_config", "reload_config", "clear_config_cache"]
