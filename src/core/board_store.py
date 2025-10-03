"""Centralized board store utilities for dual-read and canonical PT columns."""
from __future__ import annotations

import json
import logging
import os
import unicodedata
from typing import Dict, List, Optional, Tuple

import redis
from pydantic import ValidationError

from src.core.schemas import BoardStateSchema, CardSchema

logger = logging.getLogger(__name__)

# Redis configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_MAX_CONNECTIONS = int(os.getenv("REDIS_MAX_CONNECTIONS", "16"))

# Canonical Portuguese columns (PT5)
CANONICAL_PT_COLUMNS: List[str] = [
    "Espera",
    "Produção",
    "Aprovação",
    "Agendado",
    "Finalizado",
]

# Legacy English → Portuguese mapping
COLUMN_SYNONYMS: Dict[str, str] = {
    "Backlog": "Espera",
    "In Progress": "Produção",
    "Waiting Approval": "Aprovação",
    "Scheduled": "Agendado",
    "Published": "Finalizado",
}

# Pre-compute lowercase lookups for fast normalization
_LOWER_PT = {col.lower(): col for col in CANONICAL_PT_COLUMNS}
_LOWER_EN = {en.lower(): pt for en, pt in COLUMN_SYNONYMS.items()}


class EnglishColumnError(ValueError):
    """Raised when an English column name is used where only PT is allowed."""

    def __init__(self, column: str):
        super().__init__(
            f"Coluna em inglês não permitida: '{column}'. "
            "Use: Espera, Produção, Aprovação, Agendado, Finalizado"
        )
        self.column = column
        self.reason = "en_attempt"


class InvalidColumnError(ValueError):
    """Raised when a column cannot be canonicalized."""

    def __init__(self, column: Optional[str]):
        super().__init__(
            "Coluna inválida. Use: Espera, Produção, Aprovação, Agendado, Finalizado"
        )
        self.column = column
        self.reason = "invalid_column"


_redis_client: Optional[redis.Redis] = None


def get_redis_client() -> Optional[redis.Redis]:
    """Return singleton Redis client for board operations."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    try:
        _redis_client = redis.from_url(REDIS_URL, max_connections=REDIS_MAX_CONNECTIONS)
        return _redis_client
    except Exception as exc:  # pragma: no cover - network errors
        logger.error("❌ Falha ao conectar no Redis do board: %s", exc)
        return None


def _strip_accents(text: str) -> str:
    """Remove accents for comparison purposes."""
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text)
        if unicodedata.category(ch) != "Mn"
    )


def canonicalize_list(name: Optional[str], *, allow_english: bool = False) -> str:
    """Canonicalize a column/list name to PT5.

    Args:
        name: Column provided by caller
        allow_english: Allow English synonyms (for read paths only)

    Returns:
        Canonical PT column name

    Raises:
        EnglishColumnError: If English synonym used when allow_english=False
        InvalidColumnError: If name cannot be mapped
    """
    if not name or not isinstance(name, str):
        raise InvalidColumnError(name)

    candidate = name.strip()
    if candidate in CANONICAL_PT_COLUMNS:
        return candidate

    if candidate in COLUMN_SYNONYMS:
        if allow_english:
            return COLUMN_SYNONYMS[candidate]
        raise EnglishColumnError(candidate)

    # Case-insensitive PT
    lowered = candidate.lower()
    if lowered in _LOWER_PT:
        return _LOWER_PT[lowered]

    # Case-insensitive EN
    if lowered in _LOWER_EN:
        if allow_english:
            return _LOWER_EN[lowered]
        # Recover original canonical English for error messaging
        for en, pt in COLUMN_SYNONYMS.items():
            if en.lower() == lowered:
                raise EnglishColumnError(en)
        raise EnglishColumnError(candidate)

    # Accent-insensitive PT
    normalized = _strip_accents(candidate).lower().replace(" ", "")
    for col in CANONICAL_PT_COLUMNS:
        if _strip_accents(col).lower().replace(" ", "") == normalized:
            return col

    for en, pt in COLUMN_SYNONYMS.items():
        if _strip_accents(en).lower().replace(" ", "") == normalized:
            if allow_english:
                return pt
            raise EnglishColumnError(en)

    raise InvalidColumnError(candidate)


def canonicalize_column(name: Optional[str], *, allow_english: bool = False) -> str:
    """Alias for canonicalize_list to preserve legacy naming."""
    return canonicalize_list(name, allow_english=allow_english)


def _decode_card(
    redis_client: redis.Redis,
    redis_key: str,
    raw_value: str,
    source_label: str,
    column_name: str,
    metrics: Dict[str, int],
    seen_ids: set,
    dedupe: bool = True,
) -> Optional[Dict[str, any]]:
    """Decode JSON stored in Redis and validate via CardSchema."""
    try:
        card = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        logger.warning(
            "⚠️ Card mal-formado removido (%s %s): %s",
            source_label,
            column_name,
            exc,
        )
        redis_client.lrem(redis_key, 1, raw_value)
        return None

    card_id = card.get("id")
    if dedupe and card_id and card_id in seen_ids:
        metrics["deduped"] += 1
        return None

    try:
        validated_card = CardSchema(**card).model_dump()
    except ValidationError as exc:
        logger.warning(
            "⚠️ Card inválido removido (%s %s): %s",
            source_label,
            column_name,
            exc,
        )
        redis_client.lrem(redis_key, 1, raw_value)
        return None

    if card_id:
        seen_ids.add(card_id)

    metrics[source_label] += 1
    return validated_card


def get_board_snapshot() -> Dict[str, any]:
    """Fetch board state using dual-read PT/EN with PT canonical columns."""
    redis_client = get_redis_client()
    if not redis_client:
        raise RuntimeError("Redis indisponível para leitura do board")

    columns: Dict[str, List[Dict[str, any]]] = {col: [] for col in CANONICAL_PT_COLUMNS}
    metrics = {"pt": 0, "en": 0, "deduped": 0}

    for pt_col in CANONICAL_PT_COLUMNS:
        seen_ids: set = set()
        pt_key = f"wf:board:col:{pt_col}"
        pt_items = redis_client.lrange(pt_key, 0, -1)

        for raw in pt_items:
            card = _decode_card(
                redis_client,
                pt_key,
                raw,
                "pt",
                pt_col,
                metrics,
                seen_ids,
            )
            if card:
                columns[pt_col].append(card)

        # Merge legacy English keys for same column (read-only)
        for en_name, mapped_pt in COLUMN_SYNONYMS.items():
            if mapped_pt != pt_col:
                continue

            en_key = f"wf:board:col:{en_name}"
            en_items = redis_client.lrange(en_key, 0, -1)

            for raw in en_items:
                card = _decode_card(
                    redis_client,
                    en_key,
                    raw,
                    "en",
                    en_name,
                    metrics,
                    seen_ids,
                )
                if card:
                    columns[pt_col].append(card)

    autopilot_enabled = redis_client.get("wf:agent:autopilot") == "1"

    if metrics["en"] or metrics["deduped"]:
        logger.info(
            "📊 board_snapshot dual-read pt=%s en=%s deduped=%s",
            metrics["pt"],
            metrics["en"],
            metrics["deduped"],
        )

    board_state = BoardStateSchema(columns=columns, autopilot=autopilot_enabled)
    return board_state.model_dump()


__all__ = [
    "CANONICAL_PT_COLUMNS",
    "COLUMN_SYNONYMS",
    "canonicalize_column",
    "canonicalize_list",
    "get_board_snapshot",
    "EnglishColumnError",
    "InvalidColumnError",
]
