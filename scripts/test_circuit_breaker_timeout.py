#!/usr/bin/env python3
"""
Test script to validate circuit breaker timeout behavior.

Tests:
1. Circuit breaker fails open on Redis timeout
2. Timeout occurs within 1-2 seconds (not hanging forever)
"""
import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_circuit_breaker_timeout():
    """Test circuit breaker handles Redis timeout gracefully."""
    print("=" * 70)
    print("TEST: Circuit Breaker Redis Timeout")
    print("=" * 70)

    # Temporarily point to unreachable Redis
    original_redis_url = os.getenv("REDIS_URL")
    os.environ["REDIS_URL"] = "redis://192.0.2.1:6379/0"  # RFC 5737 TEST-NET-1 (unreachable)

    try:
        from src.core.llm_client import circuit_tripped

        # Test 1: Should fail open (return False) on timeout
        print("\nTest 1: Circuit breaker fails open on timeout...")
        start = time.time()
        result = circuit_tripped("anthropic")
        duration = time.time() - start

        assert result is False, f"Expected False (fail open), got {result}"
        print(f"✓ Returns False (fail open)")

        # Test 2: Should timeout quickly (< 2 seconds)
        assert duration < 2.0, f"Timeout took {duration:.2f}s, expected < 2s"
        print(f"✓ Timed out in {duration:.2f}s (< 2s)")

        # Test 3: Multiple calls should not accumulate delay
        print("\nTest 2: Multiple timeouts don't accumulate...")
        start = time.time()
        for i in range(5):
            circuit_tripped("anthropic")
        duration = time.time() - start

        assert duration < 10.0, f"5 calls took {duration:.2f}s, expected < 10s"
        print(f"✓ 5 calls completed in {duration:.2f}s (avg {duration/5:.2f}s each)")

        print("\n" + "=" * 70)
        print("✅ All circuit breaker timeout tests PASSED")
        print("=" * 70)

    finally:
        # Restore original Redis URL
        if original_redis_url:
            os.environ["REDIS_URL"] = original_redis_url
        else:
            os.environ.pop("REDIS_URL", None)


if __name__ == "__main__":
    try:
        test_circuit_breaker_timeout()
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)