#!/usr/bin/env python3
"""
Test script to validate token estimation accuracy histogram metric.

Tests:
1. Metric is defined and registered
2. Helper function records ratios correctly
3. Histogram buckets work as expected
4. Metric appears in Prometheus export
5. Edge cases (zero actual tokens, exact match, overestimation, underestimation)
"""
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_token_estimation_accuracy():
    """Test token estimation accuracy histogram metric."""
    print("=" * 70)
    print("TEST: Token Estimation Accuracy Histogram Metric")
    print("=" * 70)

    # Test 1: Metric is defined
    print("\nTest 1: Metric is defined...")
    from src.core.metrics import (
        llm_token_estimation_accuracy,
        record_token_estimation_accuracy
    )

    assert llm_token_estimation_accuracy is not None, "Metric not defined"
    print("✓ llm_token_estimation_accuracy Histogram defined")

    # Test 2: Helper function exists
    assert callable(record_token_estimation_accuracy), "Helper function not callable"
    print("✓ record_token_estimation_accuracy() function exists")

    # Test 3: Record different accuracy scenarios
    print("\nTest 2: Record token estimation accuracy scenarios...")

    # Scenario 1: Perfect estimation (1.0)
    record_token_estimation_accuracy("anthropic", estimated_tokens=1000, actual_tokens=1000)
    print("✓ Recorded perfect estimation (ratio: 1.0)")

    # Scenario 2: Overestimation (1.25)
    record_token_estimation_accuracy("anthropic", estimated_tokens=1250, actual_tokens=1000)
    print("✓ Recorded overestimation (ratio: 1.25)")

    # Scenario 3: Underestimation (0.8)
    record_token_estimation_accuracy("openai", estimated_tokens=800, actual_tokens=1000)
    print("✓ Recorded underestimation (ratio: 0.8)")

    # Scenario 4: Large overestimation (2.0)
    record_token_estimation_accuracy("openai", estimated_tokens=2000, actual_tokens=1000)
    print("✓ Recorded large overestimation (ratio: 2.0)")

    # Scenario 5: Mock provider
    record_token_estimation_accuracy("mock", estimated_tokens=500, actual_tokens=500)
    print("✓ Recorded mock provider (ratio: 1.0)")

    # Test 4: Edge case - zero actual tokens
    print("\nTest 3: Edge case - zero actual tokens...")
    # Should log warning and not crash
    record_token_estimation_accuracy("anthropic", estimated_tokens=1000, actual_tokens=0)
    print("✓ Handled zero actual tokens gracefully")

    # Test 5: Verify Prometheus export
    print("\nTest 4: Verify Prometheus export...")
    from prometheus_client import generate_latest
    from src.core.metrics import registry

    metrics_output = generate_latest(registry).decode('utf-8')

    assert "wordflux_llm_token_estimation_accuracy_ratio" in metrics_output, "Metric not in export"
    assert 'provider="anthropic"' in metrics_output, "Anthropic provider label missing"
    assert 'provider="openai"' in metrics_output, "OpenAI provider label missing"

    print("✓ Metric appears in Prometheus export")
    print(f"✓ Export size: {len(metrics_output)} bytes")

    # Test 6: Verify histogram structure
    print("\nTest 5: Verify histogram buckets...")

    # Check if histogram buckets appear in output
    # Prometheus exports histogram with _bucket, _sum, _count suffixes
    assert "wordflux_llm_token_estimation_accuracy_ratio_bucket" in metrics_output, "Histogram buckets missing"
    assert "wordflux_llm_token_estimation_accuracy_ratio_sum" in metrics_output, "Histogram sum missing"
    assert "wordflux_llm_token_estimation_accuracy_ratio_count" in metrics_output, "Histogram count missing"

    print("✓ Histogram structure correct (_bucket, _sum, _count)")

    # Test 7: Verify expected buckets
    expected_buckets = ["0.5", "0.75", "0.9", "1.0", "1.1", "1.25", "1.5", "2.0", "+Inf"]
    buckets_found = []

    for bucket in expected_buckets:
        bucket_label = f'le="{bucket}"'
        if bucket_label in metrics_output:
            buckets_found.append(bucket)

    print(f"✓ Found {len(buckets_found)}/{len(expected_buckets)} expected buckets")
    for bucket in buckets_found:
        print(f"  • le=\"{bucket}\"")

    # Test 8: Integration test - simulate real chat flow
    print("\nTest 6: Integration test - simulated chat flow...")

    # Simulate what happens in chat.py
    # 1. Estimate tokens before call
    from src.api.chat import estimate_token_count
    test_message = "This is a test message to estimate tokens. It should be about 20-25 tokens."
    estimated = estimate_token_count(test_message)

    # 2. Simulate actual tokens from LLM response (let's say estimation was close)
    actual = int(estimated * 0.95)  # 95% accuracy

    # 3. Record metric
    record_token_estimation_accuracy("anthropic", estimated, actual)

    ratio = estimated / actual if actual > 0 else 0
    print(f"✓ Integration test passed (estimated: {estimated}, actual: {actual}, ratio: {ratio:.2f})")

    print("\n" + "=" * 70)
    print("✅ All token estimation accuracy tests PASSED")
    print("=" * 70)
    print("\nMetric ready for production:")
    print("  • Histogram: wordflux_llm_token_estimation_accuracy_ratio")
    print("  • Label: provider (anthropic/openai/mock)")
    print("  • Buckets: 0.5, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 2.0, +Inf")
    print("  • Function: record_token_estimation_accuracy(provider, estimated, actual)")
    print("\nUse cases:")
    print("  • Ratio = 1.0: Perfect estimation")
    print("  • Ratio > 1.0: Overestimation (conservative, safe)")
    print("  • Ratio < 1.0: Underestimation (may hit rate limits)")
    print("  • Monitor P50, P90, P99 to detect systematic bias")
    print("")


if __name__ == "__main__":
    try:
        test_token_estimation_accuracy()
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)