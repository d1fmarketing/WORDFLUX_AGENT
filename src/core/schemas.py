"""JSON Schemas para validação de dados do WordFlux.

Este módulo define schemas Pydantic para validação de:
- Cards (CardSchema)
- Metadata de cards (CardMetaSchema)
- Estado completo do board (BoardStateSchema)

Usado para garantir integridade de dados em:
- Endpoint /board/state
- Eventos SSE
- Persistência Redis
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, validator


class CardMetaSchema(BaseModel):
    """Metadata opcional de um card.

    Attributes:
        assignee: Responsável pelo card (opcional)
        labels: Tags de categorização (opcional, default=[])
        due: Data de entrega no formato YYYY-MM-DD (opcional)
        from_chat: Se o card foi criado via chat (default=False)
        session_id: ID da sessão de chat (opcional)
    """
    assignee: Optional[str] = None
    labels: Optional[List[str]] = Field(default_factory=list)
    due: Optional[str] = Field(None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    from_chat: bool = False
    session_id: Optional[str] = None

    @validator('labels', pre=True, always=True)
    def validate_labels(cls, v):
        """Garante que labels é sempre uma lista."""
        if v is None:
            return []
        if not isinstance(v, list):
            return [str(v)]
        return v

    @validator('assignee')
    def validate_assignee(cls, v):
        """Remove espaços em branco do assignee."""
        if v and isinstance(v, str):
            return v.strip()
        return v

    class Config:
        extra = 'allow'  # Permite campos extras (extensibilidade futura)


class CardSchema(BaseModel):
    """Schema de validação para Card.

    Attributes:
        id: Identificador único do card (obrigatório)
        title: Título do card (obrigatório, 1-140 chars)
        intent: Descrição/intent do card (opcional, default="")
        status: Coluna atual do card (obrigatório)
        created_at: Timestamp ISO 8601 de criação (obrigatório)
        meta: Metadata adicional (opcional)

    Examples:
        >>> card = CardSchema(
        ...     id="c-abc123",
        ...     title="Implementar feature X",
        ...     status="Produção",
        ...     created_at="2025-10-01T10:00:00Z",
        ...     meta={"assignee": "John", "labels": ["urgent"]}
        ... )
        >>> card.dict()
        {...}
    """
    id: str = Field(..., min_length=1, description="Card ID (ex: c-abc123)")
    title: str = Field(..., min_length=1, max_length=140, description="Card title")
    intent: Optional[str] = Field("", description="Card description/intent")
    status: str = Field(..., min_length=1, description="Current column name")
    created_at: str = Field(..., description="ISO 8601 timestamp")
    meta: Optional[CardMetaSchema] = Field(default_factory=CardMetaSchema)

    @validator('title')
    def title_not_null_or_empty(cls, v):
        """Valida que título não é nulo ou vazio."""
        if not v or not v.strip():
            raise ValueError("Título não pode ser vazio")
        return v.strip()

    @validator('status')
    def status_not_empty(cls, v):
        """Valida que status (coluna) não é vazio."""
        if not v or not v.strip():
            raise ValueError("Status (coluna) não pode ser vazio")
        return v.strip()

    @validator('created_at')
    def created_at_is_iso8601(cls, v):
        """Valida que created_at é um timestamp ISO 8601 válido."""
        try:
            # Tenta parsear como ISO 8601
            datetime.fromisoformat(v.replace('Z', '+00:00'))
            return v
        except (ValueError, AttributeError):
            raise ValueError(f"created_at deve ser ISO 8601, recebeu: {v}")

    class Config:
        extra = 'allow'  # Permite campos extras para compatibilidade


class BoardStateSchema(BaseModel):
    """Schema de validação para /board/state.

    Formato esperado:
        {
            "columns": {
                "Espera": [CardSchema, ...],
                "Produção": [CardSchema, ...],
                "Aprovação": [CardSchema, ...],
                "Agendado": [CardSchema, ...],
                "Finalizado": [CardSchema, ...]
            },
            "autopilot": bool
        }

    Attributes:
        columns: Dicionário de colunas (chave=nome da coluna, valor=lista de cards)
        autopilot: Se o modo autopilot está ativado

    Examples:
        >>> state = BoardStateSchema(
        ...     columns={
        ...         "Espera": [CardSchema(...)],
        ...         "Produção": []
        ...     },
        ...     autopilot=False
        ... )
        >>> state.dict()
        {...}
    """
    columns: Dict[str, List[CardSchema]] = Field(
        ...,
        description="Map of column name to list of cards"
    )
    autopilot: bool = Field(default=False, description="Autopilot mode enabled")

    @validator('columns')
    def validate_columns(cls, v):
        """Valida que todas as colunas contêm listas."""
        if not isinstance(v, dict):
            raise ValueError("columns deve ser um dicionário")

        for col_name, cards in v.items():
            if not isinstance(cards, list):
                raise ValueError(f"Coluna '{col_name}' deve conter uma lista de cards")

        return v

    class Config:
        extra = 'allow'  # Permite campos extras (ex: metrics futuras)


# Helper functions para validação

def validate_card(card_data: dict) -> CardSchema:
    """Valida e normaliza dados de um card.

    Args:
        card_data: Dict com dados do card

    Returns:
        CardSchema validado

    Raises:
        ValidationError: Se card_data for inválido

    Examples:
        >>> card = validate_card({"id": "c-123", "title": "Test", ...})
        >>> card.title
        "Test"
    """
    return CardSchema(**card_data)


def validate_board_state(state_data: dict) -> BoardStateSchema:
    """Valida e normaliza estado completo do board.

    Args:
        state_data: Dict com estado do board

    Returns:
        BoardStateSchema validado

    Raises:
        ValidationError: Se state_data for inválido

    Examples:
        >>> state = validate_board_state({"columns": {...}, "autopilot": False})
        >>> state.autopilot
        False
    """
    return BoardStateSchema(**state_data)
