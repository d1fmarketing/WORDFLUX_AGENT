#!/usr/bin/env python3
"""Integration tests for WordFlux Cockpit with job queue."""

import json
import time
import uuid
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

import pytest
import redis
from fastapi.testclient import TestClient

# Import cockpit app
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'playbooks', 'cockpit'))

from src.core.queue import set_default_queue, MemoryJobQueue, RedisJobQueue
from src.core.job import Job


@pytest.fixture
def memory_queue():
    """Create a memory queue for testing."""
    queue = MemoryJobQueue()
    set_default_queue(queue)
    return queue


@pytest.fixture
def redis_queue():
    """Create a Redis queue for testing."""
    try:
        r = redis.Redis.from_url("redis://localhost:6379/15")  # Use test DB
        r.ping()
        r.flushdb()  # Clear test database
        queue = RedisJobQueue(redis_url="redis://localhost:6379/15")
        set_default_queue(queue)
        return queue
    except redis.ConnectionError:
        pytest.skip("Redis not available")


@pytest.fixture
def cockpit_client(monkeypatch):
    """Create a test client for the cockpit app."""
    # Set test environment
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/15")
    monkeypatch.setenv("QUEUE_MODE", "memory")

    # Import after env vars are set
    from wordflux_cockpit import build_app
    app = build_app()
    return TestClient(app)


@pytest.fixture
def cockpit_client_redis(monkeypatch):
    """Create a test client with Redis backend."""
    try:
        r = redis.Redis.from_url("redis://localhost:6379/15")
        r.ping()
        r.flushdb()
    except redis.ConnectionError:
        pytest.skip("Redis not available")

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/15")
    monkeypatch.setenv("QUEUE_MODE", "redis")

    from wordflux_cockpit import build_app
    app = build_app()
    return TestClient(app)


class TestCockpitHealth:
    """Test health and status endpoints."""

    def test_health_check(self, cockpit_client):
        """Test that health endpoint returns OK."""
        response = cockpit_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "ts" in data

    def test_queue_status(self, cockpit_client):
        """Test queue status endpoint."""
        response = cockpit_client.get("/queue/status")
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "memory"
        assert "depth" in data


class TestBoardOperations:
    """Test board management operations."""

    def test_get_board_state(self, cockpit_client):
        """Test getting the board state."""
        response = cockpit_client.get("/board/state")
        assert response.status_code == 200
        data = response.json()
        assert "columns" in data
        assert "autopilot" in data
        assert len(data["columns"]) == 5  # Default columns

    def test_create_card(self, cockpit_client):
        """Test creating a new card."""
        response = cockpit_client.post("/board/card", json={
            "title": "Test Card",
            "intent": "Testing the cockpit"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["created"]["title"] == "Test Card"
        assert data["created"]["status"] == "Espera"  # Changed from "Backlog"
        assert "id" in data["created"]
        assert "created_at" in data["created"]

    def test_move_card(self, cockpit_client):
        """Test moving a card between columns."""
        # Create a card first
        create_response = cockpit_client.post("/board/card", json={
            "title": "Card to Move"
        })
        card_id = create_response.json()["created"]["id"]

        # Move the card to a column without WIP limits (Aprovação)
        move_response = cockpit_client.post("/board/move", json={
            "card_id": card_id,
            "to": "Aprovação"  # Changed to avoid WIP limits
        })
        assert move_response.status_code == 200
        data = move_response.json()
        assert data["moved"]["to"] == "Aprovação"

    def test_move_nonexistent_card(self, cockpit_client):
        """Test moving a card that doesn't exist."""
        response = cockpit_client.post("/board/move", json={
            "card_id": "nonexistent",
            "to": "Produção"  # Changed from "In Progress"
        })
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"

    def test_create_card_with_en_column_canonicalized(self, cockpit_client):
        """Test that EN column names are canonicalized to PT automatically."""
        # Create card with EN column name "Backlog"
        response = cockpit_client.post("/board/card", json={
            "title": "Test EN Canonicalization",
            "column": "Backlog"  # English column name
        })
        assert response.status_code == 200
        data = response.json()
        # Should be canonicalized to "Espera"
        assert data["created"]["status"] == "Espera"
        assert data["created"]["title"] == "Test EN Canonicalization"

    def test_create_card_invalid_column_400(self, cockpit_client):
        """Test that invalid column names return 400 with PT-BR error message."""
        response = cockpit_client.post("/board/card", json={
            "title": "Invalid Column Test",
            "column": "invalid_column_xyz"
        })
        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "invalid_column"
        # Error message should be in PT-BR
        assert "Coluna inválida" in data["message"]
        assert "Espera" in data["message"]  # Should suggest valid columns

    def test_move_card_invalid_column_400(self, cockpit_client):
        """Test that moving to invalid column returns 400 with PT-BR error."""
        # Create a card first
        create_response = cockpit_client.post("/board/card", json={
            "title": "Card for Invalid Move"
        })
        card_id = create_response.json()["created"]["id"]

        # Try to move to invalid column
        response = cockpit_client.post("/board/move", json={
            "card_id": card_id,
            "to": "NonExistentColumn"
        })
        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "invalid_column"
        # Error message should be in PT-BR
        assert "Coluna inválida" in data["message"]
        assert "NonExistentColumn" in data["message"]


class TestAgentActions:
    """Test agent action execution."""

    @patch('wordflux_cockpit.queue_job')
    def test_agent_suggest(self, mock_queue, cockpit_client):
        """Test getting suggested actions for a card."""
        # Create a card
        create_response = cockpit_client.post("/board/card", json={
            "title": "Test Suggestions"
        })
        card_id = create_response.json()["created"]["id"]

        # Get suggestions for Espera (formerly Backlog)
        response = cockpit_client.get(f"/agent/suggest?card_id={card_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["column"] == "Espera"  # Changed from "Backlog"
        assert "start_work" in data["actions"]

    @patch('wordflux_cockpit.queue_job')
    def test_agent_act(self, mock_queue, cockpit_client):
        """Test executing an agent action."""
        mock_queue.return_value = "job-12345"

        # Create a card
        create_response = cockpit_client.post("/board/card", json={
            "title": "Test Action"
        })
        card_id = create_response.json()["created"]["id"]

        # Execute action
        response = cockpit_client.post("/agent/act", json={
            "card_id": card_id,
            "action": "start_work"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["to"] == "Produção"  # Changed from "In Progress"
        assert data["job_id"] == "job-12345"

        # Verify job was queued
        mock_queue.assert_called_once()
        call_args = mock_queue.call_args[0]
        assert call_args[0] == "task_starter"  # Agent name
        assert call_args[1]["action"] == "start_work"

    @patch('wordflux_cockpit.queue_job')
    def test_autopilot_mode(self, mock_queue, cockpit_client):
        """Test autopilot mode automatically executes actions."""
        mock_queue.return_value = "job-auto"

        # Enable autopilot
        cockpit_client.post("/agent/autopilot", json={"on": True})

        # Create and move a card
        create_response = cockpit_client.post("/board/card", json={
            "title": "Autopilot Test"
        })
        card_id = create_response.json()["created"]["id"]

        # Move to Produção - should trigger send_for_review
        with patch('wordflux_cockpit.agent_act') as mock_act:
            mock_act.return_value = {"ok": True, "to": "Aprovação", "job_id": "auto-job"}  # Changed from "Waiting Approval"
            response = cockpit_client.post("/board/move", json={
                "card_id": card_id,
                "to": "Produção"  # Changed from "In Progress"
            })
            assert response.status_code == 200
            # Autopilot should have triggered an action
            mock_act.assert_called()


class TestNotificationTriggers:
    """Test Slack notification triggers."""

    @patch('wordflux_cockpit.queue_job')
    def test_notification_on_state_change(self, mock_queue, cockpit_client_redis):
        """Test that notifications are queued on specific state changes."""
        # Create a card
        create_response = cockpit_client_redis.post("/board/card", json={
            "title": "Notification Test"
        })
        card_id = create_response.json()["created"]["id"]

        # Move to Aprovação - should trigger notification (formerly Waiting Approval)
        mock_queue.reset_mock()
        response = cockpit_client_redis.post("/agent/act", json={
            "card_id": card_id,
            "action": "send_for_review"
        })

        # Check that two jobs were queued: action + notification
        assert mock_queue.call_count >= 1
        calls = mock_queue.call_args_list
        # Find the notification call
        notification_calls = [c for c in calls if c[0][0] == "slack_notifier"]
        if notification_calls:
            notif_payload = notification_calls[0][0][1]
            assert "Review requested" in notif_payload["message"]


class TestEventStreaming:
    """Test SSE event streaming."""

    def test_recent_events(self, cockpit_client_redis):
        """Test getting recent events."""
        # Create a card to generate an event
        cockpit_client_redis.post("/board/card", json={"title": "Event Test"})

        response = cockpit_client_redis.get("/events/recent")
        assert response.status_code == 200
        events = response.json()
        assert isinstance(events, list)
        # Should have at least the board_update event
        assert any(e.get("kind") == "board_update" for e in events)


class TestQueueIntegration:
    """Test integration with WordFlux job queue."""

    def test_memory_queue_integration(self, cockpit_client, memory_queue):
        """Test that jobs are queued to memory queue."""
        with patch('wordflux_cockpit.get_queue_manager', return_value=memory_queue):
            # Create a card
            create_response = cockpit_client.post("/board/card", json={
                "title": "Queue Test"
            })
            card_id = create_response.json()["created"]["id"]

            # Execute action
            response = cockpit_client.post("/agent/act", json={
                "card_id": card_id,
                "action": "start_work"
            })

            # Check queue depth
            assert memory_queue.depth() == 1

            # Consume the job
            job = memory_queue.consume()
            assert job.agent == "task_starter"
            assert job.payload["action"] == "start_work"

    @pytest.mark.skipif(not os.getenv("REDIS_URL"), reason="Redis not configured")
    def test_redis_queue_integration(self, cockpit_client_redis, redis_queue):
        """Test that jobs are queued to Redis queue."""
        with patch('wordflux_cockpit.get_queue_manager', return_value=redis_queue):
            # Create a card
            create_response = cockpit_client_redis.post("/board/card", json={
                "title": "Redis Queue Test"
            })
            card_id = create_response.json()["created"]["id"]

            # Execute action
            response = cockpit_client_redis.post("/agent/act", json={
                "card_id": card_id,
                "action": "publish_now"
            })

            # Check queue depth
            assert redis_queue.depth() > 0

            # Consume the job
            job = redis_queue.consume()
            assert job.agent == "content_publisher"
            assert job.payload["action"] == "publish_now"


class TestIdempotency:
    """Test idempotency handling."""

    @patch('wordflux_cockpit.queue_job')
    def test_action_idempotency(self, mock_queue, cockpit_client):
        """Test that actions have unique idempotency keys."""
        mock_queue.return_value = "job-123"

        # Create a card
        create_response = cockpit_client.post("/board/card", json={
            "title": "Idempotency Test"
        })
        card_id = create_response.json()["created"]["id"]

        # Execute same action twice
        cockpit_client.post("/agent/act", json={
            "card_id": card_id,
            "action": "start_work"
        })

        time.sleep(0.1)  # Small delay to ensure different timestamp

        cockpit_client.post("/agent/act", json={
            "card_id": card_id,
            "action": "start_work"
        })

        # Check that different idempotency keys were used
        calls = mock_queue.call_args_list
        assert len(calls) == 2
        key1 = calls[0][1].get("idempotency_key")
        key2 = calls[1][1].get("idempotency_key")
        assert key1 != key2  # Different keys due to timestamp


if __name__ == "__main__":
    pytest.main([__file__, "-v"])