"""Chat API router with LLM integration and approval workflow."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, TypedDict, Literal

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError, validator, model_validator
from typing import Literal

from src.core.board_store import (
    CANONICAL_PT_COLUMNS,
    get_board_snapshot,
)
from src.core.events import emit_chat_message

# Import optimistic update metrics
from src.core.metrics_optimistic import (
    record_optimistic_job_queued,
    record_pipeline_execution,
    record_clock_skew_fallback,
)

logger = logging.getLogger(__name__)

# Create router
router = APIRouter()

# Minimalist risk policy (2025-09-30):
# Only production-impacting actions require approval:
# 1. Moving card to "Finalizado" (terminal/production state)
# 2. Agent name contains "deploy_prod" (production deployments)
# All other actions auto-execute for reduced friction (~85% of actions)

# Configuration
WF_CHAT_TTL_SEC = int(os.getenv("WF_CHAT_TTL_SEC", "86400"))  # 24 hours
WF_RATELIMIT_PER_MIN = int(os.getenv("WF_RATELIMIT_PER_MIN", "20"))
WF_TOKEN_INPUT_CAP = int(os.getenv("WF_TOKEN_INPUT_CAP", "8000"))  # Max input tokens

HTTP_CONNECT_TIMEOUT = float(os.getenv("WF_HTTP_CONNECT_TIMEOUT", "0.5"))
HTTP_READ_TIMEOUT = float(os.getenv("WF_HTTP_READ_TIMEOUT", "5.0"))
COCKPIT_TIMEOUT: tuple[float, float] = (HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT)
SUMMARY_HEALTH_THRESHOLD_MS = float(os.getenv("WF_SUMMARY_HEALTH_MS", "300"))

logger.info(
    "http_timeouts",
    extra={
        "connect": HTTP_CONNECT_TIMEOUT,
        "read": HTTP_READ_TIMEOUT,
    },
)

# Canonical Portuguese columns used across the stack
CANONICAL_PT_COLUMNS: List[str] = [
    "Espera",
    "Produção",
    "Aprovação",
    "Agendado",
    "Finalizado",
]

# Full synonym map (legacy English → canonical Portuguese)
COLUMN_EN_PT_SYNONYMS: Dict[str, str] = {
    "Backlog": "Espera",
    "In Progress": "Produção",
    "Waiting Approval": "Aprovação",
    "Scheduled": "Agendado",
    "Published": "Finalizado",
}

# Confirmation keywords (Portuguese + English for flexibility)
# Used for word-boundary matching to avoid false positives
CONFIRMATION_AFFIRMATIVE = {
    # Portuguese
    "sim", "s", "confirmo", "confirmar", "confirma",
    "ok", "okay", "vai", "pode", "vamos",
    "aceito", "concordo", "beleza",
    # English
    "yes", "y", "yeah", "yep", "sure", "confirm"
}

CONFIRMATION_NEGATIVE = {
    # Portuguese
    "não", "nao", "n", "cancelar", "cancela", "cancelo",
    "abortar", "aborta", "aborto", "para", "pare",
    "negativo", "nada", "jamais", "nunca",
    # English
    "no", "nope", "nah", "cancel", "abort", "stop", "negative"
}


def check_confirmation_intent(user_message: str) -> str:
    """
    Determine user's confirmation intent with word-boundary matching.

    Prevents false positives from substring matching (e.g., "sim" in "assim").
    Handles negation patterns (e.g., "não sim" is not affirmative).

    Args:
        user_message: Raw user message

    Returns:
        "affirmative" | "negative" | "unclear"

    Examples:
        >>> check_confirmation_intent("sim")
        "affirmative"
        >>> check_confirmation_intent("assim não")
        "negative"
        >>> check_confirmation_intent("não pode")
        "unclear"
        >>> check_confirmation_intent("talvez")
        "unclear"
    """
    import re

    # Extract words using word boundaries (no partial matches)
    words = re.findall(r'\b\w+\b', user_message.lower())

    # Check each word for confirmation keywords
    for i, word in enumerate(words):
        # Check affirmative keywords
        if word in CONFIRMATION_AFFIRMATIVE:
            # Check for negation prefix (não sim, never yes, etc.)
            if i > 0 and words[i-1] in {"não", "nao", "no", "never", "not"}:
                continue  # Skip negated affirmative
            return "affirmative"

        # Check negative keywords
        if word in CONFIRMATION_NEGATIVE:
            return "negative"

    return "unclear"


def check_summary_intent(user_message: str) -> bool:
    """
    Detect if user's message is requesting a board summary.

    Uses word-boundary regex matching to detect Portuguese summary keywords.
    Prevents false positives from substring matching.

    Args:
        user_message: Raw user message

    Returns:
        True if summary intent detected, False otherwise

    Examples:
        >>> check_summary_intent("quantos cards temos?")
        True
        >>> check_summary_intent("resumo do board")
        True
        >>> check_summary_intent("qual o status?")
        True
        >>> check_summary_intent("crie um card de resumo")
        False  # "resumo" is part of card title, not intent
    """
    import re

    # Lowercase for case-insensitive matching
    text = user_message.lower()

    # Exclude card creation/modification commands
    # If message starts with creation/modification verbs, it's not a summary intent
    creation_patterns = [
        r'^\s*(crie|criar|cria|adicione|adicionar|adiciona|mova|mover|move|atualize|atualizar|atualiza)\b'
    ]

    for pattern in creation_patterns:
        if re.search(pattern, text):
            return False  # Exclude creation commands

    # Summary intent patterns (Portuguese + English)
    # Captures: resumo, quantos, o que temos, para hoje, status, etc.
    summary_patterns = [
        r'\bresumo\b',           # resumo do board
        r'\bquantos?\b',         # quantos cards / quantas tarefas
        r'\bo que temos\b',      # o que temos no board
        r'\bpara hoje\b',        # cards para hoje
        r'\bstatus\b',           # status do board
        r'\boverview\b',         # overview (English)
        r'\btotal\b',            # total de cards
        r'\bvisão geral\b',      # visão geral
        r'\bsumário\b',          # sumário
        r'\bpanorama\b'          # panorama
    ]

    # Check if any pattern matches
    for pattern in summary_patterns:
        if re.search(pattern, text):
            return True

    return False


def generate_confirmation_idempotency_key(
    session_id: str,
    tool_call: Dict[str, Any]
) -> str:
    """
    Generate deterministic idempotency key for confirmation.

    Uses SHA-1 hash of session ID + tool name + stable JSON args.
    This prevents duplicate job creation when user confirms twice.

    Args:
        session_id: User session ID
        tool_call: Tool call dict with keys: name, input

    Returns:
        40-character SHA-1 hex string

    Example:
        >>> tool_call = {"name": "move_card", "input": {"to": "Finalizado"}}
        >>> generate_confirmation_idempotency_key("sess-abc", tool_call)
        'a7f3e89c12d45b6f8901234567890abcdef12345'
    """
    tool_name = tool_call["name"]
    tool_input = tool_call["input"]

    # Stable JSON serialization (sorted keys, no whitespace, UTF-8)
    stable_input = json.dumps(tool_input, sort_keys=True, ensure_ascii=False)

    # Combine components
    payload = f"{session_id}:{tool_name}:{stable_input}"

    # SHA-1 hash (40 chars, sufficient for this use case)
    return hashlib.sha1(payload.encode('utf-8')).hexdigest()


def check_confirmation_idempotency(
    session_id: str,
    tool_call: Dict[str, Any]
) -> Optional[str]:
    """
    Check if confirmation already executed (idempotency guard).

    Returns the job ID if this exact confirmation was already processed,
    preventing duplicate job creation from double-click or race conditions.

    Args:
        session_id: User session ID
        tool_call: Tool call dict with keys: name, input

    Returns:
        None if not executed yet (safe to proceed)
        job_id (str) if already executed (duplicate detected)

    Example:
        >>> existing_job = check_confirmation_idempotency("sess-abc", tool_call)
        >>> if existing_job:
        >>>     return f"ℹ️ Já confirmado: {existing_job}"
    """
    redis_client = get_redis_client()
    if not redis_client:
        logger.warning("Redis unavailable for idempotency check - allowing execution")
        return None

    # Generate idempotency key
    idemp_key_hash = generate_confirmation_idempotency_key(session_id, tool_call)
    redis_key = f"wf:chat:idemp:{idemp_key_hash}"

    try:
        # Check if already executed
        existing_job_id = redis_client.get(redis_key)
        if existing_job_id:
            logger.info(
                f"🔒 Duplicate confirmation detected - "
                f"session={session_id[:12]}, job={existing_job_id}"
            )
            return existing_job_id

        return None
    except Exception as e:
        logger.error(f"Idempotency check failed: {e}")
        # Fail open (allow execution on error)
        return None


def mark_confirmation_executed(
    session_id: str,
    tool_call: Dict[str, Any],
    job_id: str
) -> bool:
    """
    Atomically mark confirmation as executed using SETNX.

    This prevents race conditions when multiple confirmation requests
    arrive simultaneously. Only the first request will succeed.

    Args:
        session_id: User session ID
        tool_call: Tool call dict with keys: name, input
        job_id: Generated job ID to store

    Returns:
        True if marked successfully (won the race, safe to execute)
        False if already marked (lost the race, abort execution)

    Example:
        >>> if mark_confirmation_executed(session_id, tool_call, job_id):
        >>>     queue.publish(job)  # Safe to queue
        >>> else:
        >>>     # Another process already queued - abort
        >>>     pass
    """
    redis_client = get_redis_client()
    if not redis_client:
        logger.warning("Redis unavailable for idempotency marking - proceeding")
        return True

    # Generate idempotency key
    idemp_key_hash = generate_confirmation_idempotency_key(session_id, tool_call)
    redis_key = f"wf:chat:idemp:{idemp_key_hash}"

    try:
        # SETNX: Set only if not exists (atomic operation)
        # TTL: 600s (10 minutes, matches pending confirmation TTL)
        was_set = redis_client.set(redis_key, job_id, nx=True, ex=600)

        if was_set:
            logger.info(
                f"✅ Confirmation marked as executed - "
                f"session={session_id[:12]}, job={job_id}"
            )
            return True
        else:
            logger.warning(
                f"⚠️ Idempotency race detected - another process won - "
                f"session={session_id[:12]}"
            )
            return False
    except Exception as e:
        logger.error(f"Failed to mark idempotency: {e}")
        # Fail open (assume success to avoid blocking legitimate requests)
        return True


# Sensitive keys that should be masked in audit logs
SENSITIVE_KEYS = {
    # Auth headers
    "authorization", "auth", "x-api-key", "api-key", "api_key",
    # Environment keys
    "anthropic_api_key", "openai_api_key", "github_token",
    "slack_webhook_url", "redis_password", "aws_secret_access_key",
    "bedrock_access_key", "secret_key", "access_key",
    # JWT/Session tokens
    "token", "jwt", "session_token", "cookie", "csrf_token",
    "bearer", "basic", "digest",
    # Webhook URLs
    "webhook", "webhook_url", "callback_url",
    # Other sensitive
    "password", "passphrase", "private_key", "secret"
}


def sanitize_value(value: Any, depth: int = 0, max_depth: int = 10) -> Any:
    """
    Recursively sanitize sensitive data for audit logging.

    Protects against:
    - Credential leakage (masks sensitive keys)
    - Memory exhaustion (truncates long strings)
    - Stack overflow (depth limit)
    - Binary data exposure (converts to placeholder)

    Args:
        value: Value to sanitize (any type)
        depth: Current recursion depth (prevents infinite loops)
        max_depth: Maximum recursion depth (default 10)

    Returns:
        Sanitized value with same type structure preserved
    """
    # Depth guard - prevent stack overflow from deeply nested structures
    if depth > max_depth:
        return "[DEPTH_LIMIT_EXCEEDED]"

    # Primitives - pass through unchanged
    if value is None or isinstance(value, (bool, int, float)):
        return value

    # Strings - truncate if too long
    if isinstance(value, str):
        if len(value) > 180:
            return value[:177] + "..."
        return value

    # Bytes - convert to placeholder (don't expose binary data)
    if isinstance(value, bytes):
        return f"[BINARY:{len(value)}_BYTES]"

    # Dictionaries - recursively sanitize keys and values
    if isinstance(value, dict):
        sanitized = {}
        for k, v in value.items():
            key_lower = str(k).lower()

            # Mask sensitive keys
            if any(sensitive in key_lower for sensitive in SENSITIVE_KEYS):
                sanitized[k] = "[MASKED]"
            else:
                sanitized[k] = sanitize_value(v, depth + 1, max_depth)

        return sanitized

    # Lists/tuples - recursively sanitize elements
    if isinstance(value, (list, tuple)):
        sanitized_list = [sanitize_value(item, depth + 1, max_depth) for item in value]
        return sanitized_list if isinstance(value, list) else tuple(sanitized_list)

    # Sets - convert to list and sanitize
    if isinstance(value, set):
        return [sanitize_value(item, depth + 1, max_depth) for item in value]

    # Fallback for unknown types - safe string representation
    try:
        str_repr = str(value)
        if len(str_repr) > 180:
            return str_repr[:177] + "..."
        return str_repr
    except Exception:
        return "[UNSERIALIZABLE]"


# System prompt versions (for A/B testing)
SYSTEM_PROMPTS = {
    "v1_assistive": (
        "Você é o Assistente WordFlux, um agente de automação de workflow em português brasileiro. "
        "Use as ferramentas disponíveis para ajudar o usuário com tarefas do board Kanban. "
        "Para ações de alto risco (mover cards para Produção/Publicado, criar múltiplos cards), "
        "sempre explique claramente o que será feito antes de propor a ação."
    ),
    "v2_direct_enhanced": (
        "<identity>Você é o WordFlux AI, agente de automação de workflow em PT-BR.</identity>\n\n"

        "<capabilities>\n"
        "Você controla o board Kanban por linguagem natural usando ferramentas (tool-use).\n"
        "Ferramentas: create_card, move_card, update_card, comment_card, run_playbook, queue_job.\n"
        "</capabilities>\n\n"

        "<constraints>\n"
        "• Nunca sugira editar DOM/UI\n"
        "• Nunca mude estado fora das ferramentas\n"
        "• Máximo 1 chamada de ferramenta por mensagem\n"
        "</constraints>\n\n"

        "<risk_policy>\n"
        "HIGH RISK (requer confirmação explícita antes de executar):\n"
        "• Mover card para 'Finalizado' (publicação final)\n"
        "• Queue job com agente contendo 'deploy_prod' (case-insensitive)\n\n"

        "LOW RISK (executar imediatamente sem confirmação):\n"
        "• Todas as outras ações (criar/mover para Espera|Produção|Aprovação, atualizar, comentar)\n"
        "</risk_policy>\n\n"

        "<output_format>\n"
        "1. Texto direto em 1-2 linhas explicando a ação\n"
        "2. Chame 1 ferramenta com JSON mínimo (apenas campos obrigatórios)\n"
        "3. Tom assertivo: sem 'vou tentar', sem 'placeholder', sem 'posso ajudar você'\n"
        "</output_format>\n\n"

        "<edge_cases>\n"
        "• Email longo → extraia intents, crie cards um por vez\n"
        "• Informação faltando → peça esclarecimento em 1 linha\n"
        "• Ação não suportada → responda: 'Não posso fazer isso'\n"
        "</edge_cases>\n\n"

        "<examples>\n"
        "User: Crie um card 'Refatorar API'\n"
        "Assistant: Vou criar o card 'Refatorar API' na coluna Espera.\n"
        "[chama create_card]\n\n"

        "User: Mova 'Refatorar API' para Produção\n"
        "Assistant: Vou mover 'Refatorar API' para Produção.\n"
        "[chama move_card]\n\n"

        "User: Mova 'Refatorar API' para Finalizado\n"
        "Assistant: ⚠️ Mover para Finalizado é uma ação de alto risco.\n"
        "Confirmar? (responda 'sim' ou 'não')\n"
        "[aguarda confirmação]\n"
        "</examples>"
    ),
    "v3_ultrashort": (
        "Você é o WordFlux AI. Fale em PT-BR, direto.\n"
        "Você controla o board por linguagem natural usando ferramentas (tool-use).\n"
        "Nunca edite DOM/UI; nunca mude estado fora das ferramentas.\n"
        "Regra de risco: mover para Finalizado ou deploy_prod exige confirmação explícita do usuário no chat; demais ações são executadas imediatamente.\n"
        "Sempre explique em uma linha o que vai fazer; depois chame a ferramenta com o JSON mínimo.\n"
        "Se o usuário colar um e-mail longo, resuma e extraia intents, criando cards conforme instrução sem botão.\n"
        "Saída: texto curto + (se necessário) 1 chamada de ferramenta por mensagem."
    ),
    "v4_bedrock": (
        "Você é o WordFlux AI. Fale em PT-BR, direto.\n\n"

        "Ferramentas disponíveis:\n"
        "• create_card(title, column, assignees?, tags?, due_date?) — criar card\n"
        "• move_card(card_id, to_column) — mover card entre colunas\n"
        "• update_card(card_id, fields) — atualizar campos do card\n"
        "• summarize_board(scope?) — resumo inline do board\n"
        "• ingest_email(raw_text) — extrair intents de email e propor cards\n\n"

        "Colunas: Espera, Produção, Aprovação, Agendado, Finalizado\n\n"

        "REGRA HIGH-RISK:\n"
        "Mover para 'Finalizado' requer confirmação explícita. Pergunte 'Confirmar? (sim/não)'.\n"
        "Outras ações executam imediatamente.\n\n"

        "Formato de resposta:\n"
        "1. Diga em UMA linha o que vai fazer\n"
        "2. Chame a ferramenta com JSON mínimo (apenas campos obrigatórios)\n\n"

        "Nunca altere estado sem ferramenta. Nunca mexa no DOM/UI."
    )
}


def get_system_prompt_for_session(session_id: str) -> str:
    """
    Get system prompt based on A/B test assignment.

    Uses consistent hashing to assign sessions to groups.
    Group assignments cached in Redis for 7 days.

    Args:
        session_id: Session ID

    Returns:
        System prompt string
    """
    import hashlib

    # Check for manual override first
    manual_version = os.getenv("WF_SYSTEM_PROMPT_VERSION")
    if manual_version and manual_version in SYSTEM_PROMPTS:
        return SYSTEM_PROMPTS[manual_version]

    # Default: v4_bedrock (ultra-short PT-BR, optimized for tool-use)
    # A/B testing can be re-enabled by setting WF_SYSTEM_PROMPT_VERSION
    return SYSTEM_PROMPTS["v4_bedrock"]


def estimate_token_count(text: str) -> int:
    """
    Estimate token count for input text.

    Uses conservative estimation: ~4 characters per token (English text).
    This overestimates for safety, preventing cost explosions.

    Args:
        text: Input text to estimate

    Returns:
        Estimated token count (rounded up for safety)
    """
    # Conservative estimate: 4 chars ≈ 1 token
    # (Real tokenization varies but this errs on safe side)
    import math
    return math.ceil(len(text) / 4)


# Pydantic models
class ChatRequest(BaseModel):
    """Request model for chat endpoint."""
    text: Optional[str] = Field(None, max_length=2000, description="User message (max 2000 chars)")
    message: Optional[str] = Field(None, max_length=2000, description="User message (legacy field, use 'text' instead)")
    session_id: str = Field(default_factory=lambda: f"sess-{uuid.uuid4().hex[:8]}", description="Session ID for conversation")

    @model_validator(mode='after')
    def normalize_and_validate_text(self):
        """Normalize 'message' to 'text' field for backward compatibility."""
        # If text is provided and not empty, use it
        if self.text is not None and self.text.strip():
            return self

        # Otherwise, use message field (backward compatibility)
        if self.message is not None and self.message.strip():
            self.text = self.message
            return self

        # Neither provided or both empty - raise error
        raise ValueError("Campo 'text' é obrigatório")

    class Config:
        # Allow both fields to coexist
        extra = 'ignore'


class ChatResponse(BaseModel):
    """Response model for chat endpoint."""
    message: str = Field(..., description="Assistant response")
    role: str = Field(default="assistant", description="Message role")
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list, description="Tool calls made by LLM")
    requires_approval: bool = Field(default=False, description="Whether action requires user approval")
    proposal_id: Optional[str] = Field(default=None, description="ID of proposal if approval required")
    session_id: str = Field(..., description="Session ID")


# Tool execution structured result
class ToolResult(TypedDict, total=False):
    kind: Literal["job", "summary", "error"]
    job_id: str
    action: str
    args: Dict[str, Any]
    text: str
    error: str


# ============================================================================
# TOOL INPUT VALIDATION MODELS
# ============================================================================

# Valid column names (Portuguese) - matches TOOL_SCHEMAS in llm_client.py
ColumnEnum = Literal["Espera", "Produção", "Aprovação", "Finalizado"]


class CreateCardInput(BaseModel):
    """Validation model for create_card tool."""
    title: str = Field(..., max_length=140, description="Card title (required, max 140 chars)")
    column: str = Field("Espera", description="Initial column")
    assignees: List[str] = Field(default_factory=list, description="Assigned users")
    tags: List[str] = Field(default_factory=list, description="Tags for categorization")
    due_date: Optional[str] = Field(None, description="Due date (YYYY-MM-DD)")

    @validator('column')
    def normalize_and_validate_column(cls, v):
        """Normalize column name and validate against canonical list."""
        normalized = normalize_column_name(v)
        if normalized not in ["Espera", "Produção", "Aprovação", "Agendado", "Finalizado"]:
            raise ValueError(
                f"Coluna inválida: '{v}'. "
                f"Use uma destas: Espera, Produção, Aprovação, Agendado, Finalizado"
            )
        return normalized


class MoveCardInput(BaseModel):
    """Validation model for move_card tool."""
    card_id: str = Field(..., description="Card ID")
    to_column: str = Field(..., description="Target column")

    @validator('to_column')
    def normalize_and_validate_column(cls, v):
        """Normalize column name and validate against canonical list."""
        normalized = normalize_column_name(v)
        if normalized not in ["Espera", "Produção", "Aprovação", "Agendado", "Finalizado"]:
            raise ValueError(
                f"Coluna inválida: '{v}'. "
                f"Use uma destas: Espera, Produção, Aprovação, Agendado, Finalizado"
            )
        return normalized


class UpdateCardInput(BaseModel):
    """Validation model for update_card tool."""
    card_id: str = Field(..., description="Card ID")
    fields: Dict[str, Any] = Field(..., description="Fields to update")


class SummarizeBoardInput(BaseModel):
    """Validation model for summarize_board tool."""
    scope: Optional[str] = Field(None, description="Scope filter (e.g., 'hoje', 'esta semana')")


class IngestEmailInput(BaseModel):
    """Validation model for ingest_email tool."""
    raw_text: str = Field(..., description="Raw email text")


# Map tool names to their validation models
TOOL_VALIDATORS: Dict[str, type[BaseModel]] = {
    "create_card": CreateCardInput,
    "move_card": MoveCardInput,
    "update_card": UpdateCardInput,
    "summarize_board": SummarizeBoardInput,
    "ingest_email": IngestEmailInput
}


# ============================================================================
# COLUMN NORMALIZATION (Portuguese + English + Case/Accent Tolerant)
# ============================================================================

COLUMN_ALIASES = {
    # Portuguese (canonical - case-sensitive)
    "Espera": "Espera",
    "Produção": "Produção",
    "Aprovação": "Aprovação",
    "Finalizado": "Finalizado",

    # Portuguese (case-insensitive)
    "espera": "Espera",
    "produção": "Produção",
    "aprovação": "Aprovação",
    "finalizado": "Finalizado",

    # Portuguese (uppercase)
    "ESPERA": "Espera",
    "PRODUÇÃO": "Produção",
    "APROVAÇÃO": "Aprovação",
    "FINALIZADO": "Finalizado",

    # Portuguese (accent-insensitive)
    "producao": "Produção",
    "Producao": "Produção",
    "PRODUCAO": "Produção",
    "aprovacao": "Aprovação",
    "Aprovacao": "Aprovação",
    "APROVACAO": "Aprovação",

    # English (legacy - exact case)
    "Backlog": "Espera",
    "In Progress": "Produção",
    "Waiting Approval": "Aprovação",
    "Scheduled": "Aprovação",  # Merged with Aprovação
    "Published": "Finalizado",

    # English (lowercase)
    "backlog": "Espera",
    "in progress": "Produção",
    "waiting approval": "Aprovação",
    "scheduled": "Aprovação",
    "published": "Finalizado"
}


def normalize_column_name(column: str) -> str:
    """
    Normalize column name to canonical Portuguese (case + accent tolerant).

    Accepts variations like:
    - "APROVACAO" → "Aprovação"
    - "Waiting Approval" → "Aprovação"
    - "producao" → "Produção"

    Args:
        column: Column name in any format

    Returns:
        Canonical Portuguese column name, or original if no match

    Examples:
        >>> normalize_column_name("APROVACAO")
        "Aprovação"
        >>> normalize_column_name("In Progress")
        "Produção"
        >>> normalize_column_name("invalid")
        "invalid"  # No match, will fail validation
    """
    # Direct lookup (handles exact matches including case variations)
    return COLUMN_ALIASES.get(column, column)


def suggest_closest_column(invalid: str) -> Optional[str]:
    """
    Suggest closest valid column name using fuzzy matching.

    Args:
        invalid: Invalid column name from user/LLM

    Returns:
        Suggested column name, or None if no close match

    Examples:
        >>> suggest_closest_column("APROVACAO")
        "Aprovação"
        >>> suggest_closest_column("Producao")
        "Produção"
    """
    from difflib import get_close_matches

    valid_columns = ["Espera", "Produção", "Aprovação", "Agendado", "Finalizado"]

    # Try exact match first (case-insensitive)
    for valid in valid_columns:
        if invalid.lower() == valid.lower():
            return valid

    # Try fuzzy match (60% similarity threshold)
    matches = get_close_matches(invalid, valid_columns, n=1, cutoff=0.6)
    return matches[0] if matches else None


# ============================================================================
# TOOL VALIDATION FUNCTIONS
# ============================================================================

def extract_valid_enum_values(tool_name: str, field_name: str) -> List[str]:
    """
    Extract valid enum values for a specific tool field.

    Used to provide helpful suggestions when validation fails.

    Args:
        tool_name: Name of the tool (e.g., "move_card")
        field_name: Name of the field (e.g., "to_column")

    Returns:
        List of valid enum values, empty if not an enum field

    Examples:
        >>> extract_valid_enum_values("move_card", "to_column")
        ["Espera", "Produção", "Aprovação", "Agendado", "Finalizado"]
    """
    # Column enum fields
    if tool_name in ("move_card", "create_card") and field_name in ("to_column", "column"):
        return ["Espera", "Produção", "Aprovação", "Agendado", "Finalizado"]

    return []


def format_validation_error_pt(error: ValidationError, tool_name: str, original_input: Optional[dict] = None) -> str:
    """
    Format Pydantic validation error in Portuguese with helpful suggestions.

    Converts technical Pydantic errors into user-friendly PT-BR messages.
    For column enum errors, suggests corrections using fuzzy matching.

    Args:
        error: Pydantic ValidationError
        tool_name: Name of the tool that failed validation
        original_input: Original tool input dict (for suggestions)

    Returns:
        User-friendly error message in PT-BR

    Examples:
        >>> # error: ValueError for "to" field with value "APROVACAO"
        >>> format_validation_error_pt(error, "move_card", {"to": "APROVACAO"})
        "⚠️ Coluna inválida: 'APROVACAO'. Você quis dizer 'Aprovação'?..."
    """
    errors = error.errors()

    # Extract first error (most relevant)
    first_error = errors[0]
    field = first_error["loc"][0] if first_error["loc"] else "unknown"
    error_type = first_error["type"]
    error_msg = first_error.get("msg", "")

    # Enhanced column validation error with suggestion (from custom ValueError in validators)
    if field in ("to", "column") and "value_error" in error_type:
        # Extract invalid value from original input
        invalid_value = original_input.get(field, "") if original_input else ""

        # Error message already includes "Coluna inválida" from validator
        # Check if we should add a suggestion
        if invalid_value:
            suggestion = suggest_closest_column(str(invalid_value))
            if suggestion and suggestion != invalid_value:
                return (
                    f"⚠️ Coluna inválida: '{invalid_value}'. Você quis dizer '{suggestion}'?\n"
                    f"Valores válidos: Espera, Produção, Aprovação, Finalizado"
                )

        # Return the error message from validator (already in PT-BR)
        return f"⚠️ {error_msg}"

    # Check if it's an enum/literal validation error (legacy, for non-column fields)
    if "literal_error" in error_type or "enum" in error_type:
        # Extract valid values for this field
        valid_values = extract_valid_enum_values(tool_name, str(field))
        if valid_values:
            return (
                f"⚠️ Valor inválido para '{field}'. "
                f"Valores válidos: {', '.join(valid_values)}"
            )

    # Check if it's a missing field error
    if "missing" in error_type:
        return f"⚠️ Campo obrigatório '{field}' não fornecido."

    # Check if it's a string length error
    if "string_too_long" in error_type:
        max_length = first_error.get("ctx", {}).get("max_length", "?")
        return f"⚠️ Campo '{field}' muito longo. Máximo: {max_length} caracteres."

    # Check if it's a type error
    if "type_error" in error_type or error_type.startswith("type_error."):
        expected_type = first_error.get("msg", "").split(":")[-1].strip()
        return f"⚠️ Tipo inválido para '{field}'. Esperado: {expected_type}."

    # Generic validation error
    return f"⚠️ Erro de validação no campo '{field}': {error_msg}"


def validate_tool_call(tool_call: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """
    Validate tool call input against Pydantic schema.

    Prevents invalid inputs from being queued. Returns validation result
    and error message (if invalid).

    Args:
        tool_call: Tool call dict from LLM with keys:
            - name: Tool name (str)
            - input: Tool arguments (dict)

    Returns:
        Tuple of (is_valid, error_message):
        - (True, None) if valid
        - (False, "error message in PT-BR") if invalid

    Examples:
        >>> tool_call = {"name": "move_card", "input": {"card_ref": "c-123", "to": "APROVACAO"}}
        >>> validate_tool_call(tool_call)
        (False, "⚠️ Valor inválido para 'to'. Valores válidos: Espera, Produção, Aprovação, Finalizado")
    """
    func_name = tool_call.get("name")
    func_args = tool_call.get("input", {})

    # Get validator for this tool
    validator = TOOL_VALIDATORS.get(func_name)
    if not validator:
        # Unknown tool - skip validation (allow for extensibility)
        logger.debug(f"No validator for tool '{func_name}', skipping validation")
        return True, None

    try:
        # Validate using Pydantic model
        validator(**func_args)
        logger.debug(f"✅ Tool call '{func_name}' passed validation")
        return True, None
    except ValidationError as e:
        # Format error in PT-BR with suggestions (pass original input for context)
        error_message = format_validation_error_pt(e, func_name, func_args)
        logger.info(f"❌ Tool call '{func_name}' failed validation: {error_message}")
        return False, error_message


class ApprovalRequest(BaseModel):
    """Request model for approval endpoint."""
    proposal_id: str = Field(..., description="ID of proposal to approve")


class ApprovalResponse(BaseModel):
    """Response model for approval endpoint."""
    success: bool = Field(..., description="Whether approval succeeded")
    job_id: Optional[str] = Field(default=None, description="Job ID if enqueued")
    message: str = Field(..., description="Human-readable message")


# Helper functions
def get_redis_client():
    """Get Redis client for chat operations."""
    try:
        import redis
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        return redis.Redis.from_url(redis_url, decode_responses=True)
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        return None


def check_rate_limit(ip: str) -> bool:
    """
    Check rate limit for IP address.

    Args:
        ip: Client IP address

    Returns:
        True if under limit, False if exceeded
    """
    redis_client = get_redis_client()
    if not redis_client:
        # If Redis unavailable, allow request (fail open)
        logger.warning("Redis unavailable for rate limiting - allowing request")
        return True

    key = f"wf:chat:ratelimit:{ip}"
    try:
        count = redis_client.incr(key)
        if count == 1:
            redis_client.expire(key, 60)  # 60 second window

        if count > WF_RATELIMIT_PER_MIN:
            # Record rate limit hit in metrics
            try:
                from src.core.metrics import record_rate_limit_hit
                record_rate_limit_hit()
            except ImportError:
                pass
            return False

        return True
    except Exception as e:
        logger.error(f"Rate limit check failed: {e}")
        return True  # Fail open


def get_session_history(session_id: str) -> List[Dict[str, Any]]:
    """
    Get conversation history for session.

    Args:
        session_id: Session ID

    Returns:
        List of message dicts
    """
    redis_client = get_redis_client()
    if not redis_client:
        return []

    key = f"wf:chat:hist:{session_id}"
    try:
        messages_json = redis_client.lrange(key, 0, -1)
        messages = [json.loads(msg) for msg in reversed(messages_json)]  # Oldest first
        return messages
    except Exception as e:
        logger.error(f"Failed to get session history: {e}")
        return []


def save_message_to_history(session_id: str, role: str, content: str, tool_calls: Optional[List] = None) -> None:
    """
    Save message to session history.

    Args:
        session_id: Session ID
        role: Message role (user, assistant, system)
        content: Message content
        tool_calls: Optional tool calls
    """
    redis_client = get_redis_client()
    if not redis_client:
        return

    key = f"wf:chat:hist:{session_id}"
    message = {
        "role": role,
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    if tool_calls:
        message["tool_calls"] = tool_calls

    try:
        redis_client.lpush(key, json.dumps(message))
        redis_client.ltrim(key, 0, 49)  # Keep last 50 messages
        redis_client.expire(key, WF_CHAT_TTL_SEC)
    except Exception as e:
        logger.error(f"Failed to save message to history: {e}")


def is_high_risk_action(tool_name: str, tool_input: Dict[str, Any], agent: Optional[str] = None) -> bool:
    """
    Determine if an action requires user confirmation (minimalist policy).

    Minimalist Policy (2025-09-30):
    - Moving card to "Finalizado" → REQUIRES CONFIRMATION
    - Agent name contains "deploy_prod" → REQUIRES CONFIRMATION
    - Everything else → AUTO-EXECUTE

    This reduces approval friction from ~40% to <5% of actions while
    maintaining safety for truly high-risk operations (production deployments).

    Args:
        tool_name: Name of tool being called
        tool_input: Input parameters for the tool
        agent: Optional agent name (for queue_job tool)

    Returns:
        True if high risk (requires confirmation), False otherwise
    """
    # Rule 1: Moving to Finalizado requires confirmation
    # Finalizado = final publication/deployment (high-risk action)
    if tool_name == "move_card":
        to_column = tool_input.get("to_column", "")
        if to_column == "Finalizado":
            logger.info(f"🔒 High-risk: move_card to Finalizado (final publication)")
            return True

    # Rule 2: Agent contains "deploy_prod" requires confirmation
    # Production deployments are high-risk operations
    if agent and "deploy_prod" in agent.lower():
        logger.info(f"🔒 High-risk: agent contains 'deploy_prod' ({agent})")
        return True

    # All other actions auto-execute
    return False


def store_proposal(session_id: str, tool_call: Dict[str, Any], message: str) -> str:
    """
    Store high-risk proposal for approval.

    Args:
        session_id: Session ID
        tool_call: Tool call dict
        message: Human-readable message

    Returns:
        Proposal ID
    """
    redis_client = get_redis_client()
    if not redis_client:
        raise RuntimeError("Redis unavailable - cannot store proposal")

    proposal_id = f"prop-{uuid.uuid4().hex[:8]}"
    key = f"wf:chat:proposal:{proposal_id}"

    proposal = {
        "id": proposal_id,
        "session_id": session_id,
        "tool_call": tool_call,
        "message": message,
        "created_at": datetime.now(timezone.utc).isoformat()
    }

    try:
        redis_client.setex(key, 3600, json.dumps(proposal))  # 1 hour TTL
        logger.info(f"Stored proposal {proposal_id} for session {session_id}")
        return proposal_id
    except Exception as e:
        logger.error(f"Failed to store proposal: {e}")
        raise


def get_proposal(proposal_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve proposal by ID.

    Args:
        proposal_id: Proposal ID

    Returns:
        Proposal dict or None if not found/expired
    """
    redis_client = get_redis_client()
    if not redis_client:
        return None

    key = f"wf:chat:proposal:{proposal_id}"
    try:
        proposal_json = redis_client.get(key)
        if proposal_json:
            return json.loads(proposal_json)
        return None
    except Exception as e:
        logger.error(f"Failed to get proposal: {e}")
        return None


def delete_proposal(proposal_id: str) -> None:
    """Delete proposal after approval/rejection."""
    redis_client = get_redis_client()
    if not redis_client:
        return

    key = f"wf:chat:proposal:{proposal_id}"
    try:
        redis_client.delete(key)
    except Exception as e:
        logger.error(f"Failed to delete proposal: {e}")


def store_pending_confirmation(session_id: str, tool_call: Dict[str, Any], message: str) -> None:
    """
    Store action awaiting confirmation.

    Args:
        session_id: Session ID
        tool_call: Tool call dict from LLM
        message: Human-readable explanation
    """
    redis_client = get_redis_client()
    if not redis_client:
        raise RuntimeError("Redis unavailable - cannot store pending confirmation")

    key = f"wf:chat:pending:{session_id}"

    pending = {
        "tool_call": tool_call,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "state": "awaiting_confirmation"
    }

    try:
        redis_client.setex(key, 600, json.dumps(pending))  # 10 minute TTL
        logger.info(f"📋 Stored pending confirmation for session {session_id}")

        try:
            from src.core.metrics import record_pending_confirmation
            record_pending_confirmation()
        except ImportError:
            pass
    except Exception as e:
        logger.error(f"Failed to store pending confirmation: {e}")
        raise


def get_pending_confirmation(session_id: str, atomic_delete: bool = False) -> Optional[Dict[str, Any]]:
    """
    Retrieve pending confirmation.

    Args:
        session_id: Session ID
        atomic_delete: If True, atomically get and delete (prevents race conditions)

    Returns:
        Pending dict or None if not found/expired
    """
    redis_client = get_redis_client()
    if not redis_client:
        return None

    key = f"wf:chat:pending:{session_id}"
    try:
        if atomic_delete:
            # Use GETDEL for atomic get + delete (prevents double-execution race condition)
            # Requires Redis 6.2.0+, falls back to GET + DEL if not available
            try:
                pending_json = redis_client.getdel(key)
            except AttributeError:
                # Fallback for older Redis clients
                pending_json = redis_client.get(key)
                if pending_json:
                    redis_client.delete(key)
        else:
            # Regular GET (for re-prompting, doesn't delete)
            pending_json = redis_client.get(key)

        if pending_json:
            return json.loads(pending_json)
        return None
    except Exception as e:
        logger.error(f"Failed to get pending confirmation: {e}")
        return None


def delete_pending_confirmation(session_id: str) -> None:
    """Delete pending confirmation after execution/cancellation."""
    redis_client = get_redis_client()
    if not redis_client:
        return

    key = f"wf:chat:pending:{session_id}"
    try:
        redis_client.delete(key)
        logger.info(f"🗑️  Deleted pending confirmation for session {session_id}")
    except Exception as e:
        logger.error(f"Failed to delete pending confirmation: {e}")


def generate_deterministic_job_id(session_id: str, tool_name: str, tool_args: Dict[str, Any]) -> str:
    """
    Generate deterministic job ID using SHA1 hash.

    Creates a reproducible job_id from session + tool_name + sorted args.
    This enables idempotency: same request = same job_id = deduplication.

    Args:
        session_id: User session identifier
        tool_name: Tool/action name (e.g., "create_card")
        tool_args: Tool arguments dict

    Returns:
        Deterministic job_id string (format: "job-{sha1[:12]}")

    Examples:
        >>> generate_deterministic_job_id("sess-123", "create_card", {"title": "Test"})
        "job-a1b2c3d4e5f6"

        >>> # Same inputs = same output (idempotent)
        >>> id1 = generate_deterministic_job_id("s1", "move_card", {"to": "Done"})
        >>> id2 = generate_deterministic_job_id("s1", "move_card", {"to": "Done"})
        >>> assert id1 == id2
    """
    # Stable JSON serialization (sorted keys, no whitespace, UTF-8)
    stable_args = json.dumps(tool_args, sort_keys=True, ensure_ascii=False)

    # Combine components
    payload = f"{session_id}:{tool_name}:{stable_args}"

    # SHA1 hash (sufficient for job IDs, faster than SHA256)
    hash_digest = hashlib.sha1(payload.encode('utf-8')).hexdigest()

    # Return job-{first 12 hex chars} for readability
    return f"job-{hash_digest[:12]}"


def _publish_job_atomic(queue, job: 'Job', events_to_emit: list) -> None:
    """
    Atomically publish job and emit SSE events using Redis pipeline.

    This prevents the scenario where a job is enqueued but events are lost
    due to Redis failure between queue.publish() and emit_*() calls.

    Args:
        queue: JobQueue instance
        job: Job to enqueue
        events_to_emit: List of (emit_func, kwargs) tuples to call with pipe parameter

    Raises:
        Exception: If Redis pipeline execution fails
    """
    from src.core.queue import RedisJobQueue

    if isinstance(queue, RedisJobQueue):
        # Use Redis pipeline for atomicity
        redis_client = queue._client
        pipe = redis_client.pipeline()

        # Add job to queue (via pipeline)
        job_json = json.dumps(job.as_dict())
        pipe.rpush(queue._key, job_json)

        # Emit SSE events (via pipeline)
        for emit_func, kwargs in events_to_emit:
            emit_func(**kwargs, pipe=pipe)

        # Calculate pipeline size: 1 queue push + (3 commands per SSE event: publish + lpush + ltrim)
        pipeline_size = 1 + (len(events_to_emit) * 3)

        # Execute atomically: all or nothing
        try:
            pipe.execute()

            # Record successful pipeline execution
            record_pipeline_execution(
                operation_type="queue_publish_with_sse",
                status="success",
                pipeline_size=pipeline_size
            )
        except Exception as e:
            # Record failed pipeline execution
            record_pipeline_execution(
                operation_type="queue_publish_with_sse",
                status="error",
                pipeline_size=pipeline_size
            )
            raise  # Re-raise to preserve original behavior
    else:
        # Fallback for MemoryJobQueue (no atomicity needed)
        queue.publish(job)
        for emit_func, kwargs in events_to_emit:
            emit_func(**kwargs)


class BoardSummaryUnavailable(RuntimeError):
    """Raised when board summary cannot be generated safely."""

    def __init__(self, reason: str, duration_ms: Optional[float] = None):
        super().__init__(reason)
        self.reason = reason
        self.duration_ms = duration_ms


def execute_summarize_board(scope: Optional[str] = None) -> Dict[str, Any]:
    """Summarize board without performing internal HTTP calls."""

    start = time.perf_counter()

    try:
        board_state = get_board_snapshot()
    except Exception as exc:  # pragma: no cover - depends on Redis availability
        logger.error("Erro ao obter snapshot do board: %s", exc)
        raise BoardSummaryUnavailable("unavailable") from exc

    duration_ms = (time.perf_counter() - start) * 1000
    if duration_ms > SUMMARY_HEALTH_THRESHOLD_MS:
        logger.warning(
            "📊 Summary health-gate acionado: %.1fms (>%.1fms)",
            duration_ms,
            SUMMARY_HEALTH_THRESHOLD_MS,
        )
        raise BoardSummaryUnavailable("slow_store", duration_ms)

    columns: Dict[str, List[Dict[str, Any]]] = board_state.get("columns", {})

    totals_por_coluna = {
        col: len(columns.get(col, []))
        for col in CANONICAL_PT_COLUMNS
    }
    contagem_total = sum(totals_por_coluna.values())

    now = datetime.now(timezone.utc)
    deadline_threshold = now + timedelta(days=7)
    prazos_proximos: List[Dict[str, Any]] = []

    for col_name in CANONICAL_PT_COLUMNS:
        for card in columns.get(col_name, []):
            due_date_str = (
                card.get("due_date")
                or card.get("meta", {}).get("due")
            )
            if not due_date_str:
                continue

            try:
                due_date = datetime.fromisoformat(due_date_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue

            if now <= due_date <= deadline_threshold:
                prazos_proximos.append({
                    "title": card.get("title", "Sem título"),
                    "due_date": due_date_str,
                    "column": col_name,
                    "card_id": card.get("id"),
                })

    gargalos: List[str] = []
    wip_limit = int(os.getenv("WF_WIP_LIMIT", "2"))

    producao_count = totals_por_coluna.get("Produção", 0)
    if producao_count >= wip_limit:
        gargalos.append(f"Produção ({producao_count}/{wip_limit} cards - limite WIP)")

    old_card_threshold_days = 7
    age_threshold = now - timedelta(days=old_card_threshold_days)

    for col_name in CANONICAL_PT_COLUMNS:
        old_cards = 0
        for card in columns.get(col_name, []):
            created_at_str = card.get("created_at")
            if not created_at_str:
                continue
            try:
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
            if created_at <= age_threshold:
                old_cards += 1
        if old_cards > 0:
            gargalos.append(
                f"{col_name} ({old_cards} cards >{old_card_threshold_days} dias)"
            )

    return {
        "totals_por_coluna": totals_por_coluna,
        "contagem_total": contagem_total,
        "prazos_proximos": prazos_proximos,
        "gargalos": gargalos,
        "metrics": {"duration_ms": duration_ms},
    }


def format_summary_response(summary: Dict[str, Any]) -> str:
    """
    Format summarize_board result as concise PT-BR response.

    Produces one-line overview with totals followed by optional sections.

    Args:
        summary: Result from execute_summarize_board()

    Returns:
        Formatted PT-BR string

    Example output:
        Total: 15 · Espera 5 · Produção 2 · Aprovação 3 · Agendado 2 · Finalizado 3
        ⏰ 3 cards com prazo próximo (7 dias)
        ⚠️ Gargalos: Produção (2/2 cards - limite WIP)
    """
    totals = summary.get("totals_por_coluna", {})
    total = summary.get("contagem_total", 0)
    prazos = summary.get("prazos_proximos", [])
    gargalos = summary.get("gargalos", [])
    error = summary.get("error")

    if error:
        return f"⚠️ {error}"

    header_parts = [f"Total: {total}"]
    for col in CANONICAL_PT_COLUMNS:
        header_parts.append(f"{col} {totals.get(col, 0)}")
    header_line = " · ".join(header_parts)

    lines = [header_line]

    if prazos:
        lines.append(f"⏰ {len(prazos)} cards com prazo próximo (7 dias)")

    if gargalos:
        lines.append(f"⚠️ Gargalos: {', '.join(gargalos)}")

    return "\n".join(lines)


def execute_tool_call(tool_call: Dict[str, Any], session_id: str) -> ToolResult:
    """
    Execute a confirmed tool call.

    Args:
        tool_call: Tool call dict
        session_id: Session ID

    Returns:
        Job ID if queued, None otherwise
    """
    func_name = tool_call["name"]
    func_args = tool_call["input"]

    try:
        # Import queue dependencies once
        from src.core.queue import load_default_queue
        from src.core.job import Job

        queue = load_default_queue()

        # Implementation based on tool type
        if func_name == "create_card":
            # Generate deterministic job_id for idempotency
            job_id = generate_deterministic_job_id(session_id, func_name, func_args)

            # Get Redis timestamp to avoid clock skew (single source of truth)
            from src.core.queue import RedisJobQueue
            queued_at = time.time()  # Fallback to local time
            if isinstance(queue, RedisJobQueue):
                try:
                    redis_time = queue._client.time()  # Returns (seconds, microseconds)
                    queued_at = redis_time[0] + redis_time[1] / 1e6
                except Exception as e:
                    # P0 fix: Log clock skew fallback and record metric
                    logger.warning(f"Redis TIME() failed for create_card, using local time (clock skew possible): {e}")
                    record_clock_skew_fallback(location='create_card_execute')

            # Criar novo card via board_operator
            job = Job(
                agent="board_operator",
                payload={
                    "action": "create_card",
                    "title": func_args.get("title", ""),
                    "column": func_args.get("column", "Espera"),
                    "assignees": func_args.get("assignees", []),
                    "tags": func_args.get("tags", []),
                    "due_date": func_args.get("due_date"),
                    "from_chat": True,
                    "session_id": session_id
                },
                job_id=job_id,
                metadata={
                    "queued_at": queued_at,
                    "session_id": session_id,
                    "action": "create_card"
                }
            )

            # Record optimistic job queued metric
            record_optimistic_job_queued(
                action="create_card",
                session_id_present=(session_id is not None)
            )

            # ATOMIC: Enqueue job + emit SSE events in single Redis pipeline
            from src.core.events import emit_job_queued, emit_card_pending
            _publish_job_atomic(queue, job, [
                (emit_job_queued, {"job_id": job_id, "action": "create_card", "args": func_args, "session_id": session_id}),
                (emit_card_pending, {
                    "temp_id": job_id,
                    "title": func_args.get("title", ""),
                    "list_name": func_args.get("column", "Espera"),
                    "meta": {
                        "assignees": func_args.get("assignees", []),
                        "tags": func_args.get("tags", []),
                        "due_date": func_args.get("due_date")
                    },
                    "session_id": session_id
                })
            ])
            logger.info(f"📋 Enfileirado create_card (atomic): título='{func_args.get('title', '')}' → job={job_id}")

            return {
                "kind": "job",
                "job_id": job_id,
                "action": "create_card",
                "args": func_args,
            }

        elif func_name == "move_card":
            # Generate deterministic job_id for idempotency
            job_id = generate_deterministic_job_id(session_id, func_name, func_args)

            # Get Redis timestamp to avoid clock skew (single source of truth)
            from src.core.queue import RedisJobQueue
            queued_at = time.time()  # Fallback to local time
            if isinstance(queue, RedisJobQueue):
                try:
                    redis_time = queue._client.time()  # Returns (seconds, microseconds)
                    queued_at = redis_time[0] + redis_time[1] / 1e6
                except Exception as e:
                    # P0 fix: Log clock skew fallback and record metric
                    logger.warning(f"Redis TIME() failed for move_card, using local time (clock skew possible): {e}")
                    record_clock_skew_fallback(location='move_card_execute')

            # Mover card via board_operator
            job = Job(
                agent="board_operator",
                payload={
                    "action": "move_card",
                    "card_id": func_args.get("card_id", ""),
                    "to_column": func_args.get("to_column", ""),
                    "from_chat": True,
                    "session_id": session_id
                },
                job_id=job_id,
                metadata={
                    "queued_at": queued_at,
                    "session_id": session_id,
                    "action": "move_card"
                }
            )

            # Record optimistic job queued metric
            record_optimistic_job_queued(
                action="move_card",
                session_id_present=(session_id is not None)
            )

            # ATOMIC: Enqueue job + emit SSE events in single Redis pipeline
            from src.core.events import emit_job_queued, emit_card_pending
            _publish_job_atomic(queue, job, [
                (emit_job_queued, {"job_id": job_id, "action": "move_card", "args": func_args, "session_id": session_id}),
                (emit_card_pending, {
                    "temp_id": job_id,
                    "title": f"Moving {func_args.get('card_id', '')}...",
                    "list_name": func_args.get("to_column", ""),
                    "meta": {"original_card_id": func_args.get("card_id", "")},
                    "session_id": session_id
                })
            ])
            logger.info(f"📋 Enfileirado move_card (atomic): {func_args.get('card_id', '')} → {func_args.get('to_column', '')} (job={job_id})")

            return {
                "kind": "job",
                "job_id": job_id,
                "action": "move_card",
                "args": func_args,
            }

        elif func_name == "update_card":
            # Generate deterministic job_id for idempotency
            job_id = generate_deterministic_job_id(session_id, func_name, func_args)

            # Get Redis timestamp to avoid clock skew (single source of truth)
            from src.core.queue import RedisJobQueue
            queued_at = time.time()  # Fallback to local time
            if isinstance(queue, RedisJobQueue):
                try:
                    redis_time = queue._client.time()  # Returns (seconds, microseconds)
                    queued_at = redis_time[0] + redis_time[1] / 1e6
                except Exception as e:
                    # P0 fix: Log clock skew fallback and record metric
                    logger.warning(f"Redis TIME() failed for update_card, using local time (clock skew possible): {e}")
                    record_clock_skew_fallback(location='update_card_execute')

            # Atualizar card via board_operator
            job = Job(
                agent="board_operator",
                payload={
                    "action": "update_card",
                    "card_id": func_args.get("card_id", ""),
                    "fields": func_args.get("fields", {}),
                    "from_chat": True,
                    "session_id": session_id
                },
                job_id=job_id,
                metadata={
                    "queued_at": queued_at,
                    "session_id": session_id,
                    "action": "update_card"
                }
            )

            # Record optimistic job queued metric
            record_optimistic_job_queued(
                action="update_card",
                session_id_present=(session_id is not None)
            )

            # ATOMIC: Enqueue job + emit SSE events in single Redis pipeline
            from src.core.events import emit_job_queued
            _publish_job_atomic(queue, job, [
                (emit_job_queued, {"job_id": job_id, "action": "update_card", "args": func_args, "session_id": session_id})
            ])
            # Note: update_card doesn't need card.pending since it's an in-place update
            # Frontend will wait for card.updated event
            logger.info(f"📋 Enfileirado update_card (atomic): {func_args.get('card_id', '')} (job={job_id})")

            return {
                "kind": "job",
                "job_id": job_id,
                "action": "update_card",
                "args": func_args,
            }

        elif func_name == "summarize_board":
            logger.info(
                "📊 summarize_board chamado: scope=%s",
                func_args.get("scope", "all"),
            )

            summary_scope = func_args.get("scope")
            try:
                summary_data = execute_summarize_board(scope=summary_scope)
                summary_text = format_summary_response(summary_data)
                summary_generated = True
            except BoardSummaryUnavailable as exc:
                fallback_text = "📊 O board está indisponível para resumo agora. Tente novamente em instantes."
                logger.warning(
                    "📊 summarize_board degradado (reason=%s, duration=%.1fms)",
                    exc.reason,
                    getattr(exc, "duration_ms", -1.0),
                )
                try:
                    from src.core.metrics import record_summary_skipped
                    record_summary_skipped()
                except ImportError:
                    pass
                summary_text = fallback_text
                summary_generated = False

            # Registrar auditoria (mesmo sem job)
            log_to_audit(
                session_id,
                "tool_executed",
                {
                    "tool_name": func_name,
                    "job_id": None,
                    "params": func_args,
                    "summary_generated": summary_generated,
                },
            )

            return {
                "kind": "summary",
                "text": summary_text,
            }

        elif func_name == "ingest_email":
            # Ingest email (não enfileira job, retorna proposta inline)
            logger.info(f"📧 ingest_email chamado: {len(func_args.get('raw_text', ''))} caracteres")
            return {
                "kind": "error",
                "error": "📧 Processamento de e-mails ainda não implementado via chat."
            }

        # Log to audit
        log_to_audit(session_id, "tool_executed", {
            "tool_name": func_name,
            "job_id": job_id,
            "params": func_args
        })

        # Note: job_queued and card.pending events are now emitted
        # immediately after queue.publish() for each tool type above
        # (optimistic updates for UI)

    except Exception as e:
        logger.error(f"Failed to execute tool call {func_name}: {e}")
        return {
            "kind": "error",
            "error": f"Não consegui executar {func_name}: {e}",
        }



def execute_tool_call_with_id(
    tool_call: Dict[str, Any],
    session_id: str,
    job_id: str
) -> None:
    """
    Execute a confirmed tool call with pre-generated job ID.

    This variant is used for idempotency-protected confirmations,
    where the job ID must be generated and stored in Redis BEFORE
    queueing the job (to prevent race conditions).

    Args:
        tool_call: Tool call dict with keys: name, input
        session_id: User session ID
        job_id: Pre-generated job ID (for idempotency tracking)

    Returns:
        None (job_id is passed in, not generated)

    Raises:
        Exception: If job queueing fails
    """
    func_name = tool_call["name"]
    func_args = tool_call["input"]

    try:
        # Import queue dependencies once
        from src.core.queue import load_default_queue
        from src.core.job import Job

        queue = load_default_queue()

        # Implementation based on tool type
        if func_name == "create_card":
            # Get Redis timestamp to avoid clock skew (single source of truth)
            from src.core.queue import RedisJobQueue
            queued_at = time.time()  # Fallback to local time
            if isinstance(queue, RedisJobQueue):
                try:
                    redis_time = queue._client.time()  # Returns (seconds, microseconds)
                    queued_at = redis_time[0] + redis_time[1] / 1e6
                except Exception as e:
                    # P0 fix: Log clock skew fallback and record metric
                    logger.warning(f"Redis TIME() failed for create_card (with_id), using local time (clock skew possible): {e}")
                    record_clock_skew_fallback(location='create_card_with_id')

            # Criar novo card via board_operator
            job = Job(
                agent="board_operator",
                payload={
                    "action": "create_card",
                    "title": func_args.get("title", ""),
                    "column": func_args.get("column", "Espera"),
                    "assignees": func_args.get("assignees", []),
                    "tags": func_args.get("tags", []),
                    "due_date": func_args.get("due_date"),
                    "from_chat": True,
                    "session_id": session_id
                },
                job_id=job_id,  # Use pre-generated ID
                metadata={
                    "queued_at": queued_at,
                    "session_id": session_id,
                    "action": "create_card"
                }
            )

            # Record optimistic job queued metric
            record_optimistic_job_queued(
                action="create_card",
                session_id_present=(session_id is not None)
            )

            # ATOMIC: Enqueue job + emit SSE events in single Redis pipeline
            from src.core.events import emit_job_queued, emit_card_pending
            _publish_job_atomic(queue, job, [
                (emit_job_queued, {"job_id": job_id, "action": "create_card", "args": func_args, "session_id": session_id}),
                (emit_card_pending, {
                    "temp_id": job_id,
                    "title": func_args.get("title", ""),
                    "list_name": func_args.get("column", "Espera"),
                    "meta": {
                        "assignees": func_args.get("assignees", []),
                        "tags": func_args.get("tags", []),
                        "due_date": func_args.get("due_date")
                    },
                    "session_id": session_id
                })
            ])
            logger.info(f"📋 Enfileirado create_card (idempotente+atomic): título='{func_args.get('title', '')}' → job={job_id}")

        elif func_name == "move_card":
            # Get Redis timestamp to avoid clock skew (single source of truth)
            from src.core.queue import RedisJobQueue
            queued_at = time.time()  # Fallback to local time
            if isinstance(queue, RedisJobQueue):
                try:
                    redis_time = queue._client.time()  # Returns (seconds, microseconds)
                    queued_at = redis_time[0] + redis_time[1] / 1e6
                except Exception as e:
                    # P0 fix: Log clock skew fallback and record metric
                    logger.warning(f"Redis TIME() failed for move_card (with_id), using local time (clock skew possible): {e}")
                    record_clock_skew_fallback(location='move_card_with_id')

            # Mover card via board_operator
            job = Job(
                agent="board_operator",
                payload={
                    "action": "move_card",
                    "card_id": func_args.get("card_id", ""),
                    "to_column": func_args.get("to_column", ""),
                    "from_chat": True,
                    "session_id": session_id
                },
                job_id=job_id,  # Use pre-generated ID
                metadata={
                    "queued_at": queued_at,
                    "session_id": session_id,
                    "action": "move_card"
                }
            )

            # Record optimistic job queued metric
            record_optimistic_job_queued(
                action="move_card",
                session_id_present=(session_id is not None)
            )

            # ATOMIC: Enqueue job + emit SSE events in single Redis pipeline
            from src.core.events import emit_job_queued, emit_card_pending
            _publish_job_atomic(queue, job, [
                (emit_job_queued, {"job_id": job_id, "action": "move_card", "args": func_args, "session_id": session_id}),
                (emit_card_pending, {
                    "temp_id": job_id,
                    "title": f"Moving {func_args.get('card_id', '')}...",
                    "list_name": func_args.get("to_column", ""),
                    "meta": {"original_card_id": func_args.get("card_id", "")},
                    "session_id": session_id
                })
            ])
            logger.info(f"📋 Enfileirado move_card (idempotente+atomic): {func_args.get('card_id', '')} → {func_args.get('to_column', '')} (job={job_id})")

        elif func_name == "update_card":
            # Get Redis timestamp to avoid clock skew (single source of truth)
            from src.core.queue import RedisJobQueue
            queued_at = time.time()  # Fallback to local time
            if isinstance(queue, RedisJobQueue):
                try:
                    redis_time = queue._client.time()  # Returns (seconds, microseconds)
                    queued_at = redis_time[0] + redis_time[1] / 1e6
                except Exception as e:
                    # P0 fix: Log clock skew fallback and record metric
                    logger.warning(f"Redis TIME() failed for update_card (with_id), using local time (clock skew possible): {e}")
                    record_clock_skew_fallback(location='update_card_with_id')

            # Atualizar card via board_operator
            job = Job(
                agent="board_operator",
                payload={
                    "action": "update_card",
                    "card_id": func_args.get("card_id", ""),
                    "fields": func_args.get("fields", {}),
                    "from_chat": True,
                    "session_id": session_id
                },
                job_id=job_id,  # Use pre-generated ID
                metadata={
                    "queued_at": queued_at,
                    "session_id": session_id,
                    "action": "update_card"
                }
            )

            # Record optimistic job queued metric
            record_optimistic_job_queued(
                action="update_card",
                session_id_present=(session_id is not None)
            )

            # ATOMIC: Enqueue job + emit SSE events in single Redis pipeline
            from src.core.events import emit_job_queued
            _publish_job_atomic(queue, job, [
                (emit_job_queued, {"job_id": job_id, "action": "update_card", "args": func_args, "session_id": session_id})
            ])
            logger.info(f"📋 Enfileirado update_card (idempotente+atomic): {func_args.get('card_id', '')} (job={job_id})")

        elif func_name == "summarize_board":
            # Resumo do board (não enfileira job, responde inline)
            logger.info(f"📊 summarize_board chamado (idempotente): scope={func_args.get('scope', 'all')}")
            return  # Não criar job

        elif func_name == "ingest_email":
            # Ingest email (não enfileira job, retorna proposta inline)
            logger.info(f"📧 ingest_email chamado (idempotente): {len(func_args.get('raw_text', ''))} caracteres")
            return  # Não criar job

        # Log to audit
        log_to_audit(session_id, "tool_executed", {
            "tool_name": func_name,
            "job_id": job_id,
            "params": func_args
        })

        # Note: job_queued and card.pending events are now emitted
        # immediately after queue.publish() for each tool type above
        # (optimistic updates for UI)

    except Exception as e:
        logger.error(f"Failed to execute tool call {func_name} with ID {job_id}: {e}")
        raise


def log_to_audit(sessao: str, acao: str, dados: dict) -> None:
    """
    Log action to audit trail with sanitization and Portuguese field names.

    Uses Redis pipeline for atomic LPUSH + LTRIM + EXPIRE operations.
    Fails open (silent failure) if Redis is unavailable to avoid blocking operations.

    Args:
        sessao: Session ID (PT-BR: "sessao")
        acao: Action type (PT-BR: "acao") - e.g., "user_message", "llm_response", "tool_executed"
        dados: Action data (PT-BR: "dados") - will be sanitized before storage

    Redis Key: wf:audit:recent (LPUSH list)
    Retention: Last 2000 entries, 30-day TTL
    """
    # Validate inputs - don't log garbage
    if not sessao or not acao:
        logger.warning(f"Invalid audit params: sessao={sessao}, acao={acao}")
        return

    redis_client = get_redis_client()
    if not redis_client:
        logger.warning(f"Redis unavailable - audit entry dropped (fail-open): sessao={sessao[:12]}, acao={acao}")
        return

    # Sanitize data before storing (mask sensitive keys, truncate strings)
    sanitized_dados = sanitize_value(dados)

    # Build entry with Portuguese field names
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "sessao": sessao,
        "usuario": "chat",  # Fixed value per spec
        "acao": acao,
        "dados": sanitized_dados
    }

    try:
        # Use pipeline for atomic-ish operations (LPUSH + LTRIM + EXPIRE)
        pipe = redis_client.pipeline()
        pipe.lpush("wf:audit:recent", json.dumps(entry, ensure_ascii=False))
        pipe.ltrim("wf:audit:recent", 0, 1999)  # Keep last 2000 entries (indices 0-1999)
        pipe.expire("wf:audit:recent", 2592000)  # 30 days = 2592000 seconds
        pipe.execute()

        logger.debug(f"✅ Audit logged: sessao={sessao[:12]}, acao={acao}")

    except Exception as e:
        # Fail open - don't raise exception (audit logging should never block operations)
        logger.error(f"Audit logging failed (fail-open): sessao={sessao[:12]}, acao={acao}, error={e}")


