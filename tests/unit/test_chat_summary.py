from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.queue import MemoryJobQueue, set_default_queue
from src.api.chat import BoardSummaryUnavailable


@pytest.fixture
def chat_client(monkeypatch):
    """FastAPI test client with chat dependencies patched."""
    set_default_queue(None)
    set_default_queue(MemoryJobQueue())

    # Disable Redis interactions inside chat module
    monkeypatch.setattr("src.api.chat.get_redis_client", lambda: None)
    monkeypatch.setattr("src.api.chat.emit_chat_message", lambda **_: None)
    monkeypatch.setattr("src.api.chat.emit_sse_event", lambda *_, **__: None)

    return TestClient(app)


def test_chat_summary_success(chat_client, monkeypatch):
    """Resumo direto retorna mensagem com colunas PT."""
    sample_snapshot = {
        "columns": {
            "Espera": [{"id": "c1", "title": "A"}],
            "Produção": [{"id": "c2", "title": "B"}],
            "Aprovação": [],
            "Agendado": [],
            "Finalizado": [{"id": "c3", "title": "C"}],
        },
        "autopilot": False,
    }

    monkeypatch.setattr("src.api.chat.get_board_snapshot", lambda: sample_snapshot)

    response = chat_client.post(
        "/chat/",
        json={"session_id": "sess-test", "text": "Resumo do board"},
    )

    assert response.status_code == 200
    data = response.json()
    message = data["message"]

    assert message.startswith("Total: ")
    for col in ["Espera", "Produção", "Aprovação", "Agendado", "Finalizado"]:
        assert f"{col} " in message


def test_chat_summary_health_gate(chat_client, monkeypatch):
    """Resumo direto retorna fallback quando board indisponível."""
    def _failing_summary(scope=None):  # noqa: D401
        raise BoardSummaryUnavailable("slow_store", duration_ms=450)

    monkeypatch.setattr("src.api.chat.execute_summarize_board", _failing_summary)

    record_skipped = MagicMock()
    monkeypatch.setattr("src.core.metrics.record_summary_skipped", record_skipped)
    monkeypatch.setattr("src.core.metrics.record_chat_request", lambda *_, **__: None)

    response = chat_client.post(
        "/chat/",
        json={"session_id": "sess-slow", "text": "Resumo do board"},
    )

    assert response.status_code == 200
    message = response.json()["message"]
    assert message.startswith("📊 O board está indisponível")
    record_skipped.assert_called_once()
