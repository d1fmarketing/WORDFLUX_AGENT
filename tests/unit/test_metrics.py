"""Basic smoke test for metrics endpoint."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


def test_metrics_endpoint_not_exposed():
    """Test that metrics endpoint is exposed and returns Prometheus format."""
    client = TestClient(app)
    response = client.get("/metrics")
    # Metrics endpoint is now exposed (returns Prometheus-format metrics)
    assert response.status_code == 200
    # Check for Prometheus-format metrics
    assert "wordflux_" in response.text or "# HELP" in response.text


def test_metrics_module_imports():
    """Test that metrics module can be imported."""
    try:
        from src.core.metrics import (
            record_job_processed,
            record_job_enqueued,
            update_queue_metrics,
        )
        # Metrics module should be importable
        assert callable(record_job_processed)
        assert callable(record_job_enqueued)
        assert callable(update_queue_metrics)
    except ImportError:
        # OK if prometheus_client is not installed
        pytest.skip("prometheus_client not installed")