#!/usr/bin/env python3
"""
Validation script for critical safety features.

Tests:
1. Circuit breaker functions
2. Token input cap estimation
3. Canary routing distribution
4. Idempotency (manual check)
"""
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_circuit_breaker():
    """Test circuit breaker functions."""
    print("=" * 70)
    print("TEST 1: Circuit Breaker")
    print("=" * 70)

    from src.core.llm_client import circuit_tripped, trip_circuit, reset_circuit

    # Test 1: Circuit should not be tripped initially
    assert not circuit_tripped("anthropic"), "Circuit should not be tripped initially"
    print("✓ Circuit not tripped initially")

    # Test 2: Trip circuit
    trip_circuit("anthropic", ttl_sec=10)
    assert circuit_tripped("anthropic"), "Circuit should be tripped after trip_circuit()"
    print("✓ Circuit trips successfully")

    # Test 3: Reset circuit
    reset_circuit("anthropic")
    assert not circuit_tripped("anthropic"), "Circuit should be reset after reset_circuit()"
    print("✓ Circuit resets successfully")

    print("\n✅ Circuit breaker: PASSED\n")


def test_token_estimation():
    """Test token input cap estimation."""
    print("=" * 70)
    print("TEST 2: Token Input Cap")
    print("=" * 70)

    from src.api.chat import estimate_token_count

    # Test conservative estimation (4 chars per token)
    test_cases = [
        ("Hello world", 3),           # 11 chars / 4 = 2.75 → 3 tokens
        ("A" * 1000, 250),            # 1000 chars / 4 = 250 tokens
        ("Short", 2),                 # 5 chars / 4 = 1.25 → 2 tokens
        ("X" * 8000, 2000),           # 8000 chars / 4 = 2000 tokens (default cap)
    ]

    for text, expected_tokens in test_cases:
        estimated = estimate_token_count(text)
        assert estimated == expected_tokens, f"Expected {expected_tokens}, got {estimated}"
        print(f"✓ '{text[:20]}...' = {estimated} tokens (expected {expected_tokens})")

    print("\n✅ Token estimation: PASSED\n")


def test_canary_routing_distribution():
    """Test canary routing distributes traffic correctly."""
    print("=" * 70)
    print("TEST 3: Canary Routing Distribution")
    print("=" * 70)

    from src.api.chat import get_provider_for_request

    # Set up environment
    os.environ["WF_CANARY_PCT"] = "10"
    os.environ["WF_CANARY_PROVIDER"] = "anthropic"
    os.environ["WF_LLM_PROVIDER"] = "openai"
    os.environ["WF_LLM_PROVIDER_FALLBACK"] = "openai"

    # Test 1000 sessions
    providers = [get_provider_for_request(f"sess-{i}") for i in range(1000)]

    anthropic_count = providers.count("anthropic")
    openai_count = providers.count("openai")
    anthropic_pct = (anthropic_count / 1000) * 100

    print(f"Distribution: {anthropic_count} anthropic ({anthropic_pct:.1f}%), {openai_count} openai")

    # Validate 8-12% for 10% canary (±20% variance)
    assert 80 <= anthropic_count <= 120, f"Expected 80-120 anthropic, got {anthropic_count}"
    print(f"✓ Canary routing within acceptable range (8-12%)")

    # Test session consistency
    session_providers = [get_provider_for_request("consistent-session") for _ in range(10)]
    assert len(set(session_providers)) == 1, "Same session should always get same provider"
    print(f"✓ Session consistency maintained")

    print("\n✅ Canary routing: PASSED\n")


def test_circuit_breaker_with_canary():
    """Test circuit breaker overrides canary routing."""
    print("=" * 70)
    print("TEST 4: Circuit Breaker + Canary Integration")
    print("=" * 70)

    from src.api.chat import get_provider_for_request
    from src.core.llm_client import trip_circuit, reset_circuit

    # Set up 100% canary to anthropic
    os.environ["WF_CANARY_PCT"] = "100"
    os.environ["WF_CANARY_PROVIDER"] = "anthropic"
    os.environ["WF_LLM_PROVIDER"] = "openai"
    os.environ["WF_LLM_PROVIDER_FALLBACK"] = "openai"

    # Before circuit trip, should route to anthropic
    provider = get_provider_for_request("test-session-1")
    assert provider == "anthropic", "Should route to anthropic with 100% canary"
    print("✓ Routes to anthropic with 100% canary (before circuit trip)")

    # Trip circuit
    trip_circuit("anthropic", ttl_sec=10)

    # After circuit trip, should route to fallback (openai)
    providers = [get_provider_for_request(f"test-session-{i}") for i in range(10)]
    assert all(p == "openai" for p in providers), "Should route to fallback when circuit tripped"
    print("✓ Routes to fallback (openai) when circuit tripped")

    # Reset for next tests
    reset_circuit("anthropic")
    print("\n✅ Circuit breaker + canary: PASSED\n")


def main():
    """Run all validation tests."""
    print("\n" + "=" * 70)
    print("VALIDATING CRITICAL SAFETY FEATURES")
    print("=" * 70 + "\n")

    try:
        test_circuit_breaker()
        test_token_estimation()
        test_canary_routing_distribution()
        test_circuit_breaker_with_canary()

        print("=" * 70)
        print("🎉 ALL VALIDATION TESTS PASSED!")
        print("=" * 70)
        print("\nFeatures validated:")
        print("  ✓ Circuit breaker (trip, check, reset)")
        print("  ✓ Token input cap estimation")
        print("  ✓ Canary routing distribution (10% → 8-12%)")
        print("  ✓ Circuit breaker overrides canary routing")
        print("\nReady for staging deployment!")
        return 0

    except AssertionError as e:
        print(f"\n❌ VALIDATION FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())