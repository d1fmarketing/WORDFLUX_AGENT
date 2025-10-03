"""Cost tracking for LLM API usage."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Pricing as of January 2025 (USD per million tokens)
# Source: https://openai.com/pricing and https://www.anthropic.com/pricing
PRICING = {
    "openai": {
        "gpt-4o-mini": {
            "input": 0.150,    # $0.150 per 1M input tokens
            "output": 0.600    # $0.600 per 1M output tokens
        },
        "gpt-4o": {
            "input": 2.50,
            "output": 10.00
        },
        "gpt-4-turbo": {
            "input": 10.00,
            "output": 30.00
        }
    },
    "anthropic": {
        "claude-sonnet-4-5": {
            "input": 3.00,     # $3.00 per 1M input tokens
            "output": 15.00    # $15.00 per 1M output tokens
        },
        "claude-sonnet-4": {
            "input": 3.00,
            "output": 15.00
        },
        "claude-opus-4": {
            "input": 15.00,
            "output": 75.00
        },
        "claude-3-5-sonnet-20241022": {
            "input": 3.00,
            "output": 15.00
        }
    },
    "mock": {
        "default": {
            "input": 0.0,
            "output": 0.0
        }
    }
}


@dataclass
class CostEstimate:
    """Cost estimate for an LLM request."""
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float
    timestamp: str


def get_model_pricing(provider: str, model: str) -> Optional[dict]:
    """
    Get pricing for a specific model.

    Args:
        provider: Provider name (openai/anthropic/mock)
        model: Model name

    Returns:
        Dict with 'input' and 'output' pricing per million tokens, or None if not found
    """
    provider = provider.lower()

    if provider not in PRICING:
        logger.warning(f"Unknown provider for pricing: {provider}")
        return None

    provider_pricing = PRICING[provider]

    # Exact match
    if model in provider_pricing:
        return provider_pricing[model]

    # Fallback to default for provider
    if "default" in provider_pricing:
        return provider_pricing["default"]

    # Try partial match (e.g., "gpt-4o-mini-2024-07-18" matches "gpt-4o-mini")
    for model_key, pricing in provider_pricing.items():
        if model.startswith(model_key):
            return pricing

    logger.warning(f"Unknown model for pricing: {provider}/{model}")
    return None


def calculate_cost(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int
) -> CostEstimate:
    """
    Calculate cost for an LLM request.

    Args:
        provider: Provider name (openai/anthropic/mock)
        model: Model name
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens

    Returns:
        CostEstimate with detailed breakdown
    """
    pricing = get_model_pricing(provider, model)

    if pricing is None:
        # Return zero cost if pricing unknown
        return CostEstimate(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost_usd=0.0,
            output_cost_usd=0.0,
            total_cost_usd=0.0,
            timestamp=datetime.now(timezone.utc).isoformat()
        )

    # Calculate costs (pricing is per million tokens)
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    total_cost = input_cost + output_cost

    return CostEstimate(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_cost_usd=round(input_cost, 6),
        output_cost_usd=round(output_cost, 6),
        total_cost_usd=round(total_cost, 6),
        timestamp=datetime.now(timezone.utc).isoformat()
    )


def extract_token_counts_from_response(response: dict, provider: str) -> tuple[int, int]:
    """
    Extract token counts from LLM response.

    Args:
        response: LLM response dict
        provider: Provider name (for format differences)

    Returns:
        Tuple of (input_tokens, output_tokens)
    """
    # Try to extract from _raw_response (Anthropic format)
    if "_raw_response" in response:
        raw = response["_raw_response"]
        if "usage" in raw:
            usage = raw["usage"]
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            return input_tokens, output_tokens

    # Try OpenAI format (if we add it to response later)
    if "usage" in response:
        usage = response["usage"]
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        return input_tokens, output_tokens

    # Fallback: estimate based on message length (rough approximation)
    # 1 token ≈ 4 characters for English, ~2-3 for code
    message = response.get("message", "")
    estimated_output_tokens = len(message) // 4

    logger.warning(f"Token counts not found in response, estimated {estimated_output_tokens} output tokens")
    return 0, estimated_output_tokens


def track_request_cost(
    provider: str,
    model: str,
    response: dict
) -> CostEstimate:
    """
    Track cost for a completed LLM request.

    Args:
        provider: Provider name
        model: Model name
        response: LLM response dict

    Returns:
        CostEstimate with cost breakdown
    """
    input_tokens, output_tokens = extract_token_counts_from_response(response, provider)
    cost_estimate = calculate_cost(provider, model, input_tokens, output_tokens)

    # Update daily cost metric
    try:
        from src.core.metrics import update_chat_cost
        update_chat_cost(provider, cost_estimate.total_cost_usd)
    except ImportError:
        pass

    logger.info(f"Cost tracked: {provider}/{model} - ${cost_estimate.total_cost_usd:.6f} "
               f"({input_tokens} in + {output_tokens} out tokens)")

    return cost_estimate


__all__ = [
    "CostEstimate",
    "get_model_pricing",
    "calculate_cost",
    "extract_token_counts_from_response",
    "track_request_cost",
    "PRICING"
]