#!/usr/bin/env python3
"""
WordFlux Cockpit - Integrated with Job Queue System
Agent-left, Board-right cockpit with full job queue integration
"""

import os
import sys
import json
import time
import uuid
import threading
import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from fastapi import FastAPI, Body
    from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
except Exception as e:
    raise SystemExit("FastAPI not installed. Install: pip install fastapi uvicorn redis") from e

try:
    import redis
except Exception as e:
    raise SystemExit("redis-py not installed. Install: pip install redis") from e

# Import WordFlux components
from src.core.queue import JobQueue, load_default_queue, set_default_queue
from src.core.job import Job
from src.core.events import (
    emit_wip_limit_exceeded,
    emit_board_update,
    get_default_emitter,
    format_sse_heartbeat
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Configuration ----------
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
QUEUE_MODE = os.getenv("QUEUE_MODE", "redis")
WF_EVENTS_CHANNEL = os.getenv("WF_EVENTS_CHANNEL", "wf:events")
WF_EVENTS_LIST = os.getenv("WF_EVENTS_LIST", "wf:events:recent")
WF_BOARD_COLUMNS_KEY = "wf:board:columns"
DEFAULT_COLUMNS = ["Backlog", "In Progress", "Waiting Approval", "Scheduled", "Published"]
RECENT_EVENTS_KEEP = int(os.getenv("WF_RECENT_EVENTS_KEEP", "200"))

# Portuguese column labels for UI display
COLUMN_LABELS = {
    "Backlog": "Espera",
    "In Progress": "Produção",
    "Waiting Approval": "Aprovação",
    "Published": "Finalizado"
}

# Column name migration: English → Portuguese (4-column structure)
# During migration period (7 days from 2025-09-30), we support both
COLUMN_NAME_MIGRATION = {
    "Backlog": "Espera",
    "In Progress": "Produção",
    "Waiting Approval": "Aprovação",
    "Scheduled": "Aprovação",  # Scheduled cards migrate to Aprovação
    "Published": "Finalizado"
}

# Reverse mapping for backward compatibility
COLUMN_NAME_REVERSE = {v: k for k, v in COLUMN_NAME_MIGRATION.items() if k != "Scheduled"}

# Portuguese columns (4-column structure)
PORTUGUESE_COLUMNS = ["Espera", "Produção", "Aprovação", "Finalizado"]

# Migration deadline: 2025-10-07 (7 days from 2025-09-30)
MIGRATION_DEADLINE = "2025-10-07"

# Comprehensive title mapping for /board/state API responses
# Maps both English and Portuguese column IDs to Portuguese display titles
# Supports both 4-column (Portuguese) and 5-column (English) structures during migration
COLUMN_TITLES = {
    # English columns (5-column structure - current Redis state)
    "Backlog": "Espera",
    "In Progress": "Produção",
    "Waiting Approval": "Aprovação",
    "Scheduled": "Agendado",
    "Published": "Finalizado",
    # Portuguese columns (4-column structure - migration target)
    # Identity mapping ensures Portuguese IDs work correctly
    "Espera": "Espera",
    "Produção": "Produção",
    "Aprovação": "Aprovação",
    "Finalizado": "Finalizado"
}


def get_column_title(column_id: str) -> str:
    """
    Get Portuguese display title for a column.

    Supports both English and Portuguese column identifiers,
    ensuring /board/state always returns non-null titles.

    Args:
        column_id: Column identifier (English or Portuguese)

    Returns:
        Portuguese display title (never null)

    Examples:
        >>> get_column_title("Backlog")
        "Espera"
        >>> get_column_title("Espera")
        "Espera"
        >>> get_column_title("Unknown")
        "Unknown"  # Graceful fallback
    """
    return COLUMN_TITLES.get(column_id, column_id)


def get_column_key(column: str) -> str:
    """
    Get Redis key for column, supporting both English and Portuguese names.

    During migration: Always use Portuguese keys.
    After migration: Only Portuguese keys exist.

    Args:
        column: Column name (English or Portuguese)

    Returns:
        Redis key string
    """
    # Normalize to Portuguese
    if column in COLUMN_NAME_REVERSE:
        # Already Portuguese
        pt_name = column
    elif column in COLUMN_NAME_MIGRATION:
        # English, map to Portuguese
        pt_name = COLUMN_NAME_MIGRATION[column]
    else:
        # Unknown column or already standard (Backlog, Finalizado), use as-is
        pt_name = column

    return f"wf:board:col:{pt_name}"


# Action to Agent Mappings
ACTION_AGENTS = {
    "start_work": "task_starter",
    "send_for_review": "review_requester",
    "approve": "content_approver",
    "request_changes": "change_requester",
    "publish_now": "content_publisher",
    "reschedule": "scheduler",
    "report_kpis": "metrics_reporter",
    "pause": "task_pauser"
}

# Notification Triggers - which state changes trigger Slack notifications (Portuguese 4-column)
NOTIFY_TRIGGERS = {
    "Produção": "🔨 Work started: {title}",
    "Aprovação": "📋 Review requested: {title}",
    "Finalizado": "🚀 Published: {title}"
}


def rclient(decode: bool = True):
    """Get Redis client."""
    return redis.Redis.from_url(REDIS_URL, decode_responses=decode)


def get_queue_manager() -> JobQueue:
    """Get or create the queue manager."""
    queue = load_default_queue()
    if not queue:
        from src.core.queue import MemoryQueue, RedisQueue
        if QUEUE_MODE == "redis":
            queue = RedisQueue(redis_url=REDIS_URL)
        else:
            queue = MemoryQueue()
    return queue


# ---------- Board / Agent helpers ----------
def get_columns() -> List[str]:
    """Get board columns from Redis or use Portuguese defaults."""
    r = rclient()
    raw = r.get(WF_BOARD_COLUMNS_KEY)
    if not raw:
        # Use Portuguese columns as default
        r.set(WF_BOARD_COLUMNS_KEY, json.dumps(PORTUGUESE_COLUMNS))
        return PORTUGUESE_COLUMNS[:]
    try:
        cols = json.loads(raw)
        if isinstance(cols, list) and all(isinstance(c, str) for c in cols):
            return cols
    except Exception:
        pass
    # Fallback to Portuguese defaults on bad state
    r.set(WF_BOARD_COLUMNS_KEY, json.dumps(PORTUGUESE_COLUMNS))
    return PORTUGUESE_COLUMNS[:]


def get_board_state() -> Dict[str, Any]:
    """Get complete board state including cards and autopilot mode.

    Returns:
        {
            "columns": {
                "Espera": [Card, ...],
                "Produção": [Card, ...],
                "Aprovação": [Card, ...],
                "Agendado": [Card, ...],
                "Finalizado": [Card, ...]
            },
            "autopilot": bool
        }
    """
    r = rclient()
    cols = get_columns()
    columns_dict = {}

    for c in cols:
        key = f"wf:board:col:{c}"
        items = r.lrange(key, 0, -1)  # newest first
        cards = []

        for raw in items:
            try:
                card = json.loads(raw)

                # Validar card contra schema
                try:
                    from src.core.schemas import CardSchema
                    validated_card = CardSchema(**card).model_dump()
                    cards.append(validated_card)
                except Exception as validation_error:
                    logger.warning(f"⚠️ Card inválido removido da coluna '{c}': {validation_error}")
                    # Remove card inválido do Redis
                    r.lrem(key, 1, raw)

            except json.JSONDecodeError as e:
                logger.warning(f"⚠️ Card mal-formado removido da coluna '{c}': {e}")
                r.lrem(key, 1, raw)

        columns_dict[c] = cards

    autopilot = r.get("wf:agent:autopilot") == "1"

    # Validar resposta completa contra schema
    try:
        from src.core.schemas import BoardStateSchema
        validated_state = BoardStateSchema(columns=columns_dict, autopilot=autopilot)
        return validated_state.model_dump()
    except Exception as e:
        logger.error(f"❌ Erro ao validar estado do board: {e}")
        # Retornar estado parcial mas válido em caso de erro
        return {"columns": {col: [] for col in cols}, "autopilot": False}


def emit_event(kind: str, payload: Dict[str, Any]) -> None:
    """Emit event using unified event system.

    Deprecated: Use typed event functions (emit_board_update, etc.) instead.
    This function is kept for backward compatibility with custom event types.
    """
    emitter = get_default_emitter()
    emitter.emit_raw(kind, payload)


def push_to(column: str, card: Dict[str, Any], bypass_wip: bool = False) -> bool:
    """
    Push card to a specific column respecting WIP limits.

    During migration: Writes to BOTH English and Portuguese keys.

    Args:
        column: Target column name (English or Portuguese)
        card: Card data
        bypass_wip: If True, skip WIP checks (for system operations)

    Returns:
        True if card was added, False if WIP limit prevented it
    """
    r = rclient()

    # Normalize column name to Portuguese
    pt_column = COLUMN_NAME_MIGRATION.get(column, column)

    # Check WIP limit for "Produção" column (unless bypassed)
    if pt_column == "Produção" and not bypass_wip:
        wip_limit = int(os.getenv("WF_WIP_LIMIT", "2"))

        # Check both keys during migration
        pt_key = get_column_key(pt_column)
        en_key = "wf:board:col:In Progress"  # Legacy key

        current_count = r.llen(pt_key) + r.llen(en_key)  # Sum both

        if current_count >= wip_limit:
            logger.warning(
                f"Cannot add card to {pt_column}: WIP limit {wip_limit} reached "
                f"(current: {current_count})"
            )
            emit_wip_limit_exceeded(
                column=pt_column,
                card_id=card.get("id"),
                card_title=card.get("title", "Untitled"),
                current_count=current_count,
                limit=wip_limit
            )
            return False

    card["status"] = pt_column
    card["updated_at"] = datetime.now(timezone.utc).isoformat()
    card_json = json.dumps(card)

    # DUAL-KEY WRITE during migration period
    pt_key = get_column_key(pt_column)
    r.lpush(pt_key, card_json)

    # Also write to English key if different (for backward compatibility)
    if column in COLUMN_NAME_MIGRATION and COLUMN_NAME_MIGRATION[column] != column:
        en_key = f"wf:board:col:{column}"
        r.lpush(en_key, card_json)
        logger.debug(f"Dual-key write: {pt_key} + {en_key}")

    emit_board_update(cards=[card])

    # Check if we should trigger a notification
    if pt_column in NOTIFY_TRIGGERS:
        queue_notification(card, pt_column)

    return True


def find_and_remove(card_id: str) -> (Optional[Dict[str, Any]], Optional[str]):
    """
    Find and remove a card from any column.

    During migration: Checks BOTH English and Portuguese keys.
    """
    r = rclient()

    # Check Portuguese columns first
    for pt_col in PORTUGUESE_COLUMNS:
        key = get_column_key(pt_col)
        items = r.lrange(key, 0, -1)
        for raw in items:
            try:
                card = json.loads(raw)
            except Exception:
                r.lrem(key, 1, raw)
                continue
            if card.get("id") == card_id:
                r.lrem(key, 1, raw)
                return card, pt_col

    # Fallback: Check English columns (for cards not yet migrated)
    for en_col in DEFAULT_COLUMNS:
        if en_col not in COLUMN_NAME_MIGRATION and en_col != "Backlog":
            continue  # Skip if no mapping

        key = f"wf:board:col:{en_col}"
        items = r.lrange(key, 0, -1)
        for raw in items:
            try:
                card = json.loads(raw)
            except Exception:
                r.lrem(key, 1, raw)
                continue
            if card.get("id") == card_id:
                r.lrem(key, 1, raw)
                # Return Portuguese column name
                pt_col = COLUMN_NAME_MIGRATION.get(en_col, en_col)
                return card, pt_col

    return None, None


def suggest_actions(column: str) -> List[str]:
    """Get suggested actions for a column (4-column Portuguese structure)."""
    mapping = {
        "Espera": ["start_work"],
        "Produção": ["send_for_review", "pause"],
        "Aprovação": ["approve", "request_changes"],
        "Finalizado": []
    }
    return mapping.get(column, [])


def queue_job(agent: str, payload: Dict[str, Any], idempotency_key: Optional[str] = None) -> str:
    """Queue a job for processing by workers."""
    queue = get_queue_manager()

    job = Job(
        agent=agent,
        payload=payload,
        job_id=f"cockpit-{uuid.uuid4().hex[:8]}"
    )

    # Add idempotency key to metadata if provided
    if idempotency_key:
        job.metadata["idempotency_key"] = idempotency_key

    try:
        queue.publish(job)
        logger.info(f"Queued job {job.job_id} for agent {agent}")
        emit_event("job_queued", {
            "job_id": job.job_id,
            "agent": agent,
            "payload": payload
        })
        return job.job_id
    except Exception as e:
        logger.error(f"Failed to queue job: {e}")
        raise


def queue_notification(card: Dict[str, Any], column: str) -> None:
    """Queue a Slack notification for a card state change."""
    if column not in NOTIFY_TRIGGERS:
        return

    message = NOTIFY_TRIGGERS[column].format(title=card.get("title", "Untitled"))

    payload = {
        "message": message,
        "channel": "#cockpit-updates",
        "card_id": card.get("id"),
        "card_title": card.get("title"),
        "status": column,
        "cockpit_url": f"http://localhost:8080/#card-{card.get('id')}",
        "username": "Cockpit Bot",
        "icon_emoji": ":control_knobs:"
    }

    try:
        job_id = queue_job("slack_notifier", payload,
                          idempotency_key=f"notify-{card.get('id')}-{column}")
        logger.info(f"Queued notification job {job_id} for card {card.get('id')}")
    except Exception as e:
        logger.error(f"Failed to queue notification: {e}")


def agent_act(card_id: str, action: str) -> Dict[str, Any]:
    """Execute an agent action on a card."""
    card, cur = find_and_remove(card_id)
    if not card:
        return {"error": "not_found"}

    # Queue the job for the action
    agent_name = ACTION_AGENTS.get(action, "echo")
    job_payload = {
        "action": action,
        "card": card,
        "from_column": cur,
        "cockpit": True,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    try:
        job_id = queue_job(agent_name, job_payload,
                          idempotency_key=f"action-{card_id}-{action}-{int(time.time())}")
    except Exception as e:
        # Return card to original position on error (bypass WIP to ensure revert succeeds)
        push_to(cur or "Backlog", card, bypass_wip=True)
        return {"error": str(e)}

    # Determine next column based on action
    nxt = cur  # default return if unknown
    if action == "start_work":
        nxt = "In Progress"
    elif action == "send_for_review":
        nxt = "Waiting Approval"
    elif action == "approve":
        nxt = "Scheduled"
    elif action == "request_changes":
        nxt = "In Progress"
    elif action == "publish_now":
        nxt = "Published"
    elif action == "report_kpis":
        nxt = "Published"
        emit_event("kpi_report", {"card": card})
    elif action == "reschedule":
        nxt = "Scheduled"
    else:
        # unknown action, revert to original column if we had one
        nxt = cur or "Backlog"

    # Update card with job info
    card["last_job_id"] = job_id
    card["last_action"] = action

    # Try to push to next column; if WIP exceeded, revert to original
    if not push_to(nxt, card):
        logger.warning(f"WIP limit exceeded for {nxt}, reverting card {card_id} to {cur}")
        push_to(cur or "Backlog", card, bypass_wip=True)
        return {
            "error": "wip_limit_exceeded",
            "message": f"Cannot move to {nxt}: WIP limit reached",
            "card_id": card_id,
            "reverted_to": cur or "Backlog"
        }

    emit_event("agent_action", {
        "card": card,
        "action": action,
        "to": nxt,
        "job_id": job_id
    })

    return {"ok": True, "to": nxt, "job_id": job_id}


# ---------- FastAPI app ----------
INDEX_HTML = """<!DOCTYPE html>
<html lang="pt-BR" data-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="description" content="WordFlux Cockpit - Agent-driven task management system">
  <title>WordFlux Cockpit</title>
  <style>
    /* ============================================
       1. CSS VARIABLES & DESIGN TOKENS
       ============================================ */
    :root {
      /* Dark Mode (Default) - Updated color palette */
      --wf-bg: #0F1420;
      --wf-surface: #161C2A;
      --wf-card: #1B2332;
      --wf-border: #2A3446;
      --wf-text: #E9EEF9;
      --wf-muted: #A5B1C6;

      /* Brand Colors (Pink to Orange gradient) */
      --wf-pink: #FF2A6D;
      --wf-orange: #FF7A00;
      --wf-gradient: linear-gradient(180deg, #FF2A6D 0%, #FF7A00 100%);

      /* Legacy aliases for compatibility */
      --wf-primary: #FF2A6D;  /* maps to --wf-pink */
      --wf-accent: #FF7A00;   /* maps to --wf-orange */
      --wf-grad: linear-gradient(180deg, #FF2A6D 0%, #FF7A00 100%);
      --wf-panel: #1B2332;    /* maps to --wf-card */

      /* Extended Palette */
      --wf-purple: #8B5CF6;
      --wf-magenta: #EC4899;
      --wf-amber: #F59E0B;
      --wf-green: #22C55E;
      --wf-red: #EF4444;

      /* Semantic Colors */
      --wf-success: #4ade80;
      --wf-warning: #fbbf24;
      --wf-error: #ef4444;
      --wf-info: #3b82f6;

      /* Shadows */
      --wf-shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.5);
      --wf-shadow-md: 0 4px 6px rgba(0, 0, 0, 0.3);
      --wf-shadow-lg: 0 10px 15px rgba(0, 0, 0, 0.4);

      /* Spacing */
      --spacing-xs: 4px;
      --spacing-sm: 8px;
      --spacing-md: 12px;
      --spacing-lg: 16px;
      --spacing-xl: 24px;

      /* Typography */
      --font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif;
      --font-size-xs: 12px;
      --font-size-sm: 13px;
      --font-size-base: 14px;
      --font-size-lg: 16px;
      --font-size-xl: 18px;
      --font-size-2xl: 24px;

      /* Layout */
      --chat-width: 420px;  /* Updated from 320px */
      --metrics-width: 340px;
      --header-height: 60px;
    }

    [data-theme="light"] {
      --wf-bg: #F6F7FB;
      --wf-surface: #FFFFFF;
      --wf-card: #FFFFFF;
      --wf-border: #E5E7EB;
      --wf-text: #0B0F17;
      --wf-muted: #6B7280;
      --wf-panel: #FFFFFF;  /* compatibility */

      --wf-shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.1);
      --wf-shadow-md: 0 4px 6px rgba(0, 0, 0, 0.07);
      --wf-shadow-lg: 0 10px 15px rgba(0, 0, 0, 0.1);
    }

    /* ============================================
       2. RESET & BASE STYLES
       ============================================ */
    *, *::before, *::after {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    html {
      font-size: 16px;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
    }

    body {
      font-family: var(--font-family);
      font-size: var(--font-size-base);
      line-height: 1.5;
      color: var(--wf-text);
      background-color: var(--wf-bg);
      overflow: hidden;
    }

    /* Smooth transitions for theme changes */
    * {
      transition: background-color 0.3s ease,
                  color 0.3s ease,
                  border-color 0.3s ease,
                  box-shadow 0.3s ease;
    }

    /* Disable transitions during theme change for instant feedback */
    [data-theme-changing] * {
      transition: none !important;
    }

    /* ============================================
       3. LAYOUT - CSS GRID
       ============================================ */
    .app-container {
      display: grid;
      grid-template-columns: var(--chat-width) 1fr var(--metrics-width);
      grid-template-areas: "chat board metrics";
      height: 100vh;
      overflow: hidden;
    }

    .chat-panel {
      grid-area: chat;
      background: var(--wf-panel);
      border-right: 1px solid var(--wf-border);
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }

    .board-container {
      grid-area: board;
      background: var(--wf-bg);
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }

    .metrics-panel {
      grid-area: metrics;
      background: var(--wf-panel);
      border-left: 1px solid var(--wf-border);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }

    .metrics-panel.collapsed {
      transform: translateX(100%);
    }

    /* ============================================
       4. HEADER
       ============================================ */
    .app-header {
      height: var(--header-height);
      background: var(--wf-panel);
      border-bottom: 1px solid var(--wf-border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 var(--spacing-lg);
      flex-shrink: 0;
    }

    .header-title {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      font-weight: 700;
      font-size: var(--font-size-xl);
    }

    .header-logo {
      width: 32px;
      height: 32px;
      border-radius: 50%;
      background: var(--wf-grad);
      display: flex;
      align-items: center;
      justify-content: center;
      color: white;
      font-weight: 900;
      font-size: 18px;
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: var(--spacing-md);
    }

    .connection-status {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      font-size: var(--font-size-sm);
      color: var(--wf-muted);
    }

    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--wf-success);
      animation: pulse 2s ease-in-out infinite;
    }

    [data-connected="false"] .status-dot {
      background: var(--wf-error);
      animation: none;
    }

    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.5; }
    }

    .theme-toggle {
      background: transparent;
      border: 1px solid var(--wf-border);
      color: var(--wf-text);
      width: 40px;
      height: 40px;
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font-size: 20px;
      transition: all 0.2s ease;
    }

    .theme-toggle:hover {
      background: var(--wf-border);
      border-color: var(--wf-primary);
    }

    .theme-toggle:focus-visible {
      outline: 2px solid var(--wf-primary);
      outline-offset: 2px;
    }

    /* ============================================
       5. CHAT PANEL
       ============================================ */
    .chat-header {
      padding: var(--spacing-lg);
      border-bottom: 1px solid var(--wf-border);
      flex-shrink: 0;
    }

    .chat-title {
      font-size: var(--font-size-lg);
      font-weight: 700;
      margin-bottom: var(--spacing-xs);
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
    }

    .chat-subtitle {
      font-size: var(--font-size-sm);
      color: var(--wf-muted);
    }

    .chat-messages {
      flex: 1;
      overflow-y: auto;
      padding: var(--spacing-lg);
      display: flex;
      flex-direction: column;
      gap: var(--spacing-md);
    }

    .chat-message {
      display: flex;
      gap: var(--spacing-sm);
      animation: messageEnter 0.3s ease-out;
    }

    @keyframes messageEnter {
      from {
        opacity: 0;
        transform: translateY(10px);
      }
      to {
        opacity: 1;
        transform: translateY(0);
      }
    }

    .message-user {
      flex-direction: row-reverse;
    }

    .message-avatar {
      width: 32px;
      height: 32px;
      border-radius: 50%;
      background: var(--wf-grad);
      display: flex;
      align-items: center;
      justify-content: center;
      color: white;
      font-weight: 700;
      font-size: var(--font-size-sm);
      flex-shrink: 0;
    }

    .message-user .message-avatar {
      background: var(--wf-border);
      color: var(--wf-text);
    }

    .message-content {
      max-width: 75%;
      display: flex;
      flex-direction: column;
      gap: var(--spacing-xs);
    }

    .message-text {
      background: var(--wf-bg);
      padding: var(--spacing-sm) var(--spacing-md);
      border-radius: 12px;
      font-size: var(--font-size-sm);
      line-height: 1.5;
    }

    .message-user .message-text {
      background: var(--wf-primary);
      color: white;
    }

    .message-meta {
      font-size: var(--font-size-xs);
      color: var(--wf-muted);
      padding: 0 var(--spacing-sm);
    }

    .message-user .message-meta {
      text-align: right;
    }

    .chat-quick-actions {
      padding: var(--spacing-md) var(--spacing-lg);
      border-top: 1px solid var(--wf-border);
      display: flex;
      flex-wrap: wrap;
      gap: var(--spacing-sm);
      flex-shrink: 0;
    }

    .quick-action-chip {
      background: var(--wf-bg);
      border: 1px solid var(--wf-border);
      color: var(--wf-text);
      padding: var(--spacing-sm) var(--spacing-md);
      border-radius: 16px;
      font-size: var(--font-size-sm);
      cursor: pointer;
      transition: all 0.2s ease;
    }

    .quick-action-chip:hover {
      background: var(--wf-border);
      border-color: var(--wf-primary);
    }

    .quick-action-chip:focus-visible {
      outline: 2px solid var(--wf-primary);
      outline-offset: 2px;
    }

    .chat-input-container {
      padding: var(--spacing-lg);
      border-top: 1px solid var(--wf-border);
      flex-shrink: 0;
    }

    .chat-input-form {
      display: flex;
      gap: var(--spacing-sm);
      align-items: center;
    }

    .chat-input {
      flex: 1;
      background: var(--wf-bg);
      border: 1px solid var(--wf-border);
      color: var(--wf-text);
      padding: var(--spacing-md);
      border-radius: 8px;
      font-size: var(--font-size-base);
      font-family: var(--font-family);
    }

    .chat-input:focus {
      outline: none;
      border-color: var(--wf-primary);
    }

    .chat-input::placeholder {
      color: var(--wf-muted);
    }

    .chat-send-btn {
      background: var(--wf-grad);
      border: none;
      color: white;
      width: 44px;
      height: 44px;
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font-size: var(--font-size-xl);
      transition: all 0.2s ease;
    }

    .chat-send-btn:hover:not(:disabled) {
      transform: scale(1.05);
      box-shadow: var(--wf-shadow-md);
    }

    .chat-send-btn:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }

    .chat-send-btn:focus-visible {
      outline: 2px solid var(--wf-primary);
      outline-offset: 2px;
    }

    .autopilot-toggle {
      margin-top: var(--spacing-md);
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      font-size: var(--font-size-sm);
      color: var(--wf-muted);
    }

    .toggle-switch {
      position: relative;
      width: 44px;
      height: 24px;
      background: var(--wf-border);
      border-radius: 12px;
      cursor: pointer;
      transition: background 0.3s ease;
    }

    .toggle-switch.active {
      background: var(--wf-primary);
    }

    .toggle-switch::after {
      content: '';
      position: absolute;
      top: 2px;
      left: 2px;
      width: 20px;
      height: 20px;
      background: white;
      border-radius: 50%;
      transition: transform 0.3s ease;
    }

    .toggle-switch.active::after {
      transform: translateX(20px);
    }

    /* ============================================
       6. BOARD
       ============================================ */
    .board-header {
      height: var(--header-height);
      background: var(--wf-panel);
      border-bottom: 1px solid var(--wf-border);
      padding: 0 var(--spacing-lg);
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-shrink: 0;
    }

    .board-title {
      font-size: var(--font-size-xl);
      font-weight: 700;
    }

    .board-content {
      flex: 1;
      overflow-x: auto;
      overflow-y: hidden;
      padding: var(--spacing-lg);
    }

    .board-columns {
      display: flex;
      gap: var(--spacing-lg);
      height: 100%;
      min-width: min-content;
    }

    .board-column {
      flex: 0 0 280px;
      display: flex;
      flex-direction: column;
      background: var(--wf-panel);
      border-radius: 8px;
      overflow: hidden;
    }

    .column-header {
      padding: var(--spacing-md) var(--spacing-lg);
      border-bottom: 1px solid var(--wf-border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-shrink: 0;
    }

    .column-title {
      font-weight: 700;
      font-size: var(--font-size-base);
    }

    .column-count {
      background: var(--wf-bg);
      color: var(--wf-muted);
      padding: 2px 8px;
      border-radius: 12px;
      font-size: var(--font-size-xs);
      font-weight: 600;
    }

    .column-cards {
      flex: 1;
      overflow-y: auto;
      padding: var(--spacing-md);
      display: flex;
      flex-direction: column;
      gap: var(--spacing-md);
    }

    /* ============================================
       7. BOARD CARDS
       ============================================ */
    .board-card {
      background: var(--wf-bg);
      border: 1px solid var(--wf-border);
      border-radius: 8px;
      padding: var(--spacing-md);
      cursor: pointer;
      transition: all 0.2s ease;
      animation: cardEnter 0.3s ease-out;
    }

    @keyframes cardEnter {
      from {
        opacity: 0;
        transform: translateY(-10px) scale(0.95);
      }
      to {
        opacity: 1;
        transform: translateY(0) scale(1);
      }
    }

    .board-card.removing {
      animation: cardExit 0.2s ease-in forwards;
    }

    @keyframes cardExit {
      from {
        opacity: 1;
        transform: scale(1);
      }
      to {
        opacity: 0;
        transform: scale(0.9);
      }
    }

    .board-card:hover {
      border-color: var(--wf-primary);
      box-shadow: var(--wf-shadow-md);
      transform: translateY(-2px);
    }

    .board-card:focus-visible {
      outline: 2px solid var(--wf-primary);
      outline-offset: 2px;
    }

    .card-header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: var(--spacing-sm);
      margin-bottom: var(--spacing-sm);
    }

    .card-title {
      font-weight: 600;
      font-size: var(--font-size-base);
      line-height: 1.4;
      overflow: hidden;
      text-overflow: ellipsis;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      flex: 1;
    }

    .card-menu {
      background: transparent;
      border: none;
      color: var(--wf-muted);
      cursor: pointer;
      padding: 0;
      font-size: var(--font-size-lg);
      line-height: 1;
      flex-shrink: 0;
      width: 20px;
      height: 20px;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .card-menu:hover {
      color: var(--wf-text);
    }

    .card-tags {
      display: flex;
      gap: var(--spacing-xs);
      flex-wrap: wrap;
      margin-bottom: var(--spacing-sm);
    }

    .tag {
      padding: 4px 8px;
      border-radius: 4px;
      font-size: var(--font-size-xs);
      font-weight: 600;
      background: var(--wf-border);
      color: var(--wf-text);
    }

    .tag-priority-high {
      background: rgba(239, 68, 68, 0.2);
      color: #ef4444;
    }

    .tag-priority-medium {
      background: rgba(251, 191, 36, 0.2);
      color: #fbbf24;
    }

    .tag-priority-low {
      background: rgba(74, 222, 128, 0.2);
      color: #4ade80;
    }

    .tag-type-feature {
      background: rgba(110, 76, 217, 0.2);
      color: var(--wf-accent);
    }

    .tag-type-bug {
      background: rgba(228, 48, 69, 0.2);
      color: var(--wf-primary);
    }

    .card-progress {
      margin-bottom: var(--spacing-sm);
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
    }

    .progress-bar-container {
      flex: 1;
      height: 4px;
      background: var(--wf-border);
      border-radius: 2px;
      overflow: hidden;
    }

    .progress-bar-fill {
      height: 100%;
      background: var(--wf-grad);
      transition: width 0.3s ease;
    }

    .progress-label {
      font-size: var(--font-size-xs);
      color: var(--wf-muted);
      font-weight: 600;
      min-width: 35px;
      text-align: right;
    }

    .card-footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .card-meta {
      display: flex;
      gap: var(--spacing-md);
    }

    .meta-item {
      font-size: var(--font-size-xs);
      color: var(--wf-muted);
      display: flex;
      align-items: center;
      gap: 4px;
    }

    .card-assignee {
      width: 24px;
      height: 24px;
      border-radius: 50%;
      background: var(--wf-grad);
      color: white;
      font-size: var(--font-size-xs);
      font-weight: 700;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    /* Empty State */
    .empty-state {
      padding: var(--spacing-xl);
      text-align: center;
      color: var(--wf-muted);
    }

    .empty-icon {
      width: 48px;
      height: 48px;
      margin: 0 auto var(--spacing-md);
      opacity: 0.5;
    }

    .empty-title {
      font-size: var(--font-size-base);
      font-weight: 600;
      margin-bottom: var(--spacing-xs);
      color: var(--wf-text);
    }

    .empty-description {
      font-size: var(--font-size-sm);
    }

    /* ============================================
       8. METRICS PANEL
       ============================================ */
    .metrics-header {
      padding: var(--spacing-lg);
      border-bottom: 1px solid var(--wf-border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-shrink: 0;
    }

    .metrics-title {
      font-size: var(--font-size-lg);
      font-weight: 700;
    }

    .metrics-toggle {
      background: transparent;
      border: 1px solid var(--wf-border);
      color: var(--wf-text);
      width: 32px;
      height: 32px;
      border-radius: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font-size: var(--font-size-base);
    }

    .metrics-toggle:hover {
      background: var(--wf-border);
    }

    .metrics-content {
      flex: 1;
      overflow-y: auto;
      padding: var(--spacing-lg);
    }

    .metrics-section {
      margin-bottom: var(--spacing-xl);
    }

    .section-title {
      font-size: var(--font-size-base);
      font-weight: 700;
      margin-bottom: var(--spacing-md);
    }

    .metric-item {
      margin-bottom: var(--spacing-md);
    }

    .metric-label {
      font-size: var(--font-size-sm);
      color: var(--wf-muted);
      margin-bottom: var(--spacing-xs);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    .metric-value {
      font-weight: 700;
      color: var(--wf-text);
    }

    .metric-bar {
      height: 6px;
      background: var(--wf-border);
      border-radius: 3px;
      overflow: hidden;
    }

    .metric-bar-fill {
      height: 100%;
      background: var(--wf-grad);
      transition: width 0.3s ease;
    }

    /* Donut Charts */
    .efficiency-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: var(--spacing-md);
      margin-top: var(--spacing-md);
    }

    .donut-chart {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: var(--spacing-xs);
    }

    .donut-circle {
      width: 60px;
      height: 60px;
      border-radius: 50%;
      background: conic-gradient(
        var(--wf-primary) 0% var(--chart-value, 0%),
        var(--wf-border) var(--chart-value, 0%) 100%
      );
      position: relative;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .donut-inner {
      width: 44px;
      height: 44px;
      border-radius: 50%;
      background: var(--wf-panel);
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 700;
      font-size: var(--font-size-sm);
    }

    .donut-label {
      font-size: var(--font-size-xs);
      color: var(--wf-muted);
      text-align: center;
    }

    /* Daily Plan */
    .plan-list {
      display: flex;
      flex-direction: column;
      gap: var(--spacing-sm);
    }

    .plan-item {
      display: flex;
      gap: var(--spacing-sm);
      padding: var(--spacing-sm);
      background: var(--wf-bg);
      border-radius: 6px;
      font-size: var(--font-size-sm);
    }

    .plan-time {
      color: var(--wf-muted);
      font-weight: 600;
      min-width: 45px;
    }

    .plan-task {
      color: var(--wf-text);
    }

    /* ============================================
       9. UTILITIES
       ============================================ */
    .sr-only {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border-width: 0;
    }

    /* Focus Styles */
    *:focus {
      outline: none;
    }

    *:focus-visible {
      outline: 2px solid var(--wf-primary);
      outline-offset: 2px;
    }

    /* Scrollbar Styles */
    ::-webkit-scrollbar {
      width: 8px;
      height: 8px;
    }

    ::-webkit-scrollbar-track {
      background: var(--wf-panel);
    }

    ::-webkit-scrollbar-thumb {
      background: var(--wf-border);
      border-radius: 4px;
    }

    ::-webkit-scrollbar-thumb:hover {
      background: var(--wf-muted);
    }

    /* ============================================
       10. RESPONSIVE DESIGN
       ============================================ */
    @media (max-width: 1199px) {
      .app-container {
        grid-template-columns: var(--chat-width) 1fr;
        grid-template-areas: "chat board";
      }

      .metrics-panel {
        position: fixed;
        right: 0;
        top: 0;
        height: 100vh;
        z-index: 100;
        box-shadow: var(--wf-shadow-lg);
      }

      .metrics-panel:not(.expanded) {
        transform: translateX(100%);
      }
    }

    @media (max-width: 767px) {
      .app-container {
        grid-template-columns: 1fr;
        grid-template-areas: "board";
      }

      .chat-panel {
        position: fixed;
        left: 0;
        top: 0;
        height: 100vh;
        z-index: 100;
        transform: translateX(-100%);
        transition: transform 0.3s ease;
        box-shadow: var(--wf-shadow-lg);
      }

      .chat-panel.expanded {
        transform: translateX(0);
      }

      .board-columns {
        flex-direction: column;
      }

      .board-column {
        flex: 0 0 auto;
        min-height: 300px;
      }
    }

    /* ============================================
       11. LOADING & SKELETON STATES
       ============================================ */
    @keyframes shimmer {
      0% { background-position: -1000px 0; }
      100% { background-position: 1000px 0; }
    }

    .skeleton {
      background: linear-gradient(
        90deg,
        var(--wf-panel) 0%,
        var(--wf-border) 50%,
        var(--wf-panel) 100%
      );
      background-size: 1000px 100%;
      animation: shimmer 2s infinite;
      border-radius: 4px;
    }

    .skeleton-card {
      width: 100%;
      height: 120px;
      margin-bottom: var(--spacing-md);
    }

    .skeleton-message {
      width: 75%;
      height: 60px;
      margin-bottom: var(--spacing-md);
    }

    /* ============================================
       12. NOTIFICATIONS
       ============================================ */
    .notification-container {
      position: fixed;
      top: var(--spacing-lg);
      right: var(--spacing-lg);
      z-index: 1000;
      display: flex;
      flex-direction: column;
      gap: var(--spacing-sm);
      pointer-events: none;
    }

    .notification {
      background: var(--wf-panel);
      border: 1px solid var(--wf-border);
      border-radius: 8px;
      padding: var(--spacing-md);
      box-shadow: var(--wf-shadow-lg);
      min-width: 300px;
      pointer-events: auto;
      animation: notificationEnter 0.3s ease-out;
    }

    @keyframes notificationEnter {
      from {
        opacity: 0;
        transform: translateX(100px);
      }
      to {
        opacity: 1;
        transform: translateX(0);
      }
    }

    .notification.removing {
      animation: notificationExit 0.2s ease-in forwards;
    }

    @keyframes notificationExit {
      from {
        opacity: 1;
        transform: translateX(0);
      }
      to {
        opacity: 0;
        transform: translateX(100px);
      }
    }

    .notification-success {
      border-left: 4px solid var(--wf-success);
    }

    .notification-error {
      border-left: 4px solid var(--wf-error);
    }

    .notification-info {
      border-left: 4px solid var(--wf-info);
    }

    .notification-title {
      font-weight: 600;
      margin-bottom: var(--spacing-xs);
    }

    .notification-message {
      font-size: var(--font-size-sm);
      color: var(--wf-muted);
    }
  </style>
</head>
<body>
  <div class="app-container" role="application" aria-label="WordFlux Cockpit">
    <!-- Chat Panel -->
    <aside class="chat-panel" role="complementary" aria-label="Chat com assistente">
      <div class="chat-header">
        <div class="chat-title">
          <span style="color: var(--wf-pink);">●</span> WordFlux AI
        </div>
        <div class="chat-subtitle">IA pronta para comandar o fluxo</div>
      </div>

      <div class="chat-messages" role="log" aria-live="polite" aria-atomic="false">
        <!-- Initial message -->
        <div class="chat-message message-assistant">
          <div class="message-avatar">W</div>
          <div class="message-content">
            <div class="message-text">
              Olá! Posso criar, mover e resumir tarefas para você.<br>
              Experimente "Crie uma tarefa em Doing" ou "Resumo do quadro".
            </div>
            <div class="message-meta">
              <span class="timestamp">Agora</span>
            </div>
          </div>
        </div>
      </div>

      <div class="chat-quick-actions">
        <button class="quick-action-chip" data-action="plan" aria-label="Sugestão: Planeje o amanhã">Planeje o amanhã</button>
        <button class="quick-action-chip" data-action="my-tasks" aria-label="Sugestão: Mostrar minhas tarefas">Mostrar minhas tarefas</button>
        <button class="quick-action-chip" data-action="clear" aria-label="Sugestão: Limpar Concluído">Limpar Concluído</button>
      </div>

      <div class="chat-input-container">
        <form class="chat-input-form">
          <input
            type="text"
            class="chat-input"
            placeholder="Digite sua mensagem..."
            aria-label="Digite sua mensagem"
            autocomplete="off"
          />
          <button type="submit" class="chat-send-btn" aria-label="Enviar mensagem">
            →
          </button>
        </form>

        <div class="autopilot-toggle">
          <div class="toggle-switch" role="switch" aria-checked="false" aria-label="Ativar autopilot" tabindex="0"></div>
          <span>Autopilot: Agente age automaticamente</span>
        </div>
      </div>
    </aside>

    <!-- Board Container -->
    <div class="board-container" role="main">
      <div class="board-header">
        <h1 class="board-title">Quadro</h1>
        <div class="header-actions">
          <div class="connection-status" data-connected="false">
            <span class="status-dot"></span>
            <span class="status-label">Conectando...</span>
          </div>
          <button class="theme-toggle" aria-label="Alternar tema" title="Alternar tema">
            ☀️
          </button>
        </div>
      </div>

      <div class="board-content">
        <div class="board-columns">
          <!-- Backlog Column -->
          <section class="board-column" role="region" aria-label="Espera">
            <div class="column-header">
              <h2 class="column-title">Espera</h2>
              <span class="column-count">0</span>
            </div>
            <div class="column-cards" data-column="backlog" role="list">
              <div class="empty-state">
                <svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <rect x="3" y="3" width="18" height="18" rx="2"/>
                </svg>
                <div class="empty-title">Nenhum card aqui</div>
                <div class="empty-description">Cards aparecerão aqui quando criados</div>
              </div>
            </div>
          </section>

          <!-- In Progress Column -->
          <section class="board-column" role="region" aria-label="Produção">
            <div class="column-header">
              <h2 class="column-title">Produção</h2>
              <span class="column-count">0</span>
            </div>
            <div class="column-cards" data-column="doing" role="list">
              <div class="empty-state">
                <svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <rect x="3" y="3" width="18" height="18" rx="2"/>
                </svg>
                <div class="empty-title">Nenhum card aqui</div>
                <div class="empty-description">Cards em progresso aparecerão aqui</div>
              </div>
            </div>
          </section>

          <!-- Review Column -->
          <section class="board-column" role="region" aria-label="Aprovação">
            <div class="column-header">
              <h2 class="column-title">Aprovação</h2>
              <span class="column-count">0</span>
            </div>
            <div class="column-cards" data-column="review" role="list">
              <div class="empty-state">
                <svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <rect x="3" y="3" width="18" height="18" rx="2"/>
                </svg>
                <div class="empty-title">Nenhum card aqui</div>
                <div class="empty-description">Cards aguardando aprovação aparecerão aqui</div>
              </div>
            </div>
          </section>

          <!-- Scheduled Column -->
          <section class="board-column" role="region" aria-label="Agendado">
            <div class="column-header">
              <h2 class="column-title">Agendado</h2>
              <span class="column-count">0</span>
            </div>
            <div class="column-cards" data-column="scheduled" role="list">
              <div class="empty-state">
                <svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <rect x="3" y="3" width="18" height="18" rx="2"/>
                </svg>
                <div class="empty-title">Nenhum card aqui</div>
                <div class="empty-description">Cards agendados aparecerão aqui</div>
              </div>
            </div>
          </section>

          <!-- Done Column -->
          <section class="board-column" role="region" aria-label="Finalizado">
            <div class="column-header">
              <h2 class="column-title">Finalizado</h2>
              <span class="column-count">0</span>
            </div>
            <div class="column-cards" data-column="done" role="list">
              <div class="empty-state">
                <svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <rect x="3" y="3" width="18" height="18" rx="2"/>
                </svg>
                <div class="empty-title">Nenhum card aqui</div>
                <div class="empty-description">Cards finalizados aparecerão aqui</div>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>

    <!-- Metrics Panel -->
    <aside class="metrics-panel" role="complementary" aria-label="Métricas e estatísticas">
      <div class="metrics-header">
        <h2 class="metrics-title">Métricas</h2>
        <button class="metrics-toggle" aria-label="Recolher painel" title="Recolher">
          «
        </button>
      </div>

      <div class="metrics-content">
        <!-- Tasks Completed Section -->
        <section class="metrics-section">
          <h3 class="section-title">Tarefas Concluídas</h3>

          <div class="metric-item">
            <div class="metric-label">
              <span>Esta semana</span>
              <span class="metric-value">12</span>
            </div>
            <div class="metric-bar">
              <div class="metric-bar-fill" style="width: 80%;"></div>
            </div>
          </div>

          <div class="metric-item">
            <div class="metric-label">
              <span>Este mês</span>
              <span class="metric-value">45</span>
            </div>
            <div class="metric-bar">
              <div class="metric-bar-fill" style="width: 90%;"></div>
            </div>
          </div>
        </section>

        <!-- Efficiency Section -->
        <section class="metrics-section">
          <h3 class="section-title">Eficiência</h3>

          <div class="efficiency-grid">
            <div class="donut-chart">
              <div class="donut-circle" style="--chart-value: 92%;">
                <div class="donut-inner">92%</div>
              </div>
              <div class="donut-label">WIP</div>
            </div>

            <div class="donut-chart">
              <div class="donut-circle" style="--chart-value: 85%;">
                <div class="donut-inner">85%</div>
              </div>
              <div class="donut-label">Vel</div>
            </div>

            <div class="donut-chart">
              <div class="donut-circle" style="--chart-value: 78%;">
                <div class="donut-inner">78%</div>
              </div>
              <div class="donut-label">Qua</div>
            </div>

            <div class="donut-chart">
              <div class="donut-circle" style="--chart-value: 95%;">
                <div class="donut-inner">95%</div>
              </div>
              <div class="donut-label">Ent</div>
            </div>
          </div>
        </section>

        <!-- Daily Plan Section -->
        <section class="metrics-section">
          <h3 class="section-title">Plano Diário</h3>

          <div class="plan-list">
            <div class="plan-item">
              <span class="plan-time">09:00</span>
              <span class="plan-task">Daily Standup</span>
            </div>
            <div class="plan-item">
              <span class="plan-time">14:00</span>
              <span class="plan-task">Code Review</span>
            </div>
            <div class="plan-item">
              <span class="plan-time">16:00</span>
              <span class="plan-task">Deploy Release</span>
            </div>
          </div>
        </section>
      </div>
    </aside>
  </div>

  <!-- Notification Container -->
  <div class="notification-container" role="status" aria-live="polite"></div>

  <!-- Screen Reader Announcements -->
  <div class="sr-only" role="status" aria-live="polite" aria-atomic="true"></div>

  <script>
    // ============================================
    // 1. STATE MANAGEMENT
    // ============================================
    const AppState = {
      session_id: `sess-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
      theme: 'dark',
      autopilot: false,
      connected: false,
      board: {
        backlog: [],
        doing: [],
        review: [],
        scheduled: [],
        done: []
      },
      metrics: {
        weekCompleted: 12,
        monthCompleted: 45,
        efficiency: { wip: 92, velocity: 85, quality: 78, delivery: 95 }
      },

      update(key, value) {
        this[key] = value;
        window.dispatchEvent(new CustomEvent('state-change', {
          detail: { key, value }
        }));
      }
    };

    // ============================================
    // 2. UTILITIES
    // ============================================
    function escapeHTML(str) {
      const div = document.createElement('div');
      div.textContent = str;
      return div.innerHTML;
    }

    function formatTime(date) {
      if (!date) return '';
      const d = new Date(date);
      return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
    }

    function getInitials(name) {
      if (!name) return '?';
      return name
        .split(' ')
        .map(n => n[0])
        .join('')
        .toUpperCase()
        .substr(0, 2);
    }

    function announce(message) {
      const liveRegion = document.querySelector('.sr-only[role="status"]');
      if (liveRegion) {
        liveRegion.textContent = message;
        setTimeout(() => liveRegion.textContent = '', 1000);
      }
    }

    // ============================================
    // 3. THEME MANAGEMENT
    // ============================================
    const ThemeManager = {
      init() {
        const saved = localStorage.getItem('wf-theme');
        const system = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
        const theme = saved || system;

        this.setTheme(theme, false);

        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
          if (!localStorage.getItem('wf-theme')) {
            this.setTheme(e.matches ? 'dark' : 'light', false);
          }
        });
      },

      setTheme(theme, save = true) {
        document.documentElement.dataset.theme = theme;
        AppState.theme = theme;

        if (save) {
          localStorage.setItem('wf-theme', theme);
        }

        const toggle = document.querySelector('.theme-toggle');
        if (toggle) {
          toggle.setAttribute('aria-label', theme === 'dark' ? 'Ativar modo claro' : 'Ativar modo escuro');
          toggle.textContent = theme === 'dark' ? '☀️' : '🌙';
        }
      },

      toggle() {
        document.documentElement.dataset.themeChanging = '';

        const newTheme = AppState.theme === 'dark' ? 'light' : 'dark';
        this.setTheme(newTheme);

        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            delete document.documentElement.dataset.themeChanging;
          });
        });
      }
    };

    // ============================================
    // 4. NOTIFICATION SYSTEM
    // ============================================
    const NotificationManager = {
      show(type, title, message, duration = 5000) {
        const container = document.querySelector('.notification-container');
        const notification = document.createElement('div');
        notification.className = `notification notification-${type}`;
        notification.innerHTML = `
          <div class="notification-title">${escapeHTML(title)}</div>
          <div class="notification-message">${escapeHTML(message)}</div>
        `;

        container.appendChild(notification);

        setTimeout(() => {
          notification.classList.add('removing');
          notification.addEventListener('animationend', () => notification.remove(), { once: true });
        }, duration);
      }
    };

    // ============================================
    // 5. CHAT MANAGEMENT
    // ============================================
    const ChatManager = {
      messagesContainer: null,
      inputForm: null,
      inputField: null,

      init() {
        this.messagesContainer = document.querySelector('.chat-messages');
        this.inputForm = document.querySelector('.chat-input-form');
        this.inputField = document.querySelector('.chat-input');

        this.inputForm.addEventListener('submit', (e) => {
          e.preventDefault();
          this.sendMessage();
        });

        // Quick actions
        document.querySelectorAll('.quick-action-chip').forEach(chip => {
          chip.addEventListener('click', () => {
            const action = chip.dataset.action;
            const messages = {
              'plan': 'Planeje o amanhã',
              'my-tasks': 'Mostre minhas tarefas',
              'clear': 'Limpe os cards concluídos'
            };
            this.inputField.value = messages[action] || '';
            this.sendMessage();
          });
        });
      },

      async sendMessage() {
        const message = this.inputField.value.trim();
        if (!message) return;

        // Clear input
        this.inputField.value = '';

        // Add user message
        this.addMessage('user', message);

        // Show loading state
        const loadingId = this.addMessage('assistant', 'Pensando...', null, true);

        try {
          const response = await fetch('/chat/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              message: message,
              session_id: AppState.session_id
            }),
            signal: AbortSignal.timeout(30000)
          });

          if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
          }

          const data = await response.json();

          // Remove loading message
          this.removeMessage(loadingId);

          // Handle approval flow with Autopilot integration
          if (data.requires_approval) {
            // Show confirmation message
            this.addMessage('assistant', data.message);

            // Auto-approve if Autopilot is enabled
            if (AppState.autopilot) {
              // Small delay for UX feedback (user sees the confirmation message first)
              setTimeout(() => {
                this.inputField.value = 'sim';
                this.sendMessage();
              }, 800);
            }
          } else if (data.message) {
            // Normal low-risk flow - show response
            this.addMessage('assistant', data.message);
          }

        } catch (error) {
          this.removeMessage(loadingId);

          let errorMsg = 'Erro ao enviar mensagem';
          if (error.name === 'AbortError') {
            errorMsg = 'Timeout: O servidor demorou muito para responder';
          } else {
            errorMsg = `Erro: ${error.message}`;
          }

          this.addMessage('system', errorMsg);
          NotificationManager.show('error', 'Erro', errorMsg);
        }
      },

      addMessage(role, content, timestamp = null, isLoading = false) {
        const messageId = `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

        // Remove empty state if exists
        const emptyState = this.messagesContainer.querySelector('.empty-state');
        if (emptyState) emptyState.remove();

        const message = document.createElement('div');
        message.className = `chat-message message-${role}`;
        message.dataset.messageId = messageId;

        if (isLoading) {
          message.dataset.loading = 'true';
        }

        const avatar = role === 'assistant' ? 'W' : role === 'user' ? 'U' : 'S';
        const time = timestamp ? formatTime(timestamp) : formatTime(new Date());

        message.innerHTML = `
          <div class="message-avatar">${avatar}</div>
          <div class="message-content">
            <div class="message-text">${escapeHTML(content)}</div>
            <div class="message-meta">
              <span class="timestamp">${time}</span>
            </div>
          </div>
        `;

        this.messagesContainer.appendChild(message);
        message.scrollIntoView({ behavior: 'smooth', block: 'end' });

        return messageId;
      },

      removeMessage(messageId) {
        const message = this.messagesContainer.querySelector(`[data-message-id="${messageId}"]`);
        if (message) {
          message.remove();
        }
      },

      handleSSEMessage(event) {
        // SSE will send chat_message events
        if (event.message) {
          this.addMessage('assistant', event.message, event.timestamp);
        }
      },

      handlePendingConfirmation(event) {
        // event: { kind, token, summary, session_id?, ts }
        // Show confirmation message in chat
        this.addMessage('assistant', `⚠️ ${event.summary} (confirme com "sim" ou "não")`);
      }
    };

    // ============================================
    // 6. BOARD MANAGEMENT
    // ============================================
    const BoardManager = {
      columns: null,

      init() {
        this.columns = {
          backlog: document.querySelector('[data-column="backlog"]'),
          doing: document.querySelector('[data-column="doing"]'),
          review: document.querySelector('[data-column="review"]'),
          scheduled: document.querySelector('[data-column="scheduled"]'),
          done: document.querySelector('[data-column="done"]')
        };

        // Add event delegation for card clicks
        document.querySelector('.board-content').addEventListener('click', (e) => {
          const card = e.target.closest('.board-card');
          if (card) {
            this.handleCardClick(card);
          }
        });

        // Load initial board state
        this.fetchBoardState();
      },

      async fetchBoardState() {
        try {
          const response = await fetch('/board/state');
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
          }

          const data = await response.json();

          // Novo formato: { columns: { "Espera": [Card], "Produção": [Card], ... }, autopilot: bool }
          if (!data.columns || typeof data.columns !== 'object') {
            console.error('[Board] ⚠️ Estado do board inválido:', data);
            NotificationManager.show('error', 'Erro', 'Estado do board inválido (schema)');
            return;
          }

          // Limpar board atual
          Object.values(this.columns).forEach(column => {
            if (column) {
              column.innerHTML = '';
            }
          });

          // Renderizar cards por coluna
          Object.entries(data.columns).forEach(([colName, cards]) => {
            const colId = this.normalizeColumnId(colName);
            if (colId && this.columns[colId]) {
              cards.forEach(card => {
                this.addCard(colId, card);
              });
            }
          });

          // Update autopilot state
          if (typeof data.autopilot === 'boolean') {
            AppState.update('autopilot', data.autopilot);
          }

          console.log(`[Board] Carregado: ${Object.keys(data.columns).length} colunas`);

        } catch (error) {
          console.error('[Board] ⚠️ Erro ao carregar estado do board:', error);
          NotificationManager.show('error', 'Erro', `Falha ao carregar board: ${error.message}`);
        }
      },

      createCard(cardData) {
        const { id, title, tags = [], progress, attachments = 0, comments = 0, assignee } = cardData;

        const card = document.createElement('article');
        card.className = 'board-card';
        card.dataset.cardId = id;
        card.tabIndex = 0;
        card.setAttribute('role', 'article');
        card.setAttribute('aria-label', `Card: ${title}`);

        let html = `
          <div class="card-header">
            <h3 class="card-title">${escapeHTML(title)}</h3>
            <button class="card-menu" aria-label="Ações do card">⋮</button>
          </div>
        `;

        if (tags.length > 0) {
          html += `
            <div class="card-tags">
              ${tags.map(tag => `<span class="tag tag-${tag.type || 'default'}">${escapeHTML(tag.label)}</span>`).join('')}
            </div>
          `;
        }

        if (progress != null) {
          html += `
            <div class="card-progress">
              <div class="progress-bar-container">
                <div class="progress-bar-fill" style="width: ${progress}%;"></div>
              </div>
              <span class="progress-label">${progress}%</span>
            </div>
          `;
        }

        html += `
          <div class="card-footer">
            <div class="card-meta">
              ${attachments > 0 ? `<span class="meta-item">📎 ${attachments}</span>` : ''}
              ${comments > 0 ? `<span class="meta-item">💬 ${comments}</span>` : ''}
            </div>
            ${assignee ? `
              <div class="card-assignee" title="${assignee.name}">
                ${assignee.avatar || getInitials(assignee.name)}
              </div>
            ` : ''}
          </div>
        `;

        card.innerHTML = html;
        return card;
      },

      addCard(columnId, cardData) {
        const column = this.columns[columnId];
        if (!column) return;

        // Remove empty state
        const emptyState = column.querySelector('.empty-state');
        if (emptyState) emptyState.remove();

        const card = this.createCard(cardData);
        column.appendChild(card);

        // Update count
        this.updateColumnCount(columnId);

        // Add to state
        AppState.board[columnId].push(cardData);

        announce(`Card adicionado a ${this.getColumnName(columnId)}`);
      },

      removeCard(cardId) {
        const card = document.querySelector(`[data-card-id="${cardId}"]`);
        if (!card) return;

        const column = card.closest('[data-column]');
        const columnId = column?.dataset.column;

        card.classList.add('removing');
        card.addEventListener('animationend', () => {
          card.remove();

          // Update count
          if (columnId) {
            this.updateColumnCount(columnId);

            // Show empty state if needed
            if (column.children.length === 0) {
              this.showEmptyState(column);
            }
          }
        }, { once: true });

        // Remove from state
        if (columnId) {
          AppState.board[columnId] = AppState.board[columnId].filter(c => c.id !== cardId);
        }
      },

      moveCard(cardId, fromColumn, toColumn) {
        const card = document.querySelector(`[data-card-id="${cardId}"]`);
        if (!card) return;

        const targetColumn = this.columns[toColumn];
        if (!targetColumn) return;

        // Remove empty state from target
        const emptyState = targetColumn.querySelector('.empty-state');
        if (emptyState) emptyState.remove();

        // Move card
        targetColumn.appendChild(card);

        // Update counts
        this.updateColumnCount(fromColumn);
        this.updateColumnCount(toColumn);

        // Update state
        const cardData = AppState.board[fromColumn].find(c => c.id === cardId);
        if (cardData) {
          AppState.board[fromColumn] = AppState.board[fromColumn].filter(c => c.id !== cardId);
          AppState.board[toColumn].push(cardData);
        }

        announce(`Card movido para ${this.getColumnName(toColumn)}`);
      },

      updateColumnCount(columnId) {
        const column = this.columns[columnId];
        if (!column) return;

        const cards = column.querySelectorAll('.board-card');
        const count = cards.length;

        const countBadge = column.closest('.board-column').querySelector('.column-count');
        if (countBadge) {
          countBadge.textContent = count;
        }
      },

      showEmptyState(column) {
        const columnId = column.dataset.column;
        const names = {
          backlog: 'Cards aparecerão aqui quando criados',
          doing: 'Cards em progresso aparecerão aqui',
          review: 'Cards aguardando aprovação aparecerão aqui',
          scheduled: 'Cards agendados aparecerão aqui',
          done: 'Cards finalizados aparecerão aqui'
        };

        column.innerHTML = `
          <div class="empty-state">
            <svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="3" width="18" height="18" rx="2"/>
            </svg>
            <div class="empty-title">Nenhum card aqui</div>
            <div class="empty-description">${names[columnId] || ''}</div>
          </div>
        `;
      },

      getColumnName(columnId) {
        const names = {
          backlog: 'Espera',
          doing: 'Produção',
          review: 'Aprovação',
          scheduled: 'Agendado',
          done: 'Finalizado'
        };
        return names[columnId] || columnId;
      },

      handleCardClick(card) {
        console.log('Card clicked:', card.dataset.cardId);
        // TODO: Open card modal or show details
      },

      handleBoardUpdate(event) {
        // Handle SSE board_update events (legacy format)
        if (event.action === 'add') {
          this.addCard(event.column, event.card);
        } else if (event.action === 'remove') {
          this.removeCard(event.card_id);
        } else if (event.action === 'move') {
          this.moveCard(event.card_id, event.from_column, event.to_column);
        }
      },

      handleCardCreated(event) {
        // event: { kind, id, title, list, meta?, ts }
        const card = {
          id: event.id,
          title: event.title,
          meta: event.meta || {}
        };
        const colId = this.normalizeColumnId(event.list);
        if (colId) {
          this.addCard(colId, card);
          announce(`Card criado: ${event.title}`);
        }
      },

      handleCardMoved(event) {
        // event: { kind, id, title, from_list, to_list, meta?, ts }
        const from = this.normalizeColumnId(event.from_list);
        const to = this.normalizeColumnId(event.to_list);
        if (from && to) {
          this.moveCard(event.id, from, to);
          announce(`Card movido para ${event.to_list}`);
        }
      },

      handleCardUpdated(event) {
        // event: { kind, id, title, list, fields_updated, meta?, ts }
        const card = document.querySelector(`[data-card-id="${event.id}"]`);
        if (card && event.fields_updated.includes('title')) {
          const titleEl = card.querySelector('.card-title');
          if (titleEl) {
            titleEl.textContent = event.title;
          }
        }
        announce(`Card atualizado: ${event.title}`);
      },

      normalizeColumnId(columnName) {
        // Map Portuguese column names to DOM column IDs
        const mapping = {
          'Espera': 'backlog',
          'Produção': 'doing',
          'Aprovação': 'review',
          'Agendado': 'scheduled',
          'Finalizado': 'done'
        };
        return mapping[columnName] || columnName.toLowerCase();
      },

      loadMockData() {
        // Add some mock cards for testing
        this.addCard('backlog', {
          id: 'card-1',
          title: 'Implement dark mode toggle',
          tags: [
            { label: 'urgent', type: 'priority-high' },
            { label: 'feature', type: 'type-feature' }
          ],
          progress: 0,
          attachments: 2,
          comments: 1,
          assignee: { name: 'John Doe' }
        });

        this.addCard('doing', {
          id: 'card-2',
          title: 'Fix authentication bug',
          tags: [
            { label: 'bug', type: 'type-bug' },
            { label: 'high', type: 'priority-high' }
          ],
          progress: 60,
          comments: 3,
          assignee: { name: 'Jane Smith' }
        });

        this.addCard('review', {
          id: 'card-3',
          title: 'Update documentation',
          tags: [
            { label: 'docs', type: 'default' },
            { label: 'low', type: 'priority-low' }
          ],
          progress: 90,
          attachments: 1,
          assignee: { name: 'Bob Johnson' }
        });
      }
    };

    // ============================================
    // 7. SSE MANAGEMENT
    // ============================================

    // Validate SSE event against schema
    function validateSSEEvent(event) {
      // Validar estrutura básica
      if (!event || typeof event !== 'object') {
        console.warn('[SSE] ⚠️ Evento SSE inválido (não é objeto):', event);
        return false;
      }

      if (!event.kind || typeof event.kind !== 'string') {
        console.warn('[SSE] ⚠️ Evento SSE inválido (falta campo "kind"):', event);
        return false;
      }

      if (!event.ts || typeof event.ts !== 'number') {
        console.warn('[SSE] ⚠️ Evento SSE inválido (falta campo "ts"):', event);
        return false;
      }

      // Validar schemas específicos por tipo
      const schemas = {
        'chat_message': (e) => e.role && e.text,
        'card.created': (e) => e.id && e.title && e.list,
        'card.moved': (e) => e.id && e.title && e.from_list && e.to_list,
        'card.updated': (e) => e.id && e.title && e.list && Array.isArray(e.fields_updated),
        'pending.confirmation': (e) => e.token && e.summary,
        'job.queued': (e) => e.job_id && e.action,
        'job_started': (e) => e.job_id && e.action,
        'job_succeeded': (e) => e.job_id && e.action,
        'job_failed': (e) => e.job_id && e.action && e.error,
        'board_update': (e) => Array.isArray(e.cards),  // Legacy support
      };

      const validator = schemas[event.kind];
      if (validator && !validator(event)) {
        console.warn(`[SSE] ⚠️ Evento SSE inválido (schema): tipo="${event.kind}"`, event);
        return false;
      }

      return true;
    }

    class SSEManager {
      constructor() {
        this.eventSource = null;
        this.reconnectTimeout = null;
        this.heartbeatTimeout = null;
        this.reconnectAttempts = 0;
      }

      connect() {
        try {
          this.eventSource = new EventSource('/events/stream');

          this.eventSource.onmessage = (e) => {
            // Handle heartbeat
            if (e.data.startsWith(': hb')) {
              this.resetHeartbeatTimer();
              return;
            }

            try {
              const event = JSON.parse(e.data.replace(/^data: /, ''));

              // Validar evento antes de processar
              if (!validateSSEEvent(event)) {
                NotificationManager.show('warning', 'Aviso', 'Evento SSE inválido ignorado (verifique console)');
                return;
              }

              this.routeEvent(event);
            } catch (err) {
              console.error('[SSE] ⚠️ Erro ao processar evento SSE:', err, e.data);
              NotificationManager.show('error', 'Erro', 'Falha ao processar evento SSE (schema inválido)');
            }
          };

          this.eventSource.onerror = (err) => {
            console.error('SSE error:', err);
            this.handleDisconnect();
          };

          this.eventSource.onopen = () => {
            console.log('SSE connected');
            this.reconnectAttempts = 0;
            this.updateConnectionStatus(true);
            this.resetHeartbeatTimer();
          };

        } catch (error) {
          console.error('Failed to create EventSource:', error);
          this.handleDisconnect();
        }
      }

      routeEvent(event) {
        console.log('SSE event:', event);

        const handlers = {
          'chat_message': (e) => ChatManager.handleSSEMessage(e),
          'board_update': (e) => BoardManager.handleBoardUpdate(e),  // Legacy
          'card.created': (e) => BoardManager.handleCardCreated(e),
          'card.moved': (e) => BoardManager.handleCardMoved(e),
          'card.updated': (e) => BoardManager.handleCardUpdated(e),
          'pending.confirmation': (e) => ChatManager.handlePendingConfirmation(e),
          'job_started': (e) => this.handleJobEvent(e, 'started'),
          'job_succeeded': (e) => this.handleJobEvent(e, 'success'),
          'job_failed': (e) => this.handleJobEvent(e, 'error'),
          'wip_limit_exceeded': (e) => this.handleWIPLimit(e)
        };

        const handler = handlers[event.kind];
        if (handler) {
          handler(event);
        } else {
          console.warn('Unknown event type:', event.kind);
        }
      }

      resetHeartbeatTimer() {
        clearTimeout(this.heartbeatTimeout);
        this.heartbeatTimeout = setTimeout(() => {
          console.warn('Heartbeat timeout - connection may be stale');
          this.handleDisconnect();
        }, 35000); // 35s timeout (heartbeat is 30s)
      }

      handleDisconnect() {
        this.updateConnectionStatus(false);
        this.eventSource?.close();

        // Exponential backoff reconnect
        const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
        console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts + 1})`);

        this.reconnectTimeout = setTimeout(() => {
          this.reconnectAttempts++;
          this.connect();
        }, delay);
      }

      updateConnectionStatus(connected) {
        AppState.update('connected', connected);

        const status = document.querySelector('.connection-status');
        if (status) {
          status.dataset.connected = connected;
          status.querySelector('.status-label').textContent = connected ? 'Conectado' : 'Desconectado';
        }
      }

      handleJobEvent(event, type) {
        const titles = {
          'started': 'Job Iniciado',
          'success': 'Job Concluído',
          'error': 'Job Falhou'
        };

        NotificationManager.show(
          type === 'started' ? 'info' : type,
          titles[type],
          event.job_id || 'Job processado'
        );
      }

      handleWIPLimit(event) {
        NotificationManager.show(
          'warning',
          'Limite WIP Excedido',
          'Não é possível adicionar mais cards em progresso'
        );
      }

      disconnect() {
        clearTimeout(this.reconnectTimeout);
        clearTimeout(this.heartbeatTimeout);
        this.eventSource?.close();
      }
    }

    // ============================================
    // 8. KEYBOARD NAVIGATION
    // ============================================
    const KeyboardNav = {
      init() {
        document.addEventListener('keydown', this.handleKeydown.bind(this));
      },

      handleKeydown(e) {
        // Escape to close error messages and modals
        if (e.key === 'Escape') {
          // Close error/system messages
          const errorMessages = document.querySelectorAll('.message-system');
          errorMessages.forEach(msg => msg.remove());

          // Clear any active focus
          if (document.activeElement && document.activeElement.tagName !== 'BODY') {
            document.activeElement.blur();
          }
        }

        // Ctrl/Cmd + K to focus chat input
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
          e.preventDefault();
          document.querySelector('.chat-input')?.focus();
        }

        // Arrow keys for card navigation
        if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) {
          this.navigateCards(e);
        }
      },

      navigateCards(e) {
        const focused = document.activeElement;
        if (!focused.closest('.board-card')) return;

        const card = focused.closest('.board-card');
        let target;

        switch (e.key) {
          case 'ArrowDown':
            target = card.nextElementSibling;
            if (target?.classList.contains('empty-state')) target = null;
            break;
          case 'ArrowUp':
            target = card.previousElementSibling;
            if (target?.classList.contains('empty-state')) target = null;
            break;
          case 'ArrowRight':
            const nextColumn = card.closest('.board-column').nextElementSibling;
            target = nextColumn?.querySelector('.board-card');
            break;
          case 'ArrowLeft':
            const prevColumn = card.closest('.board-column').previousElementSibling;
            target = prevColumn?.querySelector('.board-card');
            break;
        }

        if (target) {
          e.preventDefault();
          target.focus();
        }
      }
    };

    // ============================================
    // 9. AUTOPILOT TOGGLE
    // ============================================
    const AutopilotManager = {
      init() {
        const toggle = document.querySelector('.toggle-switch');
        if (toggle) {
          toggle.addEventListener('click', () => this.toggle());
          toggle.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              this.toggle();
            }
          });
        }
      },

      async toggle() {
        const newState = !AppState.autopilot;

        try {
          const response = await fetch('/agent/autopilot', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: newState })
          });

          if (!response.ok) throw new Error('Failed to toggle autopilot');

          AppState.update('autopilot', newState);
          this.updateUI(newState);

          NotificationManager.show(
            'success',
            'Autopilot',
            newState ? 'Autopilot ativado' : 'Autopilot desativado'
          );

        } catch (error) {
          console.error('Failed to toggle autopilot:', error);
          NotificationManager.show('error', 'Erro', 'Falha ao alterar autopilot');
        }
      },

      updateUI(enabled) {
        const toggle = document.querySelector('.toggle-switch');
        if (toggle) {
          toggle.classList.toggle('active', enabled);
          toggle.setAttribute('aria-checked', enabled);
        }
      }
    };

    // ============================================
    // 10. METRICS PANEL TOGGLE
    // ============================================
    const MetricsManager = {
      init() {
        const toggle = document.querySelector('.metrics-toggle');
        if (toggle) {
          toggle.addEventListener('click', () => this.togglePanel());
        }
      },

      togglePanel() {
        const panel = document.querySelector('.metrics-panel');
        const toggle = document.querySelector('.metrics-toggle');

        if (panel.classList.contains('collapsed')) {
          panel.classList.remove('collapsed');
          toggle.textContent = '«';
          toggle.setAttribute('aria-label', 'Recolher painel');
        } else {
          panel.classList.add('collapsed');
          toggle.textContent = '»';
          toggle.setAttribute('aria-label', 'Expandir painel');
        }
      }
    };

    // ============================================
    // 11. APPLICATION INITIALIZATION
    // ============================================
    const App = {
      state: AppState,
      sse: null,

      init() {
        console.log('Initializing WordFlux Cockpit...');
        console.log('Session ID:', AppState.session_id);

        // Initialize managers
        ThemeManager.init();
        KeyboardNav.init();
        ChatManager.init();
        BoardManager.init();
        AutopilotManager.init();
        MetricsManager.init();

        // Connect SSE
        this.sse = new SSEManager();
        this.sse.connect();

        // Bind theme toggle
        document.querySelector('.theme-toggle')?.addEventListener('click', () => {
          ThemeManager.toggle();
        });

        // Load mock data (for demo)
        setTimeout(() => {
          BoardManager.loadMockData();
        }, 500);

        console.log('WordFlux Cockpit initialized successfully');
      }
    };

    // Initialize on DOMContentLoaded
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => App.init());
    } else {
      App.init();
    }
  </script>
</body>
</html>
"""


def build_app() -> FastAPI:
    app = FastAPI(title="WordFlux Cockpit - Queue Integrated")

    @app.get("/health")
    def health():
        """Health check with queue status."""
        try:
            queue = get_queue_manager()
            depth = 0
            if hasattr(queue, 'depth'):
                depth = queue.depth()

            return {
                "status": "ok",
                "ts": int(time.time()),
                "queue_mode": QUEUE_MODE,
                "queue_depth": depth,
                "redis": rclient().ping()
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @app.get("/health/pubsub")
    async def health_pubsub():
        """Check Redis pubsub health and active SSE connections."""
        import redis.asyncio as aioredis

        try:
            r = await aioredis.from_url(REDIS_URL, decode_responses=True)
            # Get number of subscribers to the events channel
            numsub_result = await r.execute_command("PUBSUB", "NUMSUB", WF_EVENTS_CHANNEL)
            await r.close()

            # numsub_result is a list like ['wf:events', 1]
            subscriber_count = numsub_result[1] if len(numsub_result) > 1 else 0

            return {
                "status": "ok",
                "channel": WF_EVENTS_CHANNEL,
                "subscribers": subscriber_count,
                "note": "This shows active SSE connections to /events/stream"
            }
        except Exception as e:
            return {
                "status": "error",
                "channel": WF_EVENTS_CHANNEL,
                "error": str(e)
            }

    @app.get("/metrics")
    def metrics():
        """
        Prometheus metrics endpoint.

        Returns metrics in Prometheus text format for scraping.
        """
        try:
            from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
            from src.core.metrics import registry
            from fastapi.responses import Response

            metrics_output = generate_latest(registry)
            return Response(content=metrics_output, media_type=CONTENT_TYPE_LATEST)
        except Exception as e:
            logger.error(f"Failed to generate metrics: {e}")
            return Response(content=f"# Error generating metrics: {e}", media_type="text/plain")

    # --- Board APIs ---
    @app.get("/board/state")
    def board_state():
        """Get complete board state."""
        return get_board_state()

    @app.post("/board/card")
    def board_add(body: Dict[str, Any] = Body(...)):
        """Create a new card in Backlog."""
        title = (body.get("title") or body.get("intent") or "Untitled").strip()[:140]
        card = {
            "id": "c-" + uuid.uuid4().hex[:8],
            "title": title,
            "intent": body.get("intent") or "",
            "status": "Backlog",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "meta": body.get("meta") or {},
        }
        push_to("Backlog", card)
        return {"created": card}

    @app.post("/board/move")
    def board_move(body: Dict[str, Any] = Body(...)):
        """Move a card to a different column."""
        card_id = body["card_id"]
        to = body["to"]
        card, cur = find_and_remove(card_id)
        if not card:
            return JSONResponse({"error": "not_found"}, status_code=404)

        # Try to push to target column; if WIP exceeded, revert
        if not push_to(to, card):
            # Revert to original column (bypass WIP)
            push_to(cur or "Backlog", card, bypass_wip=True)
            return JSONResponse({
                "error": "wip_limit_exceeded",
                "message": f"Cannot move to {to}: WIP limit reached",
                "card_id": card_id,
                "from": cur,
                "reverted": True
            }, status_code=409)

        # autopilot: suggest/act automatically
        if rclient().get("wf:agent:autopilot") == "1":
            actions = suggest_actions(to)
            if actions:
                agent_act(card["id"], actions[0])

        return {"moved": {"id": card_id, "from": cur, "to": to}}

    # --- Agent APIs ---
    @app.post("/agent/autopilot")
    def agent_autopilot(body: Dict[str, Any] = Body(...)):
        """Toggle autopilot mode."""
        on = bool(body.get("on"))
        rclient().set("wf:agent:autopilot", "1" if on else "0")
        emit_event("agent_mode", {"autopilot": on})
        return {"autopilot": on}

    @app.get("/agent/suggest")
    def agent_suggest(card_id: str):
        """Get suggested actions for a card."""
        card, cur = find_and_remove(card_id)
        if not card:
            return JSONResponse({"error": "not_found"}, status_code=404)
        # return card to original column (read-only peek, bypass WIP to ensure it goes back)
        push_to(cur or "Backlog", card, bypass_wip=True)
        return {"card_id": card_id, "column": cur, "actions": suggest_actions(cur or "Backlog")}

    @app.post("/agent/act")
    def agent_act_api(body: Dict[str, Any] = Body(...)):
        """Execute an agent action."""
        result = agent_act(body["card_id"], body["action"])
        return result

    @app.post("/agent/run_planner")
    def run_planner_api(body: Dict[str, Any] = Body(default={})):
        """
        Manually trigger the planner agent.

        Body:
        {
            "mode": "create" | "move" | "full",  # default: "full"
            "template_name": "daily_content",     # optional
            "wip_limit": 2                        # optional
        }
        """
        try:
            mode = body.get("mode", "full")
            template_name = body.get("template_name", "daily_content")
            wip_limit = int(body.get("wip_limit", os.getenv("WF_WIP_LIMIT", "2")))

            job_payload = {
                "mode": mode,
                "template_name": template_name,
                "wip_limit": wip_limit
            }

            job_id = queue_job("planner_agent", job_payload,
                             idempotency_key=f"planner-{mode}-{int(time.time())}")

            return {
                "success": True,
                "job_id": job_id,
                "mode": mode,
                "message": "Planner agent queued"
            }
        except Exception as e:
            logger.error(f"Failed to queue planner: {e}")
            return JSONResponse({
                "success": False,
                "error": str(e),
                "message": "Failed to queue planner agent"
            }, status_code=500)

    # --- Queue Status ---
    @app.get("/queue/status")
    def queue_status():
        """Get queue statistics."""
        queue = get_queue_manager()
        stats = {
            "mode": QUEUE_MODE,
            "depth": 0
        }
        if hasattr(queue, 'depth'):
            stats["depth"] = queue.depth()
        if hasattr(queue, 'stats'):
            stats.update(queue.stats())
        return stats

    # --- KPI Endpoints ---
    @app.get("/kpis/completed")
    def kpis_completed(days: int = 7):
        """Get completed tasks for the last N days."""
        from datetime import timedelta
        from collections import defaultdict

        r = rclient()
        now = datetime.now(timezone.utc)
        start_date = now - timedelta(days=days)

        completed = {
            "total": 0,
            "by_user": defaultdict(int),
            "by_day": defaultdict(int),
            "period_days": days
        }

        # Get all published cards
        published_cards = r.lrange("wf:board:col:Published", 0, -1)

        for card_json in published_cards:
            try:
                card = json.loads(card_json)
                if "updated_at" in card:
                    updated = datetime.fromisoformat(card["updated_at"].replace("Z", "+00:00"))
                    if updated >= start_date:
                        completed["total"] += 1

                        # By user
                        user = card.get("meta", {}).get("assignee", "unassigned")
                        completed["by_user"][user] += 1

                        # By day
                        day_key = updated.strftime("%Y-%m-%d")
                        completed["by_day"][day_key] += 1
            except Exception as e:
                logger.debug(f"Error parsing card: {e}")

        return dict(completed)

    @app.get("/kpis/efficiency")
    def kpis_efficiency():
        """Calculate efficiency metrics (active time vs blocked time)."""
        r = rclient()
        efficiency = {
            "overall": 0,
            "by_user": {},
            "active_vs_blocked": {}
        }

        # Calculate based on cards in different states
        total_cards = 0
        active_cards = 0

        for column in DEFAULT_COLUMNS:
            count = r.llen(f"wf:board:col:{column}")
            total_cards += count

            if column in ["In Progress", "Waiting Approval"]:
                active_cards += count

        if total_cards > 0:
            efficiency["overall"] = round((active_cards / total_cards) * 100, 2)

        efficiency["cards_by_state"] = {
            col: r.llen(f"wf:board:col:{col}")
            for col in DEFAULT_COLUMNS
        }

        return efficiency

    @app.get("/kpis/daily_plan")
    def kpis_daily_plan(user: Optional[str] = None):
        """Get today's planned tasks."""
        r = rclient()
        today = datetime.now(timezone.utc).date()
        daily_tasks = []

        # Check Scheduled column for today's tasks
        scheduled_cards = r.lrange("wf:board:col:Scheduled", 0, -1)

        for card_json in scheduled_cards:
            try:
                card = json.loads(card_json)
                scheduled_time = card.get("meta", {}).get("scheduled_time")
                assignee = card.get("meta", {}).get("assignee", "unassigned")

                # Filter by user if specified
                if user and assignee != user:
                    continue

                if scheduled_time:
                    scheduled_dt = datetime.fromisoformat(scheduled_time.replace("Z", "+00:00"))
                    if scheduled_dt.date() == today:
                        daily_tasks.append({
                            "id": card.get("id"),
                            "title": card.get("title"),
                            "scheduled_time": scheduled_time,
                            "assignee": assignee,
                            "priority": card.get("meta", {}).get("priority", "normal")
                        })
            except Exception as e:
                logger.debug(f"Error parsing scheduled card: {e}")

        # Sort by scheduled time
        daily_tasks.sort(key=lambda x: x.get("scheduled_time", ""))

        return {
            "date": today.isoformat(),
            "total": len(daily_tasks),
            "tasks": daily_tasks
        }

    # --- AI Chat Endpoints ---
    @app.post("/ai/plan_tomorrow")
    def ai_plan_tomorrow(body: Dict[str, Any] = Body(...)):
        """Plan tomorrow's tasks using AI agent."""
        user = body.get("user", "all")

        # Queue a job for planning
        job_payload = {
            "action": "plan_tasks",
            "user": user,
            "date": (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat(),
            "cockpit": True
        }

        try:
            job_id = queue_job("metrics_reporter", job_payload,
                              idempotency_key=f"plan-{user}-tomorrow")
            return {"success": True, "job_id": job_id, "message": "Planning tomorrow's tasks"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @app.get("/me/tasks")
    def get_my_tasks(scope: str = "mine", when: str = "today"):
        """Get tasks for the current user."""
        r = rclient()
        tasks = []

        # For demo, using a default user - in production, get from auth
        current_user = os.getenv("CURRENT_USER", "demo")

        columns_to_check = ["Backlog", "In Progress", "Waiting Approval", "Scheduled"]

        for column in columns_to_check:
            cards = r.lrange(f"wf:board:col:{column}", 0, -1)
            for card_json in cards:
                try:
                    card = json.loads(card_json)
                    assignee = card.get("meta", {}).get("assignee", "unassigned")

                    if scope == "mine" and assignee != current_user:
                        continue

                    # Filter by time if needed
                    if when == "today":
                        # Check if scheduled for today
                        scheduled = card.get("meta", {}).get("scheduled_time")
                        if scheduled:
                            sched_dt = datetime.fromisoformat(scheduled.replace("Z", "+00:00"))
                            if sched_dt.date() != datetime.now(timezone.utc).date():
                                continue

                    tasks.append({
                        "id": card.get("id"),
                        "title": card.get("title"),
                        "status": column,
                        "assignee": assignee,
                        "priority": card.get("meta", {}).get("priority", "normal")
                    })
                except Exception as e:
                    logger.debug(f"Error parsing card: {e}")

        return {
            "scope": scope,
            "when": when,
            "user": current_user if scope == "mine" else "all",
            "total": len(tasks),
            "tasks": tasks
        }

    @app.post("/cards/cleanup")
    def cleanup_cards(body: Dict[str, Any] = Body(...)):
        """Clean up completed cards older than N days."""
        state = body.get("state", "Published")
        days_old = body.get("days_old", 7)

        if state not in DEFAULT_COLUMNS:
            return {"error": "Invalid state", "valid_states": DEFAULT_COLUMNS}

        r = rclient()
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_old)
        removed = 0

        cards = r.lrange(f"wf:board:col:{state}", 0, -1)
        for card_json in cards:
            try:
                card = json.loads(card_json)
                if "updated_at" in card:
                    updated = datetime.fromisoformat(card["updated_at"].replace("Z", "+00:00"))
                    if updated < cutoff_date:
                        # Archive card (move to archived list)
                        r.lpush("wf:board:archived", card_json)
                        r.lrem(f"wf:board:col:{state}", 1, card_json)
                        removed += 1
            except Exception as e:
                logger.debug(f"Error processing card: {e}")

        return {
            "success": True,
            "state": state,
            "removed": removed,
            "message": f"Archived {removed} cards older than {days_old} days"
        }

    # --- Events: recent + SSE ---
    @app.get("/events/recent")
    def recent_events():
        """Get recent events."""
        r = rclient()
        items = r.lrange(WF_EVENTS_LIST, 0, 49)
        out = []
        for raw in items[::-1]:
            try:
                out.append(json.loads(raw))
            except Exception:
                pass
        return out

    @app.get("/events/stream")
    async def stream():
        """Server-sent events stream with 15s keepalive heartbeat and real-time pubsub events."""
        # Import async Redis client
        import redis.asyncio as aioredis

        # Create async Redis connection that persists for the entire stream
        r = await aioredis.from_url(
            REDIS_URL,
            decode_responses=True,
            health_check_interval=30
        )

        try:
            # Subscribe to events channel
            pubsub = r.pubsub()
            await pubsub.subscribe(WF_EVENTS_CHANNEL)
            logger.info(f"SSE: Subscribed to {WF_EVENTS_CHANNEL}")

            async def gen():
                """Async generator that yields SSE events."""
                try:
                    # Send initial heartbeat immediately to establish connection
                    yield f": connected\n\n"
                    last_heartbeat_time = time.time()

                    while True:
                        # Non-blocking check for messages
                        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0)

                        # If we got a message, yield it immediately
                        if msg and msg.get("type") == "message":
                            data = msg.get("data")
                            data_preview = data[:100] if len(data) > 100 else data
                            logger.info(f"SSE: Received pubsub message: {data_preview}")
                            yield f"data: {data}\n\n"
                            last_heartbeat_time = time.time()

                        # Send heartbeat if 15s passed without messages
                        current_time = time.time()
                        if current_time - last_heartbeat_time >= 15.0:
                            yield format_sse_heartbeat()
                            last_heartbeat_time = current_time

                        # Sleep briefly to avoid CPU spinning
                        await asyncio.sleep(0.5)

                except asyncio.CancelledError:
                    # Client disconnected gracefully
                    logger.info("SSE: Client disconnected")
                    raise
                except Exception as e:
                    # Unexpected error in stream
                    logger.error(f"SSE: Stream error: {e}")
                    yield f"event: error\ndata: {str(e)}\n\n"
                finally:
                    # Clean up subscription when client disconnects
                    logger.info("SSE: Cleaning up pubsub subscription")
                    try:
                        await pubsub.unsubscribe(WF_EVENTS_CHANNEL)
                        await pubsub.close()
                    except Exception as e:
                        logger.error(f"SSE: Cleanup error: {e}")
                    finally:
                        await r.close()

            return StreamingResponse(
                gen(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )

        except Exception as e:
            # If subscription setup fails, close connection immediately
            logger.error(f"SSE: Failed to setup subscription: {e}")
            await r.close()
            raise

    # --- UI ---
    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse(INDEX_HTML)

    # --- Background Scheduler Setup ---
    scheduler = None

    def start_scheduler():
        """Start background scheduler for autonomous planner operations."""
        nonlocal scheduler

        if os.getenv("DISABLE_SCHEDULER", "").lower() == "true":
            logger.info("Scheduler disabled by environment variable")
            return

        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.interval import IntervalTrigger

            scheduler = BackgroundScheduler(daemon=True)

            # Run PlannerAgent periodically to move cards respecting WIP
            planner_interval = int(os.getenv("WF_PLANNER_INTERVAL", "3600"))  # Default: 1 hour
            scheduler.add_job(
                func=schedule_planner,
                trigger=IntervalTrigger(seconds=planner_interval),
                id="planner_move_cards",
                name="Planner: Move cards from Backlog to In Progress",
                replace_existing=True
            )

            scheduler.start()
            logger.info(f"Background scheduler started (planner runs every {planner_interval}s)")

        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")

    def schedule_planner():
        """Queue a planner job to move cards respecting WIP limits."""
        try:
            wip_limit = int(os.getenv("WF_WIP_LIMIT", "2"))
            job_payload = {
                "mode": "full",  # Create cards if needed + move respecting WIP
                "template_name": "daily_content",
                "wip_limit": wip_limit
            }

            job_id = queue_job("planner_agent", job_payload,
                             idempotency_key=f"planner-auto-{int(time.time())}")
            logger.info(f"Scheduled planner job: {job_id}")
        except Exception as e:
            logger.error(f"Failed to schedule planner: {e}")

    @app.on_event("startup")
    async def startup_event():
        """Initialize background services."""
        start_scheduler()
        logger.info("WordFlux Cockpit startup complete")

    @app.on_event("shutdown")
    async def shutdown_event():
        """Clean shutdown of background services."""
        nonlocal scheduler
        if scheduler:
            scheduler.shutdown()
            logger.info("Background scheduler stopped")

    # Register chat router
    from src.api.chat import router as chat_router
    app.include_router(chat_router, prefix="/chat", tags=["chat"])
    logger.info("Chat router registered at /chat")

    return app


# Optional: enable direct run
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("wordflux_cockpit:build_app", factory=True,
                host="0.0.0.0",
                port=int(os.getenv("PORT", "8080")),
                reload=False)