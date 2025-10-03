"""Tests for the FastAPI application."""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest
import fakeredis
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.queue import MemoryJobQueue, set_default_queue


@pytest.fixture
def test_client(monkeypatch):
    """Create a test client for the FastAPI app."""
    # Reset queue to memory mode for testing
    set_default_queue(None)  # Clear cached queue first
    set_default_queue(MemoryJobQueue())
    # Also clear any Redis client and disable idempotency by default
    import src.api.main
    src.api.main.redis_client = None
    # Make get_redis_client return None to disable idempotency
    monkeypatch.setattr("src.api.main.get_redis_client", lambda: None)
    return TestClient(app)


@pytest.fixture
def fake_redis():
    """Create a fake Redis client for testing."""
    return fakeredis.FakeStrictRedis(decode_responses=True)


def test_health_endpoint(test_client, monkeypatch):
    """Test the health check endpoint."""
    # Mock Redis client to be unavailable
    monkeypatch.setattr("src.api.main.get_redis_client", lambda: None)

    response = test_client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data
    assert data["redis"] == "unavailable"
    assert data["queue_mode"] == "memory"


def test_health_endpoint_with_redis(test_client, fake_redis, monkeypatch):
    """Test the health check endpoint with Redis available."""
    # Mock Redis client to return fake Redis
    monkeypatch.setattr("src.api.main.get_redis_client", lambda: fake_redis)

    response = test_client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "healthy"
    assert data["redis"] == "connected"


def test_event_endpoint_basic(test_client):
    """Test basic event submission."""
    event_data = {
        "event_type": "echo",
        "payload": {"message": "Test message"}
    }

    response = test_client.post("/event", json=event_data)
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "enqueued"
    assert "job_id" in data
    assert data["duplicate"] is False
    assert "echo" in data["message"]


def test_event_endpoint_idempotency(test_client, fake_redis, monkeypatch):
    """Test idempotency with explicit idempotency key."""
    # Mock Redis client for idempotency
    monkeypatch.setattr("src.api.main.get_redis_client", lambda: fake_redis)

    event_data = {
        "event_type": "echo",
        "payload": {"message": "Test message"},
        "idempotency_key": "test-key-123"
    }

    # First request
    response1 = test_client.post("/event", json=event_data)
    assert response1.status_code == 200
    data1 = response1.json()
    assert data1["duplicate"] is False
    job_id1 = data1["job_id"]

    # Second request with same idempotency key
    response2 = test_client.post("/event", json=event_data)
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["duplicate"] is True
    assert data2["job_id"] == job_id1  # Same job ID returned


def test_event_endpoint_auto_idempotency(test_client, fake_redis, monkeypatch):
    """Test automatic idempotency based on request content."""
    # Mock Redis client for idempotency
    monkeypatch.setattr("src.api.main.get_redis_client", lambda: fake_redis)

    event_data = {
        "event_type": "slack.notify",
        "payload": {"channel": "#general", "message": "Hello"}
        # No explicit idempotency_key
    }

    # First request
    response1 = test_client.post("/event", json=event_data)
    assert response1.status_code == 200
    data1 = response1.json()
    assert data1["duplicate"] is False

    # Second request with same content (auto idempotency)
    response2 = test_client.post("/event", json=event_data)
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["duplicate"] is True
    assert data2["job_id"] == data1["job_id"]


def test_event_endpoint_unknown_type(test_client):
    """Test handling of unknown event type."""
    event_data = {
        "event_type": "unknown.type",
        "payload": {"data": "test"}
    }

    response = test_client.post("/event", json=event_data)
    assert response.status_code == 400
    assert "Unknown event type" in response.json()["detail"]


def test_event_endpoint_stripe_dispute(test_client):
    """Test Stripe dispute event handling."""
    event_data = {
        "event_type": "stripe.dispute",
        "payload": {"dispute_id": "dp_123", "amount": 5000}
    }

    response = test_client.post("/event", json=event_data)
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "enqueued"
    assert "stripe_disputes" in data["message"]


def test_event_endpoint_slack_notify(test_client):
    """Test Slack notification event handling."""
    event_data = {
        "event_type": "slack.notify",
        "payload": {
            "channel": "#alerts",
            "message": "Export ready",
            "csv_url": "https://example.com/export.csv"
        }
    }

    response = test_client.post("/event", json=event_data)
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "enqueued"
    assert "slack.notify" in data["message"]


def test_event_endpoint_metrics_disabled(test_client, monkeypatch):
    """Test that the API works when metrics are disabled."""
    # Disable metrics
    test_client.app.state.metrics_enabled = False

    event_data = {
        "event_type": "echo",
        "payload": {"message": "Test"}
    }

    response = test_client.post("/event", json=event_data)
    assert response.status_code == 200


def test_event_endpoint_with_metrics(test_client):
    """Test that the API works when metrics are enabled."""
    # Simply verify the API works when metrics flag is enabled
    # The actual metrics recording is tested in test_metrics.py
    test_client.app.state.metrics_enabled = True

    event_data = {
        "event_type": "echo",
        "payload": {"message": "Test"}
    }

    response = test_client.post("/event", json=event_data)
    assert response.status_code == 200

    # The API should work normally with metrics enabled
    data = response.json()
    assert data["status"] == "enqueued"