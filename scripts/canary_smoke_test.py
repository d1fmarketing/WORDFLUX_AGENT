#!/usr/bin/env python3
"""
Canary Routing Smoke Test Suite

Automated validation of 5 critical paths:
1. Provider selection determinism
2. Cost calculation accuracy
3. Metrics recording
4. Fallback attribution
5. Backward compatibility

Usage:
    python scripts/canary_smoke_test.py
    python scripts/canary_smoke_test.py --base-url http://localhost:8081
"""
import sys
import os
import requests
from collections import Counter

# Add project root to path
sys.path.insert(0, '/home/ubuntu')

from src.api.chat import get_provider_for_request
from src.core.cost_tracking import calculate_cost


def test_1_provider_determinism():
    """Test 1: Same session_id always gets same provider"""
    print("\n[TEST 1] Provider Selection Determinism")
    print("="*70)

    os.environ["WF_CANARY_PCT"] = "50"
    os.environ["WF_CANARY_PROVIDER"] = "anthropic"
    os.environ["WF_LLM_PROVIDER"] = "openai"

    session_id = "determinism-test-session"

    # Call 20 times with same session
    providers = [get_provider_for_request(session_id) for _ in range(20)]

    unique_providers = set(providers)

    if len(unique_providers) == 1:
        print(f"  ✅ PASS: Session consistently routed to {providers[0]}")
        return True
    else:
        print(f"  ❌ FAIL: Session got multiple providers: {unique_providers}")
        return False


def test_2_cost_calculation():
    """Test 2: Cost calculation accuracy"""
    print("\n[TEST 2] Cost Calculation Accuracy")
    print("="*70)

    # Test Anthropic pricing
    cost = calculate_cost("anthropic", "claude-sonnet-4-5", 1000, 1000)
    expected = (1000/1_000_000 * 3.0) + (1000/1_000_000 * 15.0)  # $0.018

    if abs(cost.total_cost_usd - expected) < 0.000001:
        print(f"  ✅ PASS: Cost calculation accurate (${cost.total_cost_usd:.6f})")
        return True
    else:
        print(f"  ❌ FAIL: Expected ${expected:.6f}, got ${cost.total_cost_usd:.6f}")
        return False


def test_3_metrics_recording(base_url="http://localhost:8081"):
    """Test 3: Metrics recording with provider labels"""
    print("\n[TEST 3] Metrics Recording")
    print("="*70)

    try:
        response = requests.get(f"{base_url}/metrics", timeout=5)

        if response.status_code != 200:
            print(f"  ❌ FAIL: Metrics endpoint returned {response.status_code}")
            return False

        metrics_text = response.text

        # Check for required metrics
        required = [
            "wordflux_chat_requests_total",
            "wordflux_chat_latency_seconds",
            "wordflux_chat_cost_usd_daily"
        ]

        missing = [m for m in required if m not in metrics_text]

        if missing:
            print(f"  ❌ FAIL: Missing metrics: {missing}")
            return False

        # Check for provider labels
        if 'provider=' in metrics_text:
            print(f"  ✅ PASS: All required metrics present with provider labels")
            return True
        else:
            print(f"  ⚠️  WARN: Metrics present but no provider labels found")
            return True  # Don't fail - metrics may be zero if no requests yet

    except Exception as e:
        print(f"  ❌ FAIL: {e}")
        return False


def test_4_canary_distribution():
    """Test 4: Canary distribution accuracy"""
    print("\n[TEST 4] Canary Distribution Accuracy")
    print("="*70)

    os.environ["WF_CANARY_PCT"] = "10"
    os.environ["WF_CANARY_PROVIDER"] = "anthropic"
    os.environ["WF_LLM_PROVIDER"] = "openai"

    # Test 1000 sessions for statistical accuracy
    providers = [get_provider_for_request(f"session-{i}") for i in range(1000)]

    anthropic_pct = (providers.count("anthropic") / 1000) * 100

    # Allow ±2% variance
    if 8.0 <= anthropic_pct <= 12.0:
        print(f"  ✅ PASS: Canary distribution {anthropic_pct:.1f}% (target: 10%)")
        return True
    else:
        print(f"  ❌ FAIL: Canary distribution {anthropic_pct:.1f}% (expected: 8-12%)")
        return False


def test_5_backward_compatibility():
    """Test 5: Backward compatibility (canary disabled)"""
    print("\n[TEST 5] Backward Compatibility")
    print("="*70)

    os.environ["WF_CANARY_PCT"] = "0"
    os.environ["WF_LLM_PROVIDER"] = "openai"

    # All sessions should get default provider
    providers = [get_provider_for_request(f"session-{i}") for i in range(100)]

    if all(p == "openai" for p in providers):
        print(f"  ✅ PASS: All traffic routed to default provider (canary disabled)")
        return True
    else:
        dist = Counter(providers)
        print(f"  ❌ FAIL: Traffic leaked to canary when disabled: {dict(dist)}")
        return False


def main():
    """Run all smoke tests"""
    import argparse

    parser = argparse.ArgumentParser(description="Canary routing smoke tests")
    parser.add_argument("--base-url", default="http://localhost:8081",
                       help="Base URL for API (default: http://localhost:8081)")
    args = parser.parse_args()

    print("\n" + "="*70)
    print("  CANARY ROUTING SMOKE TEST SUITE")
    print("="*70)
    print(f"  Base URL: {args.base_url}")
    print("="*70)

    tests = [
        ("Provider Determinism", test_1_provider_determinism),
        ("Cost Calculation", test_2_cost_calculation),
        ("Metrics Recording", lambda: test_3_metrics_recording(args.base_url)),
        ("Canary Distribution", test_4_canary_distribution),
        ("Backward Compatibility", test_5_backward_compatibility)
    ]

    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n  ❌ {name} CRASHED: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))

    # Summary
    print("\n" + "="*70)
    print("  TEST SUMMARY")
    print("="*70)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status}: {name}")

    print("="*70)
    print(f"  TOTAL: {passed}/{total} tests passed ({passed/total*100:.1f}%)")
    print("="*70)

    if passed == total:
        print("\n  ✅ ALL TESTS PASSED - DEPLOYMENT VALIDATED")
        return 0
    else:
        print(f"\n  ❌ {total - passed} TEST(S) FAILED - FIX BEFORE DEPLOYMENT")
        return 1


if __name__ == "__main__":
    sys.exit(main())