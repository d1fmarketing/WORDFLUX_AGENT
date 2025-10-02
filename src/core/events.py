"""Unified SSE event system for WordFlux Cockpit.

This module provides a type-safe, centralized event emission system for Server-Sent Events (SSE).
All events are emitted to Redis pub/sub for real-time streaming and stored in a rolling history list.

Event Types:
    - ChatMessageEvent: Chat messages from user or assistant
    - JobStartedEvent: Job execution started
    - JobSucceededEvent: Job execution succeeded
    - JobFailedEvent: Job execution failed
    - BoardUpdateEvent: Board state changed
    - WIPLimitExceededEvent: WIP limit reached

Example:
    >>> from src.core.events import emit_chat_message
    >>> emit_chat_message(role="user", text="Hello", session_id="sess-123")
    True
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Literal

import redis

logger = logging.getLogger(__name__)

# Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
WF_EVENTS_CHANNEL = os.getenv("WF_EVENTS_CHANNEL", "wf:events")
WF_EVENTS_LIST = os.getenv("WF_EVENTS_LIST", "wf:events:recent")
RECENT_EVENTS_KEEP = int(os.getenv("WF_RECENT_EVENTS_KEEP", "200"))


# ============================================
# Event Type Definitions
# ============================================

class BaseEvent:
    """Base class for all SSE events.

    Provides common functionality for event serialization.
    Not a dataclass itself to avoid field ordering issues with inheritance.
    """

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary for JSON serialization.

        Returns:
            Dictionary representation of the event
        """
        return asdict(self)


@dataclass
class ChatMessageEvent(BaseEvent):
    """Chat message from user or assistant.

    Attributes:
        role: Message sender ("user" or "assistant")
        text: Message content
        kind: Event type (always "chat_message")
        session_id: Chat session identifier (optional)
        ts: Unix timestamp in milliseconds
    """
    role: Literal["user", "assistant"]
    text: str
    kind: str = field(default="chat_message", init=False)
    session_id: Optional[str] = None
    ts: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class JobStartedEvent(BaseEvent):
    """Job execution started.

    Attributes:
        job_id: Unique job identifier
        action: Agent name executing the job
        kind: Event type (always "job_started")
        card_id: Associated card ID (optional)
        ts: Unix timestamp in milliseconds
    """
    job_id: str
    action: str
    kind: str = field(default="job_started", init=False)
    card_id: Optional[str] = None
    ts: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class JobSucceededEvent(BaseEvent):
    """Job execution succeeded.

    Attributes:
        job_id: Unique job identifier
        action: Agent name that executed
        kind: Event type (always "job_succeeded")
        card_id: Associated card ID (optional)
        data: Result data from job execution (optional)
        ts: Unix timestamp in milliseconds
    """
    job_id: str
    action: str
    kind: str = field(default="job_succeeded", init=False)
    card_id: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    ts: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class JobFailedEvent(BaseEvent):
    """Job execution failed.

    Attributes:
        job_id: Unique job identifier
        action: Agent name that failed
        error: Error message describing the failure
        kind: Event type (always "job_failed")
        card_id: Associated card ID (optional)
        ts: Unix timestamp in milliseconds
    """
    job_id: str
    action: str
    error: str
    kind: str = field(default="job_failed", init=False)
    card_id: Optional[str] = None
    ts: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class BoardUpdateEvent(BaseEvent):
    """Board state changed.

    Attributes:
        cards: List of affected cards with their current state
        kind: Event type (always "board_update")
        ts: Unix timestamp in milliseconds
    """
    cards: List[Dict[str, Any]]
    kind: str = field(default="board_update", init=False)
    ts: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class WIPLimitExceededEvent(BaseEvent):
    """WIP limit reached, card cannot be added.

    Attributes:
        column: Column name where limit was exceeded
        card_id: ID of card that couldn't be added
        card_title: Title of card that couldn't be added
        current_count: Current number of cards in column
        limit: Maximum allowed cards in column
        kind: Event type (always "wip_limit_exceeded")
        ts: Unix timestamp in milliseconds
    """
    column: str
    card_id: str
    card_title: str
    current_count: int
    limit: int
    kind: str = field(default="wip_limit_exceeded", init=False)
    ts: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class CardCreatedEvent(BaseEvent):
    """Card criado no board.

    Attributes:
        id: Card ID
        title: Card title
        list: Coluna onde foi criado
        kind: Event type (always "card.created")
        meta: Card metadata (optional)
        ts: Unix timestamp in milliseconds
    """
    id: str
    title: str
    list: str  # Column name
    kind: str = field(default="card.created", init=False)
    meta: Optional[Dict[str, Any]] = None
    ts: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class CardMovedEvent(BaseEvent):
    """Card movido entre colunas.

    Attributes:
        id: Card ID
        title: Card title
        from_list: Coluna origem
        to_list: Coluna destino
        kind: Event type (always "card.moved")
        meta: Card metadata (optional)
        ts: Unix timestamp in milliseconds
    """
    id: str
    title: str
    from_list: str
    to_list: str
    kind: str = field(default="card.moved", init=False)
    meta: Optional[Dict[str, Any]] = None
    ts: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class CardUpdatedEvent(BaseEvent):
    """Card atualizado.

    Attributes:
        id: Card ID
        title: Card title
        list: Coluna atual
        fields_updated: Lista de campos alterados
        kind: Event type (always "card.updated")
        meta: Card metadata (optional)
        ts: Unix timestamp in milliseconds
    """
    id: str
    title: str
    list: str
    fields_updated: List[str]
    kind: str = field(default="card.updated", init=False)
    meta: Optional[Dict[str, Any]] = None
    ts: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class PendingConfirmationEvent(BaseEvent):
    """Ação aguardando confirmação do usuário.

    Attributes:
        token: Token único para identificar a confirmação
        summary: Descrição da ação aguardando aprovação
        kind: Event type (always "pending.confirmation")
        session_id: Chat session ID (optional)
        ts: Unix timestamp in milliseconds
    """
    token: str
    summary: str
    kind: str = field(default="pending.confirmation", init=False)
    session_id: Optional[str] = None
    ts: int = field(default_factory=lambda: int(time.time() * 1000))


# ============================================
# SSE Emitter
# ============================================

class SSEEmitter:
    """Centralized SSE event emission system.

    Handles:
        - Redis pub/sub publishing for real-time streaming
        - Event history maintenance (rolling list)
        - Error handling and logging
        - Thread-safe emission

    Example:
        >>> emitter = SSEEmitter()
        >>> event = ChatMessageEvent(role="user", text="Hello")
        >>> emitter.emit(event)
        True
    """

    def __init__(
        self,
        redis_url: str = REDIS_URL,
        channel: str = WF_EVENTS_CHANNEL,
        list_key: str = WF_EVENTS_LIST,
        history_size: int = RECENT_EVENTS_KEEP
    ):
        """Initialize SSE emitter.

        Args:
            redis_url: Redis connection URL
            channel: Redis pub/sub channel name
            list_key: Redis list key for event history
            history_size: Maximum number of events to keep in history
        """
        self.redis_url = redis_url
        self.channel = channel
        self.list_key = list_key
        self.history_size = history_size
        self._redis_client: Optional[redis.Redis] = None

    def _get_redis(self) -> Optional[redis.Redis]:
        """Get or create Redis client (lazy initialization).

        Returns:
            Redis client instance or None if connection fails
        """
        if self._redis_client is None:
            try:
                self._redis_client = redis.Redis.from_url(
                    self.redis_url,
                    decode_responses=True
                )
                self._redis_client.ping()
                logger.debug(f"SSEEmitter connected to Redis at {self.redis_url}")
            except Exception as e:
                logger.warning(f"Redis not available for SSE events: {e}")
                return None
        return self._redis_client

    def emit(self, event: BaseEvent) -> bool:
        """Emit an event to Redis pub/sub and history list.

        The event is:
        1. Published to Redis pub/sub channel for real-time streaming
        2. Prepended to history list (lpush)
        3. History list trimmed to max size

        Args:
            event: Event instance to emit

        Returns:
            True if emission succeeded, False otherwise
        """
        r = self._get_redis()
        if not r:
            logger.debug(f"Skipping event emission (Redis unavailable): {event.kind}")
            return False

        try:
            event_dict = event.to_dict()
            raw = json.dumps(event_dict, default=str)

            pipe = r.pipeline()
            pipe.publish(self.channel, raw)
            pipe.lpush(self.list_key, raw)
            pipe.ltrim(self.list_key, 0, self.history_size - 1)
            pipe.execute()

            logger.debug(f"Emitted SSE event: {event.kind} ({len(raw)} bytes)")
            return True

        except Exception as e:
            logger.error(f"Failed to emit SSE event {event.kind}: {e}")
            return False

    def emit_raw(self, kind: str, payload: Dict[str, Any]) -> bool:
        """Emit a raw event (for backward compatibility or ad-hoc events).

        Use this for custom events that don't have a dedicated dataclass.
        The payload will be merged with kind and timestamp.

        Args:
            kind: Event type string
            payload: Event payload (will be merged with kind and ts)

        Returns:
            True if emission succeeded, False otherwise
        """
        event_dict = {
            "ts": int(time.time() * 1000),
            "kind": kind,
            **payload
        }

        r = self._get_redis()
        if not r:
            logger.debug(f"Skipping raw event emission (Redis unavailable): {kind}")
            return False

        try:
            raw = json.dumps(event_dict, default=str)
            pipe = r.pipeline()
            pipe.publish(self.channel, raw)
            pipe.lpush(self.list_key, raw)
            pipe.ltrim(self.list_key, 0, self.history_size - 1)
            pipe.execute()

            logger.debug(f"Emitted raw SSE event: {kind} ({len(raw)} bytes)")
            return True

        except Exception as e:
            logger.error(f"Failed to emit raw event {kind}: {e}")
            return False

    def close(self):
        """Close Redis connection.

        Should be called when shutting down to clean up resources.
        """
        if self._redis_client:
            try:
                self._redis_client.close()
                logger.debug("SSEEmitter Redis connection closed")
            except Exception as e:
                logger.warning(f"Error closing Redis connection: {e}")
            finally:
                self._redis_client = None


# ============================================
# Global Emitter Instance
# ============================================

_default_emitter: Optional[SSEEmitter] = None


def get_default_emitter() -> SSEEmitter:
    """Get or create the default global SSE emitter.

    Returns:
        Singleton SSEEmitter instance
    """
    global _default_emitter
    if _default_emitter is None:
        _default_emitter = SSEEmitter()
    return _default_emitter


def set_default_emitter(emitter: Optional[SSEEmitter]):
    """Set the default global SSE emitter.

    Useful for testing - inject a mock emitter.

    Args:
        emitter: SSEEmitter instance or None to reset
    """
    global _default_emitter
    _default_emitter = emitter


# ============================================
# Convenience Functions
# ============================================

def emit_chat_message(
    role: Literal["user", "assistant"],
    text: str,
    session_id: Optional[str] = None
) -> bool:
    """Emit a chat message event.

    Args:
        role: Message sender ("user" or "assistant")
        text: Message content
        session_id: Chat session identifier (optional)

    Returns:
        True if emission succeeded, False otherwise
    """
    event = ChatMessageEvent(role=role, text=text, session_id=session_id)
    return get_default_emitter().emit(event)


def emit_job_started(
    job_id: str,
    action: str,
    card_id: Optional[str] = None
) -> bool:
    """Emit a job started event.

    Args:
        job_id: Unique job identifier
        action: Agent name executing the job
        card_id: Associated card ID (optional)

    Returns:
        True if emission succeeded, False otherwise
    """
    event = JobStartedEvent(job_id=job_id, action=action, card_id=card_id)
    return get_default_emitter().emit(event)


def emit_job_succeeded(
    job_id: str,
    action: str,
    card_id: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None
) -> bool:
    """Emit a job succeeded event.

    Args:
        job_id: Unique job identifier
        action: Agent name that executed
        card_id: Associated card ID (optional)
        data: Result data from job execution (optional)

    Returns:
        True if emission succeeded, False otherwise
    """
    event = JobSucceededEvent(job_id=job_id, action=action, card_id=card_id, data=data)
    return get_default_emitter().emit(event)


def emit_job_failed(
    job_id: str,
    action: str,
    error: str,
    card_id: Optional[str] = None
) -> bool:
    """Emit a job failed event.

    Args:
        job_id: Unique job identifier
        action: Agent name that failed
        error: Error message describing the failure
        card_id: Associated card ID (optional)

    Returns:
        True if emission succeeded, False otherwise
    """
    event = JobFailedEvent(job_id=job_id, action=action, error=error, card_id=card_id)
    return get_default_emitter().emit(event)


def emit_board_update(cards: List[Dict[str, Any]]) -> bool:
    """Emit a board update event.

    Args:
        cards: List of affected cards with their current state

    Returns:
        True if emission succeeded, False otherwise
    """
    event = BoardUpdateEvent(cards=cards)
    return get_default_emitter().emit(event)


def emit_wip_limit_exceeded(
    column: str,
    card_id: str,
    card_title: str,
    current_count: int,
    limit: int
) -> bool:
    """Emit a WIP limit exceeded event.

    Args:
        column: Column name where limit was exceeded
        card_id: ID of card that couldn't be added
        card_title: Title of card that couldn't be added
        current_count: Current number of cards in column
        limit: Maximum allowed cards in column

    Returns:
        True if emission succeeded, False otherwise
    """
    event = WIPLimitExceededEvent(
        column=column,
        card_id=card_id,
        card_title=card_title,
        current_count=current_count,
        limit=limit
    )
    return get_default_emitter().emit(event)


def emit_card_created(
    card_id: str,
    title: str,
    list_name: str,
    meta: Optional[Dict[str, Any]] = None
) -> bool:
    """Emit a card created event.

    Args:
        card_id: Card ID (ex: c-abc123)
        title: Card title
        list_name: Column name where card was created
        meta: Card metadata (optional)

    Returns:
        True if emission succeeded, False otherwise

    Examples:
        >>> emit_card_created("c-123", "Implementar feature X", "Produção")
        True
    """
    event = CardCreatedEvent(id=card_id, title=title, list=list_name, meta=meta)
    return get_default_emitter().emit(event)


def emit_card_moved(
    card_id: str,
    title: str,
    from_list: str,
    to_list: str,
    meta: Optional[Dict[str, Any]] = None
) -> bool:
    """Emit a card moved event.

    Args:
        card_id: Card ID
        title: Card title
        from_list: Origin column name
        to_list: Target column name
        meta: Card metadata (optional)

    Returns:
        True if emission succeeded, False otherwise

    Examples:
        >>> emit_card_moved("c-123", "Deploy", "Produção", "Agendado")
        True
    """
    event = CardMovedEvent(id=card_id, title=title, from_list=from_list, to_list=to_list, meta=meta)
    return get_default_emitter().emit(event)


def emit_card_updated(
    card_id: str,
    title: str,
    list_name: str,
    fields_updated: List[str],
    meta: Optional[Dict[str, Any]] = None
) -> bool:
    """Emit a card updated event.

    Args:
        card_id: Card ID
        title: Card title (current, after update)
        list_name: Current column name
        fields_updated: List of field names that were updated
        meta: Card metadata (optional)

    Returns:
        True if emission succeeded, False otherwise

    Examples:
        >>> emit_card_updated("c-123", "Deploy fixed", "Produção", ["title", "assignee"])
        True
    """
    event = CardUpdatedEvent(id=card_id, title=title, list=list_name, fields_updated=fields_updated, meta=meta)
    return get_default_emitter().emit(event)


def emit_pending_confirmation(
    token: str,
    summary: str,
    session_id: Optional[str] = None
) -> bool:
    """Emit a pending confirmation event.

    Args:
        token: Unique token to identify this confirmation request
        summary: Human-readable description of the action awaiting approval
        session_id: Chat session ID (optional)

    Returns:
        True if emission succeeded, False otherwise

    Examples:
        >>> emit_pending_confirmation("abc123", "Mover card para Finalizado?", "sess-456")
        True
    """
    event = PendingConfirmationEvent(token=token, summary=summary, session_id=session_id)
    return get_default_emitter().emit(event)


# ============================================
# SSE Stream Helpers
# ============================================

def format_sse_event(data: Dict[str, Any], event_type: str = "message") -> str:
    """Format a dictionary as an SSE event.

    Follows SSE protocol specification:
    https://html.spec.whatwg.org/multipage/server-sent-events.html

    Args:
        data: Event data to serialize
        event_type: SSE event type (default: "message")

    Returns:
        Formatted SSE string: "event: {type}\\ndata: {json}\\n\\n"

    Example:
        >>> format_sse_event({"foo": "bar"})
        'event: message\\ndata: {"foo": "bar"}\\n\\n'
    """
    json_data = json.dumps(data, default=str)
    return f"event: {event_type}\ndata: {json_data}\n\n"


def format_sse_comment(comment: str) -> str:
    """Format an SSE comment (used for heartbeats and metadata).

    Comments are lines starting with ':' and are ignored by clients.

    Args:
        comment: Comment text

    Returns:
        Formatted SSE comment: ": {comment}\\n\\n"

    Example:
        >>> format_sse_comment("heartbeat")
        ': heartbeat\\n\\n'
    """
    return f": {comment}\n\n"


def format_sse_heartbeat() -> str:
    """Format an SSE heartbeat comment.

    Heartbeats prevent connection timeout and detect client disconnection.
    Should be sent every 15-30 seconds when no events are being emitted.

    Returns:
        Formatted SSE heartbeat: ": hb\\n\\n"

    Example:
        >>> format_sse_heartbeat()
        ': hb\\n\\n'
    """
    return ": hb\n\n"
