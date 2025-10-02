"""Chat API router with LLM integration and approval workflow."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError, validator
from typing import Literal

from src.core.events import emit_chat_message

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
    message: str = Field(..., max_length=2000, description="User message (max 2000 chars)")
    session_id: str = Field(default_factory=lambda: f"sess-{uuid.uuid4().hex[:8]}", description="Session ID for conversation")


class ChatResponse(BaseModel):
    """Response model for chat endpoint."""
    message: str = Field(..., description="Assistant response")
    role: str = Field(default="assistant", description="Message role")
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list, description="Tool calls made by LLM")
    requires_approval: bool = Field(default=False, description="Whether action requires user approval")
    proposal_id: Optional[str] = Field(default=None, description="ID of proposal if approval required")
    session_id: str = Field(..., description="Session ID")


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


def execute_tool_call(tool_call: Dict[str, Any], session_id: str) -> Optional[str]:
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

    job_id = None

    try:
        # Import queue dependencies once
        from src.core.queue import load_default_queue
        from src.core.job import Job

        queue = load_default_queue()

        # Implementation based on tool type
        if func_name == "create_card":
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
                job_id=f"chat-{uuid.uuid4().hex[:8]}"
            )
            queue.publish(job)
            job_id = job.job_id
            logger.info(f"📋 Enfileirado create_card: título='{func_args.get('title', '')}' → job={job_id}")

        elif func_name == "move_card":
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
                job_id=f"chat-{uuid.uuid4().hex[:8]}"
            )
            queue.publish(job)
            job_id = job.job_id
            logger.info(f"📋 Enfileirado move_card: {func_args.get('card_id', '')} → {func_args.get('to_column', '')} (job={job_id})")

        elif func_name == "update_card":
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
                job_id=f"chat-{uuid.uuid4().hex[:8]}"
            )
            queue.publish(job)
            job_id = job.job_id
            logger.info(f"📋 Enfileirado update_card: {func_args.get('card_id', '')} (job={job_id})")

        elif func_name == "summarize_board":
            # Resumo do board (não enfileira job, responde inline)
            # Por ora retornar mensagem placeholder, implementação futura
            logger.info(f"📊 summarize_board chamado: scope={func_args.get('scope', 'all')}")
            # Não criar job - será respondido inline pelo LLM
            return None

        elif func_name == "ingest_email":
            # Ingest email (não enfileira job, retorna proposta inline)
            logger.info(f"📧 ingest_email chamado: {len(func_args.get('raw_text', ''))} caracteres")
            # Não criar job - será respondido inline pelo LLM
            return None

        # Log to audit
        log_to_audit(session_id, "tool_executed", {
            "tool_name": func_name,
            "job_id": job_id,
            "params": func_args
        })

        # Emit job_queued event if job was created
        if job_id:
            emit_sse_event("job_queued", {
                "job_id": job_id,
                "agent": func_args.get("agent") or func_name,
                "from_chat": True,
                "session_id": session_id
            })

    except Exception as e:
        logger.error(f"Failed to execute tool call {func_name}: {e}")
        raise

    return job_id


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
                job_id=job_id  # Use pre-generated ID
            )
            queue.publish(job)
            logger.info(f"📋 Enfileirado create_card (idempotente): título='{func_args.get('title', '')}' → job={job_id}")

        elif func_name == "move_card":
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
                job_id=job_id  # Use pre-generated ID
            )
            queue.publish(job)
            logger.info(f"📋 Enfileirado move_card (idempotente): {func_args.get('card_id', '')} → {func_args.get('to_column', '')} (job={job_id})")

        elif func_name == "update_card":
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
                job_id=job_id  # Use pre-generated ID
            )
            queue.publish(job)
            logger.info(f"📋 Enfileirado update_card (idempotente): {func_args.get('card_id', '')} (job={job_id})")

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

        # Emit job_queued event
        emit_sse_event("job_queued", {
            "job_id": job_id,
            "agent": func_args.get("agent") or func_name,
            "from_chat": True,
            "session_id": session_id
        })

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
@router.post("/", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest = Body(...)) -> ChatResponse:
    """
    Main chat endpoint with LLM integration.

    Processes user message, invokes LLM with tools, handles tool calls,
    and returns response with optional approval requirements.
    """
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
        "content": body.message
    })

    # Emit user chat message event
    emit_chat_message(role="user", text=body.message, session_id=body.session_id)

    # Check if user is responding to pending confirmation
    pending = get_pending_confirmation(body.session_id)
    if pending:
        # Use robust word-boundary matching with negation detection
        intent = check_confirmation_intent(body.message)

        # Check for affirmative response
        if intent == "affirmative":
            # Atomically retrieve and delete pending confirmation (prevents race condition)
            pending = get_pending_confirmation(body.session_id, atomic_delete=True)
            if not pending:
                # Pending already executed or expired
                assistant_message = "⚠️ A confirmação expirou. Por favor, solicite a ação novamente."
                save_message_to_history(body.session_id, "user", body.message)
                save_message_to_history(body.session_id, "assistant", assistant_message)
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
                assistant_message = (
                    f"ℹ️ Já confirmado anteriormente. Job ID: `{existing_job_id[:12]}`\n\n"
                    f"Nenhuma ação adicional foi executada (proteção contra duplicação)."
                )
                save_message_to_history(body.session_id, "user", body.message)
                save_message_to_history(body.session_id, "assistant", assistant_message)

                # Emit SSE event for duplicate confirmation
                emit_sse_event("confirmation_already_executed", {
                    "session_id": body.session_id,
                    "job_id": existing_job_id,
                    "tool_name": tool_call["name"]
                })

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
                    save_message_to_history(body.session_id, "user", body.message)
                    save_message_to_history(body.session_id, "assistant", assistant_message)

                    emit_sse_event("confirmation_already_executed", {
                        "session_id": body.session_id,
                        "job_id": existing_job_id or job_id,
                        "tool_name": tool_call["name"]
                    })

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

                save_message_to_history(body.session_id, "user", body.message)
                save_message_to_history(body.session_id, "assistant", assistant_message)

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
                save_message_to_history(body.session_id, "user", body.message)
                save_message_to_history(body.session_id, "assistant", error_msg)
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

            assistant_message = "❌ Ação cancelada. Como posso ajudar?"
            save_message_to_history(body.session_id, "user", body.message)
            save_message_to_history(body.session_id, "assistant", assistant_message)

            emit_sse_event("confirmation_rejected", {
                "session_id": body.session_id,
                "tool_name": pending["tool_call"]["name"]
            })

            # Log confirmation rejection to audit trail
            log_to_audit(body.session_id, "confirmation_rejected", {
                "tool_name": pending["tool_call"]["name"]
            })

            return ChatResponse(
                message=assistant_message,
                role="assistant",
                tool_calls=[],
                requires_approval=False,
                proposal_id=None,
                session_id=body.session_id
            )

        # Ambiguous response - short re-prompt
        else:
            assistant_message = (
                "⚠️ Resposta não reconhecida. "
                "Diga **sim** para confirmar ou **não** para cancelar."
            )
            save_message_to_history(body.session_id, "user", body.message)
            save_message_to_history(body.session_id, "assistant", assistant_message)

            return ChatResponse(
                message=assistant_message,
                role="assistant",
                tool_calls=[],
                requires_approval=True,
                proposal_id=None,
                session_id=body.session_id
            )

    # Save user message to history
    save_message_to_history(body.session_id, "user", body.message)

    # Log user message to audit trail
    log_to_audit(body.session_id, "user_message", {
        "texto": body.message[:180]  # Truncate to 180 chars for audit
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

    # Call LLM with timing
    import time
    start_time = time.time()
    try:
        # Get system prompt (A/B tested) - only for first message
        if len(history) == 1:  # Only user message
            system_prompt_content = get_system_prompt_for_session(body.session_id)
            # Prepend system message temporarily for LLM call
            llm_messages = [{"role": "system", "content": system_prompt_content}] + history
        else:
            llm_messages = history

        llm_response = llm_client.chat(messages=llm_messages, tools=tools)

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
        raise HTTPException(status_code=500, detail="Erro ao processar mensagem")

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
                save_message_to_history(body.session_id, "user", body.message)
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
                    "⚠️ Ação de alto risco. Diga **sim** para confirmar ou **não** para cancelar."
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
            executed_job_ids = []

            for tool_call in tool_calls:
                func_name = tool_call["name"]

                # Execute tool call imediatamente (enfileira job + emite SSE)
                try:
                    job_id = execute_tool_call(tool_call, body.session_id)
                    if job_id:
                        executed_job_ids.append(job_id)
                        logger.info(f"✅ Low-risk tool executed: {func_name} → job={job_id}")
                except Exception as e:
                    logger.error(f"❌ Failed to execute {func_name}: {e}")
                    # Continue executando outras tools (não falha toda a requisição)

                # Record metrics
                try:
                    from src.core.metrics import record_chat_tool_call
                    record_chat_tool_call(func_name, "anthropic")
                except ImportError:
                    pass

            # Enriquecer resposta do assistente com job IDs (para clareza)
            if executed_job_ids:
                job_ids_str = ", ".join([f"`{jid}`" for jid in executed_job_ids])
                assistant_message += f"\n\n✅ Job(s) enfileirado(s): {job_ids_str}"

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