"""Helpers for uploading artifacts to object storage."""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Dict, Optional

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from src.core.config import get_config

logger = logging.getLogger(__name__)

_CLIENT: Any | None = None
_KMS_CLIENT: Any | None = None


def reset_artifact_client_cache() -> None:
    """Clear cached AWS clients (primarily for tests)."""
    global _CLIENT, _KMS_CLIENT
    _CLIENT = None
    _KMS_CLIENT = None


def _s3_client() -> Any:
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    cfg = get_config()
    boto_cfg = BotoConfig(
        retries={"mode": "standard", "max_attempts": int(os.getenv("S3_MAX_ATTEMPTS", "2"))},
        connect_timeout=float(os.getenv("S3_CONNECT_TIMEOUT", "2")),
        read_timeout=float(os.getenv("S3_READ_TIMEOUT", "5")),
        user_agent_extra="wordflux-artifacts/1",
    )
    kwargs: Dict[str, Any] = {"region_name": cfg.aws_region, "config": boto_cfg}
    if cfg.s3_endpoint_url:
        kwargs["endpoint_url"] = cfg.s3_endpoint_url

    _CLIENT = boto3.client("s3", **kwargs)
    return _CLIENT


def _kms_client(region_name: str) -> Any:
    global _KMS_CLIENT
    if _KMS_CLIENT is not None and getattr(_KMS_CLIENT, "_wf_region", None) == region_name:
        return _KMS_CLIENT

    client = boto3.client("kms", region_name=region_name)
    setattr(client, "_wf_region", region_name)
    _KMS_CLIENT = client
    return _KMS_CLIENT


def _is_kms_related(error_code: str) -> bool:
    return any(
        token in (error_code or "")
        for token in (
            "KMS.",
            "AccessDenied",
            "InvalidArgument",
            "InvalidKey",
            "NotFound",
            "Disabled",
        )
    )


def put_bytes(key: str, data: bytes, mime: str, ttl_sec: Optional[int] = None) -> str:
    """Upload bytes to S3 and return a presigned URL; gracefully handles KMS fallbacks."""
    if not key:
        raise ValueError("artifact key must be provided")

    cfg = get_config()
    if not cfg.artifact_bucket:
        raise ValueError("ARTIFACT_BUCKET is not configured")

    client = _s3_client()
    base_headers: Dict[str, Any] = {
        "ContentType": mime,
        "Metadata": {"sha256": hashlib.sha256(data).hexdigest()},
    }

    mode = (cfg.artifact_encryption or "auto").lower()
    allow_fallback = cfg.artifact_fallback_sse_s3

    want_kms = mode in {"auto", "kms"} and bool(cfg.artifact_kms_key)
    force_s3 = mode == "s3"
    disable_sse = mode == "none"

    headers = dict(base_headers)
    if want_kms:
        headers["ServerSideEncryption"] = "aws:kms"
        headers["SSEKMSKeyId"] = cfg.artifact_kms_key
        try:  # best-effort validation
            _kms_client(cfg.aws_region).describe_key(KeyId=cfg.artifact_kms_key)  # type: ignore[arg-type]
        except Exception:  # pragma: no cover - allow S3 to surface precise error
            pass
    elif force_s3:
        headers["ServerSideEncryption"] = "AES256"
    elif disable_sse:
        pass

    def _put(extra: Dict[str, Any]) -> None:
        client.put_object(Bucket=cfg.artifact_bucket, Key=key, Body=data, **extra)

    try:
        _put(headers)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if want_kms and allow_fallback and _is_kms_related(code):
            fallback_headers = dict(base_headers)
            fallback_headers["ServerSideEncryption"] = "AES256"
            _put(fallback_headers)
            logger.warning(
                "artifact_upload_kms_fallback",
                extra={"bucket": cfg.artifact_bucket, "key": key, "error_code": code},
            )
        else:
            raise RuntimeError(
                f"failed to upload artifact to S3 (code={code or 'unknown'})"
            ) from exc
    except BotoCoreError as exc:
        raise RuntimeError("failed to upload artifact to S3 (botocore)") from exc

    ttl = int(ttl_sec or cfg.artifact_url_ttl)
    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": cfg.artifact_bucket, "Key": key},
            ExpiresIn=ttl,
        )
    except (ClientError, BotoCoreError) as exc:
        raise RuntimeError("failed to generate presigned URL") from exc


__all__ = ["put_bytes", "reset_artifact_client_cache"]
