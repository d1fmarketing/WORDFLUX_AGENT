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

logger = logging.getLogger(__name__)


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

        # Criar card no board
        if not push_to(column, card):
            raise RuntimeError(f"Falha ao criar card na coluna '{column}' (WIP limit?)")

        # Emitir evento SSE tipado
        from src.core.events import emit_card_created
        emit_card_created(
            card_id=card["id"],
            title=card["title"],
            list_name=column,
            meta=card.get("meta")
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
            raise ValueError(f"Card não encontrado: '{card_ref}'")

        card_id = card["id"]
        original_column = from_column

        # Tentar mover para coluna destino
        if not push_to(to_column, card):
            # WIP limit excedido - reverter
            push_to(original_column, card, bypass_wip=True)
            raise RuntimeError(
                f"WIP limit excedido na coluna '{to_column}'. "
                f"Card revertido para '{original_column}'."
            )

        # Emitir evento SSE tipado
        from src.core.events import emit_card_moved
        emit_card_moved(
            card_id=card_id,
            title=card.get("title", ""),
            from_list=original_column,
            to_list=to_column,
            meta=card.get("meta")
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
            raise ValueError(f"Card não encontrado: '{card_ref}'")

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
            raise ValueError(f"Card não encontrado: '{card_ref}'")

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
