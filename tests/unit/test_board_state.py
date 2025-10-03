"""Unit tests for board state management and column canonicalization.

Tests dual-read behavior, PT/EN column mapping, and validation.
"""
from __future__ import annotations

import json
import pytest
import redis
from unittest.mock import Mock, patch, MagicMock

# Add project root to path
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'playbooks', 'cockpit'))

# Import functions to test
from wordflux_cockpit import canonicalize_column, get_board_state, CANONICAL_PT_COLUMNS


class TestCanonicalizeColumn:
    """Test column name canonicalization (PT/EN mapping)."""

    def test_canonicalize_column_pt_exact_match(self):
        """Test that exact PT column names pass through unchanged."""
        assert canonicalize_column("Espera") == "Espera"
        assert canonicalize_column("Produção") == "Produção"
        assert canonicalize_column("Aprovação") == "Aprovação"
        assert canonicalize_column("Agendado") == "Agendado"
        assert canonicalize_column("Finalizado") == "Finalizado"

    def test_canonicalize_column_en_to_pt(self):
        """Test that EN column names map correctly to PT."""
        assert canonicalize_column("Backlog") == "Espera"
        assert canonicalize_column("In Progress") == "Produção"
        assert canonicalize_column("Waiting Approval") == "Aprovação"
        assert canonicalize_column("Scheduled") == "Agendado"
        assert canonicalize_column("Published") == "Finalizado"

    def test_canonicalize_column_case_insensitive_pt(self):
        """Test case-insensitive matching for PT columns."""
        assert canonicalize_column("espera") == "Espera"
        assert canonicalize_column("ESPERA") == "Espera"
        assert canonicalize_column("EsPeRa") == "Espera"
        assert canonicalize_column("produção") == "Produção"
        assert canonicalize_column("PRODUÇÃO") == "Produção"

    def test_canonicalize_column_case_insensitive_en(self):
        """Test case-insensitive matching for EN columns."""
        assert canonicalize_column("backlog") == "Espera"
        assert canonicalize_column("BACKLOG") == "Espera"
        assert canonicalize_column("in progress") == "Produção"
        assert canonicalize_column("IN PROGRESS") == "Produção"

    def test_canonicalize_column_fuzzy_match(self):
        """Test fuzzy matching (accent/space normalization)."""
        # Remove accents
        assert canonicalize_column("producao") == "Produção"
        assert canonicalize_column("PRODUCAO") == "Produção"
        assert canonicalize_column("aprovacao") == "Aprovação"
        assert canonicalize_column("APROVACAO") == "Aprovação"

        # Remove spaces
        assert canonicalize_column("inprogress") == "Produção"
        assert canonicalize_column("waitingapproval") == "Aprovação"

        # Aliases
        assert canonicalize_column("doing") == "Produção"
        assert canonicalize_column("review") == "Aprovação"
        assert canonicalize_column("done") == "Finalizado"

    def test_canonicalize_column_all_5_columns(self):
        """Test that all 5 canonical PT columns are valid."""
        for col in CANONICAL_PT_COLUMNS:
            assert canonicalize_column(col) == col

    def test_canonicalize_column_invalid_raises(self):
        """Test that invalid column names raise ValueError with PT message."""
        with pytest.raises(ValueError) as exc_info:
            canonicalize_column("invalid_column_name")

        error_msg = str(exc_info.value)
        assert "Coluna inválida" in error_msg
        assert "invalid_column_name" in error_msg
        assert "Espera" in error_msg  # Should suggest valid columns

    def test_canonicalize_column_empty_raises(self):
        """Test that empty/None column names raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            canonicalize_column("")
        assert "Coluna inválida" in str(exc_info.value)

        with pytest.raises(ValueError) as exc_info:
            canonicalize_column(None)
        assert "Coluna inválida" in str(exc_info.value)


class TestDualReadBoardState:
    """Test dual-read behavior in get_board_state()."""

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis client."""
        mock = MagicMock()
        mock.get.return_value = "1"  # autopilot enabled
        return mock

    def test_dual_read_merges_pt_and_en(self, mock_redis):
        """Test that dual-read merges cards from PT and EN Redis keys."""
        # Setup: 2 cards in PT "Espera", 3 unique cards in EN "Backlog"
        espera_cards = [
            {"id": "c-pt1", "title": "PT Card 1", "status": "Espera", "created_at": "2025-10-02T10:00:00Z", "intent": "", "meta": {}},
            {"id": "c-pt2", "title": "PT Card 2", "status": "Espera", "created_at": "2025-10-02T10:01:00Z", "intent": "", "meta": {}}
        ]
        backlog_cards = [
            {"id": "c-en1", "title": "EN Card 1", "status": "Backlog", "created_at": "2025-10-02T10:02:00Z", "intent": "", "meta": {}},
            {"id": "c-en2", "title": "EN Card 2", "status": "Backlog", "created_at": "2025-10-02T10:03:00Z", "intent": "", "meta": {}},
            {"id": "c-en3", "title": "EN Card 3", "status": "Backlog", "created_at": "2025-10-02T10:04:00Z", "intent": "", "meta": {}}
        ]

        def lrange_side_effect(key, start, end):
            if key == "wf:board:col:Espera":
                return [json.dumps(c) for c in espera_cards]
            elif key == "wf:board:col:Backlog":
                return [json.dumps(c) for c in backlog_cards]
            else:
                return []

        mock_redis.lrange.side_effect = lrange_side_effect

        with patch('wordflux_cockpit.rclient', return_value=mock_redis):
            state = get_board_state()

        # Assertions
        assert "Espera" in state["columns"]
        espera_col = state["columns"]["Espera"]
        assert len(espera_col) == 5  # 2 PT + 3 EN

        # Verify all cards present
        card_ids = {c["id"] for c in espera_col}
        assert card_ids == {"c-pt1", "c-pt2", "c-en1", "c-en2", "c-en3"}

    def test_dual_read_deduplicates_by_id(self, mock_redis):
        """Test that duplicate cards (same ID in PT and EN) appear only once."""
        # Setup: Same card in both PT and EN keys
        duplicate_card = {
            "id": "c-dup",
            "title": "Duplicate Card",
            "status": "Espera",
            "created_at": "2025-10-02T10:00:00Z",
            "intent": "",
            "meta": {}
        }

        def lrange_side_effect(key, start, end):
            if key in ("wf:board:col:Espera", "wf:board:col:Backlog"):
                return [json.dumps(duplicate_card)]
            else:
                return []

        mock_redis.lrange.side_effect = lrange_side_effect

        with patch('wordflux_cockpit.rclient', return_value=mock_redis):
            state = get_board_state()

        # Assertions: Card appears only once
        espera_col = state["columns"]["Espera"]
        assert len(espera_col) == 1
        assert espera_col[0]["id"] == "c-dup"

    def test_dual_read_pt_only(self, mock_redis):
        """Test that PT-only cards work correctly (no EN cards)."""
        pt_cards = [
            {"id": "c-pt1", "title": "PT Only 1", "status": "Produção", "created_at": "2025-10-02T10:00:00Z", "intent": "", "meta": {}},
            {"id": "c-pt2", "title": "PT Only 2", "status": "Produção", "created_at": "2025-10-02T10:01:00Z", "intent": "", "meta": {}}
        ]

        def lrange_side_effect(key, start, end):
            if key == "wf:board:col:Produção":
                return [json.dumps(c) for c in pt_cards]
            else:
                return []

        mock_redis.lrange.side_effect = lrange_side_effect

        with patch('wordflux_cockpit.rclient', return_value=mock_redis):
            state = get_board_state()

        # Assertions
        producao_col = state["columns"]["Produção"]
        assert len(producao_col) == 2
        assert all(c["status"] == "Produção" for c in producao_col)

    def test_dual_read_en_only(self, mock_redis):
        """Test that EN-only cards are merged into PT columns."""
        en_cards = [
            {"id": "c-en1", "title": "EN Only 1", "status": "In Progress", "created_at": "2025-10-02T10:00:00Z", "intent": "", "meta": {}},
            {"id": "c-en2", "title": "EN Only 2", "status": "In Progress", "created_at": "2025-10-02T10:01:00Z", "intent": "", "meta": {}}
        ]

        def lrange_side_effect(key, start, end):
            if key == "wf:board:col:In Progress":
                return [json.dumps(c) for c in en_cards]
            else:
                return []

        mock_redis.lrange.side_effect = lrange_side_effect

        with patch('wordflux_cockpit.rclient', return_value=mock_redis):
            state = get_board_state()

        # Assertions: EN cards appear in PT "Produção" column
        producao_col = state["columns"]["Produção"]
        assert len(producao_col) == 2
        card_ids = {c["id"] for c in producao_col}
        assert card_ids == {"c-en1", "c-en2"}

    def test_dual_read_invalid_json_cleanup(self, mock_redis):
        """Test that malformed JSON is cleaned up automatically."""
        valid_card = {"id": "c-valid", "title": "Valid", "status": "Espera", "created_at": "2025-10-02T10:00:00Z", "intent": "", "meta": {}}

        def lrange_side_effect(key, start, end):
            if key == "wf:board:col:Espera":
                return [
                    json.dumps(valid_card),
                    "INVALID_JSON{{{",  # Malformed JSON
                    "{incomplete",  # Incomplete JSON
                ]
            else:
                return []

        mock_redis.lrange.side_effect = lrange_side_effect
        mock_redis.lrem.return_value = 1

        with patch('wordflux_cockpit.rclient', return_value=mock_redis):
            state = get_board_state()

        # Assertions: Only valid card remains
        espera_col = state["columns"]["Espera"]
        assert len(espera_col) == 1
        assert espera_col[0]["id"] == "c-valid"

        # Verify malformed cards were removed
        assert mock_redis.lrem.call_count >= 2

    def test_dual_read_schema_validation(self, mock_redis):
        """Test that cards failing schema validation are rejected."""
        valid_card = {
            "id": "c-valid",
            "title": "Valid Card",
            "status": "Finalizado",
            "created_at": "2025-10-02T10:00:00Z",
            "intent": "",
            "meta": {}
        }
        invalid_card_missing_title = {
            "id": "c-invalid",
            "status": "Finalizado",
            "created_at": "2025-10-02T10:01:00Z"
            # Missing required "title" field
        }

        def lrange_side_effect(key, start, end):
            if key == "wf:board:col:Finalizado":
                return [
                    json.dumps(valid_card),
                    json.dumps(invalid_card_missing_title)
                ]
            else:
                return []

        mock_redis.lrange.side_effect = lrange_side_effect
        mock_redis.lrem.return_value = 1

        with patch('wordflux_cockpit.rclient', return_value=mock_redis):
            state = get_board_state()

        # Assertions: Only valid card remains
        finalizado_col = state["columns"]["Finalizado"]
        assert len(finalizado_col) == 1
        assert finalizado_col[0]["id"] == "c-valid"

        # Verify invalid card was removed
        assert mock_redis.lrem.called

    def test_dual_read_returns_5_pt_columns(self, mock_redis):
        """Test that get_board_state always returns 5 PT columns."""
        mock_redis.lrange.return_value = []  # No cards

        with patch('wordflux_cockpit.rclient', return_value=mock_redis):
            state = get_board_state()

        # Assertions
        assert "columns" in state
        assert len(state["columns"]) == 5
        assert set(state["columns"].keys()) == set(CANONICAL_PT_COLUMNS)

        # All columns should be empty lists
        for col in CANONICAL_PT_COLUMNS:
            assert state["columns"][col] == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