def emit_sse_event(kind: str, payload: Dict[str, Any]) -> None:
    """
    Emit Server-Sent Event via Redis pub/sub.

    Args:
        kind: Event kind
        payload: Event payload
    """
    redis_client = get_redis_client()
    if not redis_client:
        return

    event = {
        "ts": int(time.time() * 1000),
        "kind": kind,
        **payload
    }

    try:
        channel = os.getenv("WF_EVENTS_CHANNEL", "wf:events")
        list_key = os.getenv("WF_EVENTS_LIST", "wf:events:recent")

        redis_client.publish(channel, json.dumps(event))
        redis_client.lpush(list_key, json.dumps(event))
        redis_client.ltrim(list_key, 0, 199)  # Keep last 200 events
    except Exception as e:
        logger.error(f"Failed to emit SSE event: {e}")


def validate_response_compliance(message: str, tool_calls: List[Dict[str, Any]], session_id: str) -> Dict[str, Any]:
    """
    Validate that LLM response follows system prompt constraints.

    Monitors compliance with:
    - Line count (target: ≤2 lines)
    - Hedging language (avoid: "vou tentar", "placeholder")
    - Excessive politeness (avoid: "posso ajudar você")
    - Tool call count (target: ≤1 per message)

    Args:
        message: Assistant response message
        tool_calls: List of tool calls
        session_id: Session ID for logging

    Returns:
        Dict with compliance metrics
    """
    lines = [l.strip() for l in message.split('\n') if l.strip()]
    hedging_words = ["vou tentar", "placeholder", "talvez"]
    assistive_phrases = ["posso ajudar você", "adoraria ajudar", "com prazer"]

    compliance = {
        "line_count_ok": len(lines) <= 2,
        "line_count": len(lines),
        "no_hedging": not any(word in message.lower() for word in hedging_words),
        "no_excessive_politeness": not any(phrase in message.lower() for phrase in assistive_phrases),
        "tool_call_count_ok": len(tool_calls) <= 1,
        "tool_call_count": len(tool_calls)
    }

    # Log violations for monitoring
    if not all([compliance["line_count_ok"], compliance["no_hedging"],
                compliance["no_excessive_politeness"], compliance["tool_call_count_ok"]]):
        logger.warning(
            f"System prompt compliance violation - session {session_id[:8]}: "
            f"lines={compliance['line_count']}, "
            f"hedging={not compliance['no_hedging']}, "
            f"tools={compliance['tool_call_count']}"
        )

    return compliance


def get_llm_client():
    """
    Factory function para criar LLM client baseado em WF_LLM_PROVIDER.

    Providers suportados:
    - bedrock: AWS Bedrock Converse API (default)
    - anthropic: Anthropic Direct API

    Sem fallback - se provider inválido ou credenciais ausentes, lança exceção.

    Returns:
        Cliente LLM (BedrockClient ou AnthropicClient)

    Raises:
        ValueError: Se provider inválido ou credenciais ausentes
    """
    provider = os.getenv("WF_LLM_PROVIDER", "bedrock").lower()

    if provider == "bedrock":
        from src.core.llm_client import get_bedrock_client
        logger.info(f"🤖 Usando LLM provider: bedrock")
        return get_bedrock_client()
    elif provider == "anthropic":
        from src.core.llm_client import get_anthropic_client
        logger.info(f"🤖 Usando LLM provider: anthropic")
        return get_anthropic_client()
    else:
        raise ValueError(
            f"Provider LLM inválido: '{provider}'. "
            f"Valores aceitos: 'bedrock', 'anthropic'. "
            f"Configure WF_LLM_PROVIDER."
        )


# Router endpoints
@router.post("", response_model=ChatResponse)
@router.post("/", response_model=ChatResponse)
async def chat(request: Request, payload: Dict[str, Any] = Body(...)) -> ChatResponse:
    """
    Main chat endpoint with LLM integration.

    Processes user message, invokes LLM with tools, handles tool calls,
    and returns response with optional approval requirements.
    """
    try:
        body = ChatRequest(**payload)
    except ValidationError as exc:
        missing = []
        for err in exc.errors():
            loc = err.get("loc", [])
            if not loc:
                continue
            missing.append(str(loc[-1]))

        if not missing:
            missing = ["text"]

        raise HTTPException(
            status_code=422,
            detail={
                "erro": "Requisição inválida",
                "falta": sorted(set(missing)),
            },
        )

    request_start = time.perf_counter()

    def _record_chat_latency() -> None:
        """Record chat latency metric in milliseconds."""
        try:
            from src.core.metrics import record_chat_latency_ms
            latency_ms = (time.perf_counter() - request_start) * 1000
            record_chat_latency_ms(latency_ms)
        except ImportError:
            pass

    # Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "message": f"Limite de {WF_RATELIMIT_PER_MIN} requisições por minuto excedido.",
                "retry_after": 60
            },
            headers={"Retry-After": "60"}
        )

    # Get LLM client and tool schemas (provider-agnostic)
    from src.core.llm_client import TOOL_SCHEMAS
    llm_client = get_llm_client()
    tools = TOOL_SCHEMAS

    # Build conversation history
    raw_history = get_session_history(body.session_id)

    # Filter out extra fields (Anthropic API only accepts role + content)
    # Remove: timestamp, tool_calls, and other metadata
    history = []
    for msg in raw_history:
        clean_msg = {
            "role": msg.get("role"),
            "content": msg.get("content", "")
        }
        # Only keep messages with valid roles (skip system from history)
        if clean_msg["role"] in ["user", "assistant"]:
            history.append(clean_msg)

    # Add user message
    history.append({
        "role": "user",
        "content": body.text
    })

    # Emit user chat message event
    emit_chat_message(role="user", text=body.text, session_id=body.session_id)

    # Check if user is responding to pending confirmation
    pending = get_pending_confirmation(body.session_id)
    if pending:
        # Use robust word-boundary matching with negation detection
        intent = check_confirmation_intent(body.text)

        # Check for affirmative response
        if intent == "affirmative":
            # Atomically retrieve and delete pending confirmation (prevents race condition)
            pending = get_pending_confirmation(body.session_id, atomic_delete=True)
            if not pending:
                # Pending already executed or expired
                assistant_message = "⚠️ A confirmação expirou. Por favor, solicite a ação novamente."
                save_message_to_history(body.session_id, "user", body.text)
                save_message_to_history(body.session_id, "assistant", assistant_message)
                emit_chat_message(role="assistant", text=assistant_message, session_id=body.session_id)
                _record_chat_latency()
                return ChatResponse(
                    message=assistant_message,
                    role="assistant",
                    tool_calls=[],
                    requires_approval=False,
                    proposal_id=None,
                    session_id=body.session_id
                )

            # Extract tool call from pending confirmation
            tool_call = pending["tool_call"]

            # Check idempotency BEFORE execution (prevents double-click)
            existing_job_id = check_confirmation_idempotency(body.session_id, tool_call)
            if existing_job_id:
                # Already confirmed previously (duplicate detected)
                assistant_message = f"ℹ️ Já confirmado antes. Job: {existing_job_id[:12]}"
                save_message_to_history(body.session_id, "user", body.text)
                save_message_to_history(body.session_id, "assistant", assistant_message)

                try:
                    from src.core.metrics import record_confirmation
                    record_confirmation("duplicate")
                except ImportError:
                    pass

                # Emit SSE event for duplicate confirmation
                emit_sse_event("confirmation_already_executed", {
                    "session_id": body.session_id,
                    "job_id": existing_job_id,
                    "tool_name": tool_call["name"]
                })
                emit_chat_message(role="assistant", text=assistant_message, session_id=body.session_id)

                _record_chat_latency()
                return ChatResponse(
                    message=assistant_message,
                    role="assistant",
                    tool_calls=[],
                    requires_approval=False,
                    proposal_id=None,
                    session_id=body.session_id
                )

            # Execute with idempotency protection
            try:
                # Generate job ID BEFORE marking idempotency
                # (so we can store it in Redis for future duplicate checks)
                job_id = f"chat-{uuid.uuid4().hex[:8]}"

                # Atomically claim execution slot (SETNX)
                if not mark_confirmation_executed(body.session_id, tool_call, job_id):
                    # Lost the race - another process already confirmed
                    # Retrieve the winning job ID
                    existing_job_id = check_confirmation_idempotency(body.session_id, tool_call)
                    assistant_message = (
                        f"ℹ️ Confirmação processada simultaneamente. "
                        f"Job ID: `{(existing_job_id or job_id)[:12]}`\n\n"
                        f"Nenhuma ação adicional foi executada (proteção contra duplicação)."
                    )
                    save_message_to_history(body.session_id, "user", body.text)
                    save_message_to_history(body.session_id, "assistant", assistant_message)

                    try:
                        from src.core.metrics import record_confirmation
                        record_confirmation("duplicate")
                    except ImportError:
                        pass

                    emit_sse_event("confirmation_already_executed", {
                        "session_id": body.session_id,
                        "job_id": existing_job_id or job_id,
                        "tool_name": tool_call["name"]
                    })
                    emit_chat_message(role="assistant", text=assistant_message, session_id=body.session_id)

                    _record_chat_latency()
                    return ChatResponse(
                        message=assistant_message,
                        role="assistant",
                        tool_calls=[],
                        requires_approval=False,
                        proposal_id=None,
                        session_id=body.session_id
                    )

                # Won the race - safe to execute
                # Use execute_tool_call_with_id to ensure job ID consistency
                execute_tool_call_with_id(tool_call, body.session_id, job_id)

                assistant_message = "✅ Confirmado! Ação executada."
                if job_id:
                    assistant_message += f" Job ID: `{job_id[:12]}`"

                save_message_to_history(body.session_id, "user", body.text)
                save_message_to_history(body.session_id, "assistant", assistant_message)

                try:
                    from src.core.metrics import record_confirmation
                    record_confirmation("accepted")
                except ImportError:
                    pass

                emit_sse_event("confirmation_accepted", {
                    "session_id": body.session_id,
                    "job_id": job_id,
                    "tool_name": tool_call["name"]
                })

                # Log confirmation acceptance to audit trail
                log_to_audit(body.session_id, "confirmation_accepted", {
                    "tool_name": tool_call["name"],
                    "job_id": job_id
                })

                emit_chat_message(role="assistant", text=assistant_message, session_id=body.session_id)

                _record_chat_latency()
                return ChatResponse(
                    message=assistant_message,
                    role="assistant",
                    tool_calls=[],
                    requires_approval=False,
                    proposal_id=None,
                    session_id=body.session_id
                )
            except Exception as e:
                logger.error(f"Failed to execute confirmed action: {e}")
                error_msg = f"❌ Erro ao executar: {str(e)}"
                save_message_to_history(body.session_id, "user", body.text)
                save_message_to_history(body.session_id, "assistant", error_msg)
                emit_chat_message(role="assistant", text=error_msg, session_id=body.session_id)
                _record_chat_latency()
                return ChatResponse(
                    message=error_msg,
                    role="assistant",
                    tool_calls=[],
                    requires_approval=False,
                    proposal_id=None,
                    session_id=body.session_id
                )

        # Check for negative response
        elif intent == "negative":
            delete_pending_confirmation(body.session_id)

            assistant_message = "Cancelado."
            save_message_to_history(body.session_id, "user", body.text)
            save_message_to_history(body.session_id, "assistant", assistant_message)

            try:
                from src.core.metrics import record_confirmation
                record_confirmation("rejected")
            except ImportError:
                pass

            emit_sse_event("confirmation_rejected", {
                "session_id": body.session_id,
                "tool_name": pending["tool_call"]["name"]
            })

            # Log confirmation rejection to audit trail
            log_to_audit(body.session_id, "confirmation_rejected", {
                "tool_name": pending["tool_call"]["name"]
            })

            emit_chat_message(role="assistant", text=assistant_message, session_id=body.session_id)

            _record_chat_latency()
            return ChatResponse(
                message=assistant_message,
                role="assistant",
                tool_calls=[],
                requires_approval=False,
                proposal_id=None,
                session_id=body.session_id
            )

        # Ambiguous response - short re-prompt (repeat original)
        else:
            assistant_message = "⚠️ Ação de alto risco. Diga 'sim' para confirmar ou 'não' para cancelar."
            save_message_to_history(body.session_id, "user", body.text)
            save_message_to_history(body.session_id, "assistant", assistant_message)
            emit_chat_message(role="assistant", text=assistant_message, session_id=body.session_id)

            _record_chat_latency()
            return ChatResponse(
                message=assistant_message,
                role="assistant",
                tool_calls=[],
                requires_approval=True,
                proposal_id=None,
                session_id=body.session_id
            )

    # Save user message to history
    save_message_to_history(body.session_id, "user", body.text)

    # Log user message to audit trail
    log_to_audit(body.session_id, "user_message", {
        "texto": body.text[:180]  # Truncate to 180 chars for audit
    })

    # Validate token count before LLM call (prevent cost explosions)
    total_text = "\n".join([msg.get("content", "") for msg in history])
    estimated_tokens = estimate_token_count(total_text)

    if estimated_tokens > WF_TOKEN_INPUT_CAP:
        logger.warning(
            f"Token cap exceeded: {estimated_tokens} > {WF_TOKEN_INPUT_CAP} "
            f"(session: {body.session_id[:12]})"
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": "token_limit_exceeded",
                "message": f"A conversa está muito longa ({estimated_tokens} tokens estimados). "
                          f"Limite: {WF_TOKEN_INPUT_CAP} tokens. "
                          f"Por favor, inicie uma nova sessão ou reduza o tamanho da mensagem.",
                "estimated_tokens": estimated_tokens,
                "token_cap": WF_TOKEN_INPUT_CAP
            }
        )

    # Record user message metric
    try:
        from src.core.metrics import record_chat_message
        record_chat_message("user", "anthropic")
    except ImportError:
        pass

    # ------------------------------------------------------------------
    # Resumo direto (evita round-trip no LLM e timeouts desnecessários)
    # ------------------------------------------------------------------
    summary_intent_detected = check_summary_intent(body.text)
    if summary_intent_detected:
        logger.info("📊 Summary intent direto para sessão %s", body.session_id[:12])

        try:
            summary_data = execute_summarize_board(scope=None)
            summary_message = format_summary_response(summary_data)

            save_message_to_history(body.session_id, "assistant", summary_message)
            emit_chat_message(role="assistant", text=summary_message, session_id=body.session_id)

            try:
                from src.core.metrics import record_chat_request
                record_chat_request("anthropic", "summary_direct", 0.0)
            except ImportError:
                pass

            _record_chat_latency()
            return ChatResponse(
                message=summary_message,
                role="assistant",
                tool_calls=[],
                requires_approval=False,
                proposal_id=None,
                session_id=body.session_id
            )
        except BoardSummaryUnavailable as exc:
            fallback_message = "📊 O board está indisponível para resumo agora. Tente novamente em instantes."

            save_message_to_history(body.session_id, "assistant", fallback_message)
            emit_chat_message(role="assistant", text=fallback_message, session_id=body.session_id)

            try:
                from src.core.metrics import record_chat_request, record_summary_skipped
                record_chat_request("anthropic", "summary_failed", 0.0)
                record_summary_skipped()
            except ImportError:
                pass

            logger.warning(
                "📊 Resumo direto degradado (reason=%s, duration=%.1fms)",
                exc.reason,
                getattr(exc, "duration_ms", -1.0),
            )

            _record_chat_latency()
            return ChatResponse(
                message=fallback_message,
                role="assistant",
                tool_calls=[],
                requires_approval=False,
                proposal_id=None,
                session_id=body.session_id
            )
        except Exception as exc:  # pragma: no cover - unexpected edge cases
            logger.error("⚠️ Falha ao gerar resumo direto: %s", exc)
            fallback_message = "📊 O board está indisponível para resumo agora. Tente novamente em instantes."

            save_message_to_history(body.session_id, "assistant", fallback_message)
            emit_chat_message(role="assistant", text=fallback_message, session_id=body.session_id)

            try:
                from src.core.metrics import record_chat_request, record_summary_skipped
                record_chat_request("anthropic", "summary_failed", 0.0)
                record_summary_skipped()
            except ImportError:
                pass

            _record_chat_latency()
            return ChatResponse(
                message=fallback_message,
                role="assistant",
                tool_calls=[],
                requires_approval=False,
                proposal_id=None,
                session_id=body.session_id
            )

    # Call LLM with timing and 20s timeout
    start_time = time.time()
    try:
        # Get system prompt (A/B tested) - only for first message
        if len(history) == 1:  # Only user message
            system_prompt_content = get_system_prompt_for_session(body.session_id)
            # Prepend system message temporarily for LLM call
            llm_messages = [{"role": "system", "content": system_prompt_content}] + history
        else:
            llm_messages = history

        summary_intent_detected = False  # já tratado acima

        # Wrap LLM call with 20s timeout
        try:
            llm_response = await asyncio.wait_for(
                asyncio.to_thread(llm_client.chat, messages=llm_messages, tools=tools),
                timeout=20.0
            )
        except asyncio.TimeoutError:
            # LLM took too long - return fallback message
            logger.warning(f"LLM timeout after 20s for session {body.session_id[:12]}")

            fallback_message = "Recebi seu pedido e enfileirei a ação. Vou atualizando aqui."

            # Save messages to history
            save_message_to_history(body.session_id, "user", body.text)
            save_message_to_history(body.session_id, "assistant", fallback_message)

            # Emit timeout event
            emit_sse_event("chat_timeout", {
                "session_id": body.session_id,
                "timeout_seconds": 20
            })

            # Emit assistant chat message via SSE
            emit_chat_message(role="assistant", text=fallback_message, session_id=body.session_id)

            # Record timeout metric
            try:
                from src.core.metrics import record_chat_request
                record_chat_request("anthropic", "timeout", 20.0)
            except ImportError:
                pass

            _record_chat_latency()
            return ChatResponse(
                message=fallback_message,
                role="assistant",
                tool_calls=[],
                requires_approval=False,
                proposal_id=None,
                session_id=body.session_id
            )

        # Calculate latency
        latency = time.time() - start_time

        # Record success metrics
        try:
            from src.core.metrics import record_chat_request
            record_chat_request("anthropic", "success", latency)
        except ImportError:
            pass
    except Exception as e:
        # Calculate latency even on error
        latency = time.time() - start_time

        # Record error metrics
        try:
            from src.core.metrics import record_chat_request
            record_chat_request("anthropic", "error", latency)
        except ImportError:
            pass

        logger.error(f"LLM error: {e}")
        emit_sse_event("chat_error", {
            "session_id": body.session_id,
            "error_type": "llm_api_error",
            "message": f"Erro ao processar mensagem: {str(e)}"
        })

        fallback_message = "Não consegui processar agora. Já registrei e vou tentar novamente."

        save_message_to_history(body.session_id, "assistant", fallback_message)
        emit_chat_message(role="assistant", text=fallback_message, session_id=body.session_id)

        _record_chat_latency()
        return ChatResponse(
            message=fallback_message,
            role="assistant",
            tool_calls=[],
            requires_approval=False,
            proposal_id=None,
            session_id=body.session_id
        )

    # Process response (Anthropic format)
    assistant_message = llm_response.get("text", "")
    tool_calls = llm_response.get("tool_calls", [])

    # Log LLM response to audit trail
    log_to_audit(body.session_id, "llm_response", {
        "texto_truncado": assistant_message[:180],  # Truncate to 180 chars
        "tool_calls": len(tool_calls)  # Count of tool calls
    })

    # Determine if approval required
    requires_approval = False
    proposal_id = None

    if tool_calls:
        for tool_call in tool_calls:
            # Anthropic format: {"id": "toolu_xxx", "name": "...", "input": {...}}
            func_name = tool_call["name"]
            func_args = tool_call["input"]

            # ⭐ VALIDATION: Check tool call against schema BEFORE any execution
            is_valid, validation_error = validate_tool_call(tool_call)
            if not is_valid:
                # Return validation error immediately (conversational repair)
                assistant_message = validation_error
                save_message_to_history(body.session_id, "user", body.text)
                save_message_to_history(body.session_id, "assistant", assistant_message)

                # Emit validation_error event for monitoring
                emit_sse_event("validation_error", {
                    "session_id": body.session_id,
                    "tool_name": func_name,
                    "error": validation_error,
                    "invalid_input": func_args
                })

                # Log to audit
                log_to_audit(body.session_id, "validation_failed", {
                    "tool_name": func_name,
                    "error": validation_error,
                    "params": func_args
                })

                emit_chat_message(role="assistant", text=assistant_message, session_id=body.session_id)

                _record_chat_latency()
                return ChatResponse(
                    message=assistant_message,
                    role="assistant",
                    tool_calls=[],
                    requires_approval=False,
                    proposal_id=None,
                    session_id=body.session_id
                )

            # Check if high risk (minimalist policy: only Finalizado moves and deploy_prod agents)
            agent = func_args.get("agent") if func_name == "queue_job" else None
            if is_high_risk_action(func_name, func_args, agent):
                requires_approval = True

                # Build confirmation message in Portuguese (standard format)
                confirmation_message = (
                    f"{assistant_message}\n\n"
                    "⚠️ Ação de alto risco. Diga 'sim' para confirmar ou 'não' para cancelar."
                )

                # Store pending confirmation
                store_pending_confirmation(body.session_id, tool_call, confirmation_message)

                # Update assistant message to include confirmation prompt
                assistant_message = confirmation_message

                # Emit pending_confirmation event (tipado)
                from src.core.events import emit_pending_confirmation
                emit_pending_confirmation(
                    token=generate_confirmation_idempotency_key(body.session_id, tool_call),
                    summary=confirmation_message,
                    session_id=body.session_id
                )

                # Log to audit
                log_to_audit(body.session_id, "confirmation_requested", {
                    "tool_name": func_name,
                    "params": func_args,
                    "risk_level": "high"
                })

                # Record metric
                try:
                    from src.core.metrics import record_chat_tool_call
                    record_chat_tool_call(func_name, "anthropic")
                except ImportError:
                    pass

                break  # Only handle first high-risk tool call per message

        # If not high risk, execute tool calls immediately (LOW RISK path)
        if not requires_approval:
            executed_results: List[ToolResult] = []
            summary_chunks: List[str] = []
            error_chunks: List[str] = []

            for tool_call in tool_calls:
                func_name = tool_call["name"]

                # Execute tool call imediatamente (enfileira job + emite SSE)
                try:
                    tool_result = execute_tool_call(tool_call, body.session_id)

                    executed_results.append(tool_result)
                    kind = tool_result.get("kind")

                    if kind == "summary" and "text" in tool_result:
                        summary_chunks.append(tool_result["text"])
                        logger.info(
                            "📊 summarize_board inline concluído para sessão %s",
                            body.session_id[:12]
                        )
                    elif kind == "job" and "job_id" in tool_result:
                        logger.info(
                            "✅ Low-risk tool executed: %s → job=%s",
                            func_name,
                            tool_result.get("job_id", "?"),
                        )
                    elif kind == "error" and "error" in tool_result:
                        error_chunks.append(tool_result["error"])
                        logger.warning(
                            "⚠️ Ferramenta %s retornou erro inline: %s",
                            func_name,
                            tool_result["error"],
                        )
                except Exception as e:
                    logger.error(f"❌ Failed to execute {func_name}: {e}")
                    # Continue executando outras tools (não falha toda a requisição)

                # Record metrics
                try:
                    from src.core.metrics import record_chat_tool_call
                    record_chat_tool_call(func_name, "anthropic")
                except ImportError:
                    pass

            # Anexar resumos gerados inline (se houver)
            if summary_chunks:
                summary_text = "\n\n".join(summary_chunks)
                assistant_message = (
                    f"{assistant_message}\n\n{summary_text}"
                    if assistant_message
                    else summary_text
                )

            if error_chunks:
                error_text = "\n".join(f"⚠️ {msg}" for msg in error_chunks)
                assistant_message = (
                    f"{assistant_message}\n\n{error_text}"
                    if assistant_message
                    else error_text
                )

            job_ids = [res.get("job_id") for res in executed_results if res.get("kind") == "job" and res.get("job_id")]
            if job_ids:
                job_ids_str = ", ".join(f"`{jid}`" for jid in job_ids)
                assistant_message = (
                    f"{assistant_message}\n\n✅ Job(s) enfileirado(s): {job_ids_str}"
                    if assistant_message
                    else f"✅ Job(s) enfileirado(s): {job_ids_str}"
                )

    # Save assistant message to history
    save_message_to_history(body.session_id, "assistant", assistant_message, tool_calls if tool_calls else None)

    # Record assistant message metric
    try:
        from src.core.metrics import record_chat_message
        record_chat_message("assistant", "anthropic")
    except ImportError:
        pass

    # Emit assistant chat message event
    emit_chat_message(role="assistant", text=assistant_message, session_id=body.session_id)

    _record_chat_latency()
    return ChatResponse(
        message=assistant_message,
        role="assistant",
        tool_calls=tool_calls,
        requires_approval=requires_approval,
        proposal_id=proposal_id,
        session_id=body.session_id
    )


@router.post("/approve", response_model=ApprovalResponse)
async def approve(request: Request, body: ApprovalRequest = Body(...)) -> ApprovalResponse:
    """
    Approve a high-risk proposal and execute the action.

    Retrieves proposal from Redis, enqueues job via existing queue infrastructure,
    and emits approval events.

    Idempotent: Multiple approvals of the same proposal return the original job_id.
    """
    # Check for duplicate approval (idempotency)
    redis_client = get_redis_client()
    if redis_client:
        idempotency_key = f"wf:approval:{body.proposal_id}"
        existing_job_id = redis_client.get(idempotency_key)

        if existing_job_id:
            logger.info(
                f"Duplicate approval detected for proposal {body.proposal_id}, "
                f"returning original job_id: {existing_job_id}"
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "already_approved",
                    "message": "Esta proposta já foi aprovada anteriormente.",
                    "job_id": existing_job_id,
                    "proposal_id": body.proposal_id
                }
            )

    # Get proposal
    proposal = get_proposal(body.proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposta não encontrada ou expirada")

    # Extract tool call
    tool_call = proposal["tool_call"]
    func_name = tool_call["function"]["name"]
    func_args = tool_call["function"]["arguments"]

    # Execute based on tool
    job_id = None

    try:
        if func_name == "propose_move":
            # Move card to column via existing cockpit infrastructure
            # Import dynamically to avoid circular imports
            import sys
            import os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

            # Use existing queue_job function from cockpit
            # For now, queue a job for the appropriate agent
            card_id = func_args["card_id"]
            to_column = func_args["to_column"]

            # Map column to agent
            column_agent_map = {
                "In Progress": "task_starter",
                "Waiting Approval": "review_requester",
                "Scheduled": "content_approver",
                "Published": "content_publisher"
            }

            agent = column_agent_map.get(to_column, "echo")

            # Queue job (simplified for now)
            from src.core.queue import load_default_queue
            from src.core.job import Job

            queue = load_default_queue()
            job = Job(
                agent=agent,
                payload={
                    "card_id": card_id,
                    "to_column": to_column,
                    "from_chat": True,
                    "proposal_id": body.proposal_id
                },
                job_id=f"chat-{uuid.uuid4().hex[:8]}"
            )
            queue.publish(job)
            job_id = job.job_id

        elif func_name == "queue_job":
            # Direct job queueing
            from src.core.queue import load_default_queue
            from src.core.job import Job

            queue = load_default_queue()
            job = Job(
                agent=func_args["agent"],
                payload={
                    **func_args["payload"],
                    "from_chat": True,
                    "proposal_id": body.proposal_id
                },
                job_id=f"chat-{uuid.uuid4().hex[:8]}"
            )
            queue.publish(job)
            job_id = job.job_id

        elif func_name == "bulk_from_email":
            # Bulk card creation (simplified - would need full implementation)
            # For now, just acknowledge
            logger.info(f"Bulk creation approved: {func_args}")
            job_id = f"chat-bulk-{uuid.uuid4().hex[:8]}"

    except Exception as e:
        logger.error(f"Failed to execute approved action: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao executar ação: {str(e)}")

    # Store approval in Redis for idempotency (24 hour TTL)
    if redis_client and job_id:
        idempotency_key = f"wf:approval:{body.proposal_id}"
        redis_client.setex(idempotency_key, WF_CHAT_TTL_SEC, job_id)

    # Delete proposal
    delete_proposal(body.proposal_id)

    # Log to audit
    log_to_audit(proposal["session_id"], "approval", {
        "proposal_id": body.proposal_id,
        "tool_name": func_name,
        "job_id": job_id,
        "result": "approved"
    })

    # Emit approval event
    emit_sse_event("approval", {
        "proposal_id": body.proposal_id,
        "job_id": job_id,
        "message": "Proposta aprovada. Job enfileirado para execução."
    })

    # Emit job_queued event if job was created
    if job_id:
        emit_sse_event("job_queued", {
            "job_id": job_id,
            "agent": func_args.get("agent") or "unknown",
            "from_chat": True
        })

    return ApprovalResponse(
        success=True,
        job_id=job_id,
        message="Proposta aprovada e job enfileirado"
    )


@router.get("/history")
async def get_history(session_id: str) -> Dict[str, Any]:
    """
    Get conversation history for a session.

    Args:
        session_id: Session ID

    Returns:
        Dict with messages array
    """
    messages = get_session_history(session_id)
    return {
        "session_id": session_id,
        "messages": messages,
        "count": len(messages)
    }


__all__ = ["router"]
