"""
Audit log retrieval endpoints with API key authentication.

Provides read-only access to audit logs stored in Redis for authorized operators.
All responses use Portuguese field names for consistency with the rest of the system.
"""
import json
import logging
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def get_redis_client():
    """Get Redis client for audit operations."""
    try:
        import redis
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        return redis.Redis.from_url(redis_url, decode_responses=True)
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        return None


async def verify_operator_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    """
    Dependency for X-API-Key authentication.

    Validates that the provided API key matches WF_OPERATOR_API_KEY environment variable.

    Args:
        x_api_key: API key from X-API-Key header

    Returns:
        The validated API key (for logging purposes)

    Raises:
        HTTPException 403: If key is missing or invalid
        HTTPException 500: If WF_OPERATOR_API_KEY is not configured
    """
    expected_key = os.getenv("WF_OPERATOR_API_KEY")

    if not expected_key:
        logger.error("WF_OPERATOR_API_KEY not configured - audit endpoints unavailable")
        raise HTTPException(
            status_code=500,
            detail="Configuração do servidor: WF_OPERATOR_API_KEY não definida"
        )

    if not x_api_key or x_api_key != expected_key:
        logger.warning("Invalid API key attempt for audit endpoint")
        raise HTTPException(
            status_code=403,
            detail="Acesso negado: X-API-Key inválida ou ausente"
        )

    return x_api_key


@router.get("/tail")
async def get_audit_tail(
    n: int = Query(200, description="Number of recent entries to retrieve", ge=1, le=2000),
    _api_key: str = Depends(verify_operator_api_key)
) -> List[dict]:
    """
    Retrieve recent audit log entries in JSON format.

    Requires X-API-Key header matching WF_OPERATOR_API_KEY environment variable.

    Args:
        n: Number of recent entries to retrieve (1-2000, default 200)

    Returns:
        JSON array of audit entries with Portuguese field names:
        [
            {
                "ts": "2025-10-01T14:30:00Z",
                "sessao": "sess-abc123",
                "usuario": "chat",
                "acao": "user_message",
                "dados": {"texto": "criar card..."}
            }
        ]

    Raises:
        HTTPException 403: Invalid or missing API key
        HTTPException 503: Redis unavailable
    """
    redis_client = get_redis_client()

    if not redis_client:
        logger.error("Redis unavailable for audit retrieval")
        raise HTTPException(
            status_code=503,
            detail="Serviço de auditoria temporariamente indisponível (Redis não conectado)"
        )

    try:
        # LRANGE returns list from head (most recent) to tail
        # Index 0 = most recent entry (last LPUSH)
        # Index -1 = oldest entry
        # We want last N entries: LRANGE 0 (n-1)
        raw_entries = redis_client.lrange("wf:audit:recent", 0, n - 1)

        if not raw_entries:
            logger.info("No audit entries found")
            return []

        # Parse JSON entries
        entries = []
        for raw_entry in raw_entries:
            try:
                entry = json.loads(raw_entry)
                entries.append(entry)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse audit entry: {e}")
                continue

        logger.info(f"Retrieved {len(entries)} audit entries (requested: {n})")
        return entries

    except Exception as e:
        logger.error(f"Error retrieving audit log: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao recuperar log de auditoria: {str(e)}"
        )


@router.get("/tail/text", response_class=PlainTextResponse)
async def get_audit_tail_text(
    n: int = Query(200, description="Number of recent entries to retrieve", ge=1, le=2000),
    _api_key: str = Depends(verify_operator_api_key)
) -> str:
    """
    Retrieve recent audit log entries in human-readable text format.

    Requires X-API-Key header matching WF_OPERATOR_API_KEY environment variable.

    Args:
        n: Number of recent entries to retrieve (1-2000, default 200)

    Returns:
        Plain text with one entry per line:
        [2025-10-01T14:30:00Z] sess-abc123 | user_message | {"texto": "criar card..."}

    Raises:
        HTTPException 403: Invalid or missing API key
        HTTPException 503: Redis unavailable
    """
    redis_client = get_redis_client()

    if not redis_client:
        logger.error("Redis unavailable for audit retrieval")
        raise HTTPException(
            status_code=503,
            detail="Serviço de auditoria temporariamente indisponível (Redis não conectado)"
        )

    try:
        # Retrieve entries using same logic as JSON endpoint
        raw_entries = redis_client.lrange("wf:audit:recent", 0, n - 1)

        if not raw_entries:
            return "# Nenhuma entrada de auditoria encontrada\n"

        # Format as human-readable lines
        lines = []
        for raw_entry in raw_entries:
            try:
                entry = json.loads(raw_entry)

                # Extract fields with defaults
                ts = entry.get("ts", "N/A")
                sessao = entry.get("sessao", "N/A")
                acao = entry.get("acao", "N/A")
                dados = entry.get("dados", {})

                # Format: [timestamp] session | action | data
                dados_str = json.dumps(dados, ensure_ascii=False)
                line = f"[{ts}] {sessao} | {acao} | {dados_str}"
                lines.append(line)

            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse audit entry: {e}")
                lines.append(f"# [ERRO] Entrada corrompida: {raw_entry[:100]}...")
                continue

        logger.info(f"Retrieved {len(lines)} audit entries as text (requested: {n})")

        # Join with newlines and add header
        header = f"# WordFlux Audit Log - {len(lines)} entradas\n"
        return header + "\n".join(lines) + "\n"

    except Exception as e:
        logger.error(f"Error retrieving audit log: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao recuperar log de auditoria: {str(e)}"
        )
