from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.queue import MemoryJobQueue, set_default_queue
from src.core.worker import Worker


@pytest.fixture
def chat_e2e_client(monkeypatch):
    """Test client with queue + SSE patched for integration scenarios."""
    queue = MemoryJobQueue()
    set_default_queue(None)
    set_default_queue(queue)

    events: list[tuple[str, dict]] = []

    monkeypatch.setattr("src.api.chat.get_redis_client", lambda: None)
    monkeypatch.setattr("src.api.chat.emit_chat_message", lambda **_: None)
    monkeypatch.setattr("src.api.chat.emit_sse_event", lambda *_, **__: None)

    monkeypatch.setattr("src.core.events.emit_job_queued", lambda **_: None)
    monkeypatch.setattr("src.core.events.emit_job_started", lambda *_, **__: None)
    monkeypatch.setattr("src.core.events.emit_job_succeeded", lambda *_, **__: None)
    monkeypatch.setattr("src.core.events.emit_job_failed", lambda *_, **__: None)

    def capture_pending(**payload):
        events.append(("pending", payload))

    def capture_created(card_id: str, title: str, list_name: str, meta=None):
        events.append(("created", {
            "card_id": card_id,
            "title": title,
            "list": list_name,
            "meta": meta or {}
        }))

    monkeypatch.setattr("src.core.events.emit_card_pending", capture_pending)
    monkeypatch.setattr("src.core.events.emit_card_created", capture_created)
    monkeypatch.setattr("src.core.events.emit_card_moved", lambda *_, **__: None)

    # Stub LLM client to return create_card tool call deterministically
    class StubLLM:
        def chat(self, messages, tools):
            return {
                "text": "Vou criar o card solicitado.",
                "tool_calls": [
                    {
                        "id": "toolu-1",
                        "name": "create_card",
                        "input": {
                            "title": "E2E Test",
                            "column": "Espera",
                        },
                    }
                ],
            }

    monkeypatch.setattr("src.api.chat.get_llm_client", lambda: StubLLM())

    # Stub board_operator agent to emit card.created event immediately
    class StubBoardOperator:
        name = "board_operator"

        def run(self, payload):  # noqa: D401
            capture_created("card-e2e", payload.get("title", "?"), payload.get("column", "Espera"))
            return {"success": True, "card": {"id": "card-e2e", "title": payload.get("title")}}

    def fake_create_agent(name):
        if name == "board_operator":
            return StubBoardOperator()
        raise RuntimeError(f"Unexpected agent: {name}")

    monkeypatch.setattr("src.core.registry.create_agent", fake_create_agent)

    # Silence metrics in tests
    monkeypatch.setattr("src.core.metrics.record_chat_message", lambda *_, **__: None, raising=False)
    monkeypatch.setattr("src.core.metrics.record_chat_request", lambda *_, **__: None, raising=False)
    monkeypatch.setattr("src.core.metrics.record_chat_tool_call", lambda *_, **__: None, raising=False)
    monkeypatch.setattr("src.core.metrics.record_job_latency_ms", lambda *_, **__: None, raising=False)
    monkeypatch.setattr("src.core.metrics.record_job_processed", lambda *_, **__: None, raising=False)

    client = TestClient(app)
    return client, events, queue


def test_chat_create_card_flow(chat_e2e_client):
    """End-to-end: chat → job → pending → created."""
    client, events, queue = chat_e2e_client

    response = client.post(
        "/chat/",
        json={"session_id": "sess-e2e", "text": "Crie o card \"E2E Test\" em Espera"},
    )

    assert response.status_code == 200
    data = response.json()
    assert "E2E Test" in data["message"]

    # card.pending emitted immediately
    pending_events = [evt for evt in events if evt[0] == "pending"]
    assert pending_events, "card.pending não emitido"

    # Process queued job synchronously
    worker = Worker(queue=queue)
    worker.run_once(timeout=0.01)

    created_events = [evt for evt in events if evt[0] == "created"]
    assert created_events, "card.created não emitido"
    created_payload = created_events[-1][1]
    assert created_payload["title"] == "E2E Test"
    assert created_payload["list"] == "Espera"
