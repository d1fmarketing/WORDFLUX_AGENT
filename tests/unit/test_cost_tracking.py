"""Tests for cost tracking module."""
from __future__ import annotations

import pytest

from src.core.cost_tracking import (
    calculate_cost,
    extract_token_counts_from_response,
    get_model_pricing,
    track_request_cost,
    PRICING
)


def test_pricing_data_structure():
    """Test pricing data has correct structure."""
    assert "openai" in PRICING
    assert "anthropic" in PRICING
    assert "mock" in PRICING

    # Check OpenAI models
    assert "gpt-4o-mini" in PRICING["openai"]
    assert "input" in PRICING["openai"]["gpt-4o-mini"]
    assert "output" in PRICING["openai"]["gpt-4o-mini"]

    # Check Anthropic models
    assert "claude-sonnet-4-5" in PRICING["anthropic"]


def test_get_model_pricing_exact_match():
    """Test exact model name matching."""
    pricing = get_model_pricing("openai", "gpt-4o-mini")
    assert pricing is not None
    assert pricing["input"] == 0.150
    assert pricing["output"] == 0.600


def test_get_model_pricing_partial_match():
    """Test partial model name matching (e.g., versioned models)."""
    pricing = get_model_pricing("openai", "gpt-4o-mini-2024-07-18")
    assert pricing is not None
    assert pricing["input"] == 0.150  # Should match gpt-4o-mini


def test_get_model_pricing_unknown():
    """Test unknown model returns None."""
    pricing = get_model_pricing("openai", "nonexistent-model")
    assert pricing is None


def test_calculate_cost_openai():
    """Test cost calculation for OpenAI."""
    cost = calculate_cost("openai", "gpt-4o-mini", 1_000_000, 1_000_000)

    assert cost.provider == "openai"
    assert cost.model == "gpt-4o-mini"
    assert cost.input_tokens == 1_000_000
    assert cost.output_tokens == 1_000_000
    assert cost.input_cost_usd == 0.15
    assert cost.output_cost_usd == 0.60
    assert cost.total_cost_usd == 0.75


def test_calculate_cost_anthropic():
    """Test cost calculation for Anthropic Claude."""
    cost = calculate_cost("anthropic", "claude-sonnet-4-5", 1_000_000, 1_000_000)

    assert cost.provider == "anthropic"
    assert cost.input_cost_usd == 3.0
    assert cost.output_cost_usd == 15.0
    assert cost.total_cost_usd == 18.0


def test_calculate_cost_realistic_request():
    """Test cost for realistic request size."""
    # Typical request: 150 input tokens, 200 output tokens
    cost = calculate_cost("anthropic", "claude-sonnet-4-5", 150, 200)

    # Manual calculation:
    # Input: (150 / 1_000_000) * 3.0 = 0.00045
    # Output: (200 / 1_000_000) * 15.0 = 0.003
    # Total: 0.00345
    expected = 0.00045 + 0.003
    assert abs(cost.total_cost_usd - expected) < 0.000001


def test_calculate_cost_zero_tokens():
    """Test cost calculation with zero tokens."""
    cost = calculate_cost("openai", "gpt-4o-mini", 0, 0)
    assert cost.total_cost_usd == 0.0


def test_calculate_cost_unknown_model():
    """Test cost calculation for unknown model returns zero."""
    cost = calculate_cost("unknown-provider", "unknown-model", 1000, 2000)
    assert cost.total_cost_usd == 0.0


def test_extract_token_counts_anthropic_format():
    """Test token extraction from Anthropic response format."""
    response = {
        "message": "Hello",
        "_raw_response": {
            "usage": {
                "input_tokens": 123,
                "output_tokens": 456
            }
        }
    }

    input_tok, output_tok = extract_token_counts_from_response(response, "anthropic")
    assert input_tok == 123
    assert output_tok == 456


def test_extract_token_counts_openai_format():
    """Test token extraction from OpenAI response format."""
    response = {
        "message": "Hello",
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 200
        }
    }

    input_tok, output_tok = extract_token_counts_from_response(response, "openai")
    assert input_tok == 100
    assert output_tok == 200


def test_extract_token_counts_fallback_estimation():
    """Test token count estimation when usage data missing."""
    response = {
        "message": "This is a test message with some content"
    }

    input_tok, output_tok = extract_token_counts_from_response(response, "unknown")
    assert input_tok == 0  # No input data
    assert output_tok > 0  # Estimated from message length


def test_track_request_cost_integration(monkeypatch):
    """Test track_request_cost integrates with metrics."""
    metrics_called = []

    def mock_update_cost(provider, cost):
        metrics_called.append((provider, cost))

    # Mock in the metrics module where it's actually defined
    monkeypatch.setattr("src.core.metrics.update_chat_cost", mock_update_cost)

    response = {
        "message": "Test",
        "_raw_response": {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 200
            }
        }
    }

    cost = track_request_cost("anthropic", "claude-sonnet-4-5", response)

    assert cost.input_tokens == 100
    assert cost.output_tokens == 200
    assert len(metrics_called) == 1
    assert metrics_called[0][0] == "anthropic"
    assert metrics_called[0][1] > 0  # Cost should be non-zero


def test_cost_rounding():
    """Test cost values are properly rounded to 6 decimals."""
    cost = calculate_cost("anthropic", "claude-sonnet-4-5", 17, 23)

    # Should have at most 6 decimal places
    assert len(str(cost.total_cost_usd).split('.')[-1]) <= 6


def test_anthropic_vs_openai_cost_ratio():
    """Test Anthropic is significantly more expensive than OpenAI."""
    anthropic_cost = calculate_cost("anthropic", "claude-sonnet-4-5", 1000, 1000)
    openai_cost = calculate_cost("openai", "gpt-4o-mini", 1000, 1000)

    ratio = anthropic_cost.total_cost_usd / openai_cost.total_cost_usd

    # Anthropic should be ~24x more expensive
    assert 20 <= ratio <= 30, f"Expected ratio ~24x, got {ratio:.1f}x"