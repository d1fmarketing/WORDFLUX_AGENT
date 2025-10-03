"""Board Operator Agent - Processa operações de board via fila Redis.

Este agente é responsável por executar todas as operações de manipulação
de cards no board Kanban, garantindo que o chat agent não chame APIs
diretamente, mas sempre enfileire jobs que serão processados de forma
assíncrona.

Operações suportadas:
- create_card: Criar novo card
- move_card: Mover card entre colunas
- update_card: Atualizar campos de um card
- comment_card: Adicionar comentário a um card

Fluxo:
1. Chat LLM gera tool_call (ex: move_card)
2. execute_tool_call() enfileira Job(agent="board_operator", action="move_card")
3. Worker consome job e chama board_operator.run()
4. board_operator executa ação usando helpers do cockpit
5. Emite SSE board_update para UI reativa
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Adicionar cockpit ao path para importar helpers
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'playbooks', 'cockpit'))

# Import optimistic update metrics
from src.core.metrics_optimistic import record_card_operation_completed

logger = logging.getLogger(__name__)


# ============================================
# Custom Exceptions with PT-BR error codes
# ============================================

class BoardOperatorError(Exception):
    """Base exception for board operator errors with structured error info."""

    def __init__(self, message: str, code: str, hint: str):
        """
        Args:
            message: Error message (technical)
            code: Error code (e.g., "wip_limit", "card_not_found")
            hint: User-friendly hint in PT-BR
        """
        super().__init__(message)
        self.code = code
        self.hint = hint

    def to_dict(self) -> Dict[str, str]:
        """Convert to dict for logging/events."""
        return {
            "message": str(self),
            "code": self.code,
            "hint": self.hint
        }


class WIPLimitError(BoardOperatorError):
    """WIP limit exceeded."""

    def __init__(self, column: str, current: int, limit: int):
        super().__init__(
            message=f"WIP limit exceeded in column '{column}': {current}/{limit}",
            code="wip_limit",
            hint=f"Limite WIP atingido em '{column}'. Remova outros cards dessa coluna primeiro."
        )


class CardNotFoundError(BoardOperatorError):
    """Card not found by ID or title."""

    def __init__(self, card_ref: str):
        super().__init__(
            message=f"Card not found: '{card_ref}'",
            code="card_not_found",
            hint=f"Card '{card_ref}' não encontrado. Verifique o ID ou título."
        )


class InvalidColumnError(BoardOperatorError):
    """Invalid/unknown column name."""

    def __init__(self, column: str):
        super().__init__(
            message=f"Invalid column: '{column}'",
            code="invalid_column",
            hint=f"Coluna '{column}' inválida. Use: Espera, Produção, Aprovação, Agendado, Finalizado."
        )


class BoardOperatorAgent:
    """Agente que processa operações de board (create, move, update, comment)."""

    def __init__(self):
        """Inicializar agente."""
        self.name = "board_operator"

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executar operação de board baseada no payload.

        Args:
            payload: Dict com:
                - action: Tipo de operação (create_card, move_card, etc.)
                - ... parâmetros específicos da ação

        Returns:
            Dict com resultado da operação

        Raises:
            ValueError: Se action não for suportada
        """
        action = payload.get("action")
        if not action:
            raise ValueError("Campo 'action' obrigatório no payload")

        logger.info(f"📋 Board Operator: processando action={action}")

        # Dispatcher por tipo de ação
        handlers = {
            "create_card": self._create_card,
            "move_card": self._move_card,
            "update_card": self._update_card,
            "comment_card": self._comment_card,
        }

        handler = handlers.get(action)
        if not handler:
            raise ValueError(
                f"Action '{action}' não suportada. "
                f"Opções: {', '.join(handlers.keys())}"
            )

        # Executar handler
        result = handler(payload)

        logger.info(f"✅ Board Operator: {action} executado com sucesso")
        return result

    def _create_card(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Criar novo card no board.

        Args:
            payload: Dict com title, description, column (opcional), labels/tags, assignee/assignees, due/due_date

        Returns:
            Dict com card criado
        """
        from wordflux_cockpit import push_to, emit_event

        title = payload.get("title", "").strip()[:140]
        if not title:
            raise ValueError("Campo 'title' obrigatório para criar card")

        column = payload.get("column", "Espera")  # Default: Espera (Backlog)

        # Campo mapping (backward compatibility)
        # Aceita tanto o formato antigo (assignee, labels, due) quanto novo (assignees, tags, due_date)
        labels = payload.get("tags") or payload.get("labels") or []
        assignee = None
        if payload.get("assignees"):
            # Novo formato: assignees (array) → pegar primeiro
            assignee = payload["assignees"][0] if payload["assignees"] else None
        elif payload.get("assignee"):
            # Antigo formato: assignee (string)
            assignee = payload["assignee"]

        due = payload.get("due_date") or payload.get("due")

        card = {
            "id": f"c-{uuid.uuid4().hex[:8]}",
            "title": title,
            "intent": payload.get("description", ""),
            "status": column,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "meta": {
                "labels": labels,
                "assignee": assignee,
                "due": due,
                "from_chat": payload.get("from_chat", False),
                "session_id": payload.get("session_id"),
            },
        }

        # Criar card no board (push_to já valida coluna e incrementa métricas)
        try:
            success = push_to(column, card)
        except ValueError as e:
            # Coluna inválida - métrica já incrementada em push_to()
            logger.error(
                f"❌ create_card falhou: coluna inválida '{column}'",
                extra={"operation": "create_card", "column": column, "title": title}
            )
            raise InvalidColumnError(column) from e

        if not success:
            # WIP limit excedido - métrica já incrementada em push_to()
            # Get current count for error message
            from wordflux_cockpit import rclient, get_column_key
            r = rclient()
            current_count = r.llen(get_column_key(column))

            # Record failed operation (WIP limit)
            record_card_operation_completed(
                operation='create',
                list_name=column,
                success=False,
                error_code='wip_limit'
            )

            raise WIPLimitError(column=column, current=current_count, limit=2)  # TODO: get actual limit

        # Emitir evento SSE tipado
        from src.core.events import emit_card_created
        emit_card_created(
            card_id=card["id"],
            title=card["title"],
            list_name=column,
            meta=card.get("meta")
        )

        # Record successful operation
        record_card_operation_completed(
            operation='create',
            list_name=column,
            success=True
        )

        logger.info(f"✅ Card criado: {card['id']} → {column} (título: {title})")
        return {"success": True, "card": card}

    def _move_card(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Mover card entre colunas.

        Args:
            payload: Dict com card_ref/card_id (ID ou parte do título) e to/to_column (coluna destino)

        Returns:
            Dict com resultado da movimentação
        """
        from wordflux_cockpit import find_and_remove, push_to, emit_event

        # Campo mapping (backward compatibility)
        card_ref = payload.get("card_id") or payload.get("card_ref", "")
        card_ref = card_ref.strip() if isinstance(card_ref, str) else ""
        to_column = payload.get("to_column") or payload.get("to")

        if not card_ref:
            raise ValueError("Campo 'card_id' ou 'card_ref' obrigatório para mover card")
        if not to_column:
            raise ValueError("Campo 'to_column' ou 'to' obrigatório")

        # Encontrar card (por ID ou parte do título)
        card, from_column = find_and_remove(card_ref)

        if not card:
            # Tentar busca fuzzy por título
            card, from_column = self._find_card_by_title_fuzzy(card_ref)

        if not card:
            raise CardNotFoundError(card_ref=card_ref)

        card_id = card["id"]
        original_column = from_column

        # Tentar mover para coluna destino (push_to já valida coluna e incrementa métricas)
        try:
            success = push_to(to_column, card)
        except ValueError as e:
            # Coluna inválida - reverter card para coluna original
            push_to(original_column, card, bypass_wip=True)
            logger.error(
                f"❌ move_card falhou: coluna inválida '{to_column}'",
                extra={"operation": "move_card", "card_id": card_id, "to_column": to_column}
            )
            raise InvalidColumnError(to_column) from e

        if not success:
            # WIP limit excedido - reverter
            push_to(original_column, card, bypass_wip=True)
            # Get current count for error message
            from wordflux_cockpit import rclient, get_column_key
            r = rclient()
            current_count = r.llen(get_column_key(to_column))

            # Record failed operation (WIP limit)
            record_card_operation_completed(
                operation='move',
                list_name=to_column,
                success=False,
                error_code='wip_limit'
            )

            raise WIPLimitError(column=to_column, current=current_count, limit=2)  # TODO: get actual limit

        # Emitir evento SSE tipado
        from src.core.events import emit_card_moved
        emit_card_moved(
            card_id=card_id,
            title=card.get("title", ""),
            from_list=original_column,
            to_list=to_column,
            meta=card.get("meta")
        )

        # Record successful operation
        record_card_operation_completed(
            operation='move',
            list_name=to_column,
            success=True
        )

        logger.info(f"✅ Card movido: {card_id} ({card.get('title', '')[:30]}...) → {original_column} → {to_column}")
        return {
            "success": True,
            "card_id": card_id,
            "from": original_column,
            "to": to_column,
        }

    def _update_card(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Atualizar campos de um card.

        Args:
            payload: Dict com card_ref/card_id e fields (dict de campos a atualizar)

        Returns:
            Dict com card atualizado
        """
        from wordflux_cockpit import find_and_remove, push_to, emit_event

        # Campo mapping (backward compatibility)
        card_ref = payload.get("card_id") or payload.get("card_ref", "")
        card_ref = card_ref.strip() if isinstance(card_ref, str) else ""
        fields = payload.get("fields", {})

        if not card_ref:
            raise ValueError("Campo 'card_id' ou 'card_ref' obrigatório para atualizar card")
        if not fields:
            raise ValueError("Campo 'fields' obrigatório para atualizar card")

        # Encontrar card
        card, column = find_and_remove(card_ref)
        if not card:
            card, column = self._find_card_by_title_fuzzy(card_ref)

        if not card:
            raise CardNotFoundError(card_ref=card_ref)

        card_id = card["id"]

        # Atualizar campos permitidos
        allowed_fields = ["title", "intent", "meta", "labels", "assignee", "due"]
        for field, value in fields.items():
            if field in allowed_fields:
                if field in ["labels", "assignee", "due"] and "meta" in card:
                    card["meta"][field] = value
                else:
                    card[field] = value

        # Adicionar timestamp de atualização
        card["updated_at"] = datetime.now(timezone.utc).isoformat()

        # Re-adicionar card à coluna original
        push_to(column, card, bypass_wip=True)

        # Emitir evento SSE tipado
        from src.core.events import emit_card_updated
        emit_card_updated(
            card_id=card_id,
            title=card.get("title", ""),
            list_name=column,
            fields_updated=list(fields.keys()),
            meta=card.get("meta")
        )

        # Record successful operation
        record_card_operation_completed(
            operation='update',
            list_name=column,
            success=True
        )

        logger.info(f"✅ Card atualizado: {card_id} (campos: {', '.join(fields.keys())})")
        return {"success": True, "card": card}

    def _comment_card(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Adicionar comentário a um card.

        Args:
            payload: Dict com card_ref e text

        Returns:
            Dict com resultado
        """
        from wordflux_cockpit import find_and_remove, push_to, emit_event

        card_ref = payload.get("card_ref", "").strip()
        text = payload.get("text", "").strip()

        if not card_ref:
            raise ValueError("Campo 'card_ref' obrigatório para comentar card")
        if not text:
            raise ValueError("Campo 'text' obrigatório para comentário")

        # Encontrar card
        card, column = find_and_remove(card_ref)
        if not card:
            card, column = self._find_card_by_title_fuzzy(card_ref)

        if not card:
            raise CardNotFoundError(card_ref=card_ref)

        card_id = card["id"]

        # Adicionar comentário ao meta
        if "meta" not in card:
            card["meta"] = {}
        if "comments" not in card["meta"]:
            card["meta"]["comments"] = []

        comment = {
            "id": f"cmt-{uuid.uuid4().hex[:8]}",
            "text": text,
            "author": "WordFlux AI",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "from_chat": payload.get("from_chat", False),
        }

        card["meta"]["comments"].append(comment)

        # Re-adicionar card
        push_to(column, card, bypass_wip=True)

        # Emitir evento SSE tipado
        from src.core.events import emit_card_updated
        emit_card_updated(
            card_id=card_id,
            title=card.get("title", ""),
            list_name=column,
            fields_updated=["comments"],
            meta=card.get("meta")
        )

        logger.info(f"✅ Comentário adicionado ao card: {card_id} (texto: {text[:50]}...)")
        return {"success": True, "comment": comment}

    def _find_card_by_title_fuzzy(self, title_part: str) -> tuple[Optional[Dict], Optional[str]]:
        """
        Buscar card por parte do título (fuzzy search).

        Args:
            title_part: Parte do título para buscar

        Returns:
            Tupla (card, column) ou (None, None)
        """
        try:
            from wordflux_cockpit import rclient, PORTUGUESE_COLUMNS, get_column_key

            r = rclient()
            title_lower = title_part.lower()

            # Buscar em todas as colunas
            for column in PORTUGUESE_COLUMNS:
                key = get_column_key(column)
                items = r.lrange(key, 0, -1)

                for raw in items:
                    try:
                        card = json.loads(raw)
                        card_title = card.get("title", "").lower()

                        if title_lower in card_title or card_title in title_lower:
                            # Remover card da coluna atual
                            r.lrem(key, 1, raw)
                            return card, column

                    except Exception:
                        continue

            return None, None

        except Exception as e:
            logger.error(f"Erro ao buscar card fuzzy: {e}")
            return None, None


def build_agent() -> BoardOperatorAgent:
    """Factory function para criar instância do agente."""
    return BoardOperatorAgent()
