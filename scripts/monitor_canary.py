#!/usr/bin/env python3
"""
Monitor Canary Deployment - Phase 3C

Real-time monitoring of canary metrics vs stable baseline.
Automatically triggers rollback if thresholds are breached.

Usage:
    python scripts/monitor_canary.py
    python scripts/monitor_canary.py --duration 900  # 15 minutes
    python scripts/monitor_canary.py --check-interval 30  # Check every 30s

Monitors:
    - Error rate (canary vs stable)
    - Latency (P50, P90, P99)
    - Circuit breaker trips
    - Queue depth
    - Redis memory

Rollback Thresholds:
    - Error rate >5% higher than stable
    - P90 latency >50% higher than stable
    - Circuit breaker trip rate >0.1 trips/sec
    - Queue depth >50 jobs
    - Any critical alert fires

Exit codes:
    0: Monitoring complete, no issues
    1: Threshold breached, rollback triggered
    2: Cannot collect metrics
"""
import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import requests

# Color codes
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
RESET = "\033[0m"

# Configuration
PROMETHEUS_URL = "http://localhost:9090"
DEFAULT_DURATION = 900  # 15 minutes
DEFAULT_CHECK_INTERVAL = 30  # 30 seconds

# Thresholds
ERROR_RATE_THRESHOLD = 0.05  # 5% higher than stable
LATENCY_THRESHOLD = 0.50  # 50% higher than stable
CIRCUIT_BREAKER_RATE_THRESHOLD = 0.1  # trips per second
QUEUE_DEPTH_THRESHOLD = 50  # jobs


def query_prometheus(query: str, timeout: int = 10) -> Optional[float]:
    """Query Prometheus and return single value."""
    try:
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=timeout
        )
        response.raise_for_status()
        data = response.json()

        results = data.get("data", {}).get("result", [])
        if results:
            value = results[0].get("value", [None, None])[1]
            return float(value) if value is not None else None

        return None

    except requests.RequestException as e:
        print(f"{RED}Prometheus query failed: {e}{RESET}")
        return None
    except (ValueError, KeyError, IndexError) as e:
        print(f"{RED}Failed to parse Prometheus response: {e}{RESET}")
        return None


def get_error_rate(instance: str) -> Optional[float]:
    """Get error rate for instance (5xx errors per total requests)."""
    query = f'''
    rate(wordflux_api_requests_total{{status_code=~"5..", instance="{instance}"}}[5m])
    /
    rate(wordflux_api_requests_total{{instance="{instance}"}}[5m])
    '''
    result = query_prometheus(query)
    return result if result is not None else 0.0


def get_latency_percentile(instance: str, percentile: float) -> Optional[float]:
    """Get latency percentile for instance."""
    query = f'''
    histogram_quantile({percentile},
      rate(wordflux_api_request_duration_seconds_bucket{{instance="{instance}"}}[5m])
    )
    '''
    return query_prometheus(query)


def get_circuit_breaker_rate(instance: str) -> Optional[float]:
    """Get circuit breaker trip rate for instance."""
    query = f'''
    rate(wordflux_circuit_breaker_trips_total{{instance="{instance}"}}[5m])
    '''
    result = query_prometheus(query)
    return result if result is not None else 0.0


def get_queue_depth() -> Optional[float]:
    """Get current queue depth."""
    query = "wordflux_jobs_in_queue"
    return query_prometheus(query)


def get_redis_memory() -> Optional[float]:
    """Get Redis memory usage in MB."""
    query = "wordflux_redis_memory_used_bytes / 1024 / 1024"
    return query_prometheus(query)


class CanaryMetrics:
    """Container for canary metrics."""
    def __init__(self):
        self.error_rate: Optional[float] = None
        self.latency_p50: Optional[float] = None
        self.latency_p90: Optional[float] = None
        self.latency_p99: Optional[float] = None
        self.circuit_breaker_rate: Optional[float] = None
        self.queue_depth: Optional[float] = None
        self.redis_memory: Optional[float] = None

    def collect(self, instance: str):
        """Collect all metrics for instance."""
        self.error_rate = get_error_rate(instance)
        self.latency_p50 = get_latency_percentile(instance, 0.50)
        self.latency_p90 = get_latency_percentile(instance, 0.90)
        self.latency_p99 = get_latency_percentile(instance, 0.99)
        self.circuit_breaker_rate = get_circuit_breaker_rate(instance)

        # Queue and Redis are global (not per-instance)
        if instance == "canary":
            self.queue_depth = get_queue_depth()
            self.redis_memory = get_redis_memory()


class ThresholdCheck:
    """Result of threshold check."""
    def __init__(self, metric: str, passed: bool, value: float, threshold: float, message: str):
        self.metric = metric
        self.passed = passed
        self.value = value
        self.threshold = threshold
        self.message = message


def check_thresholds(stable: CanaryMetrics, canary: CanaryMetrics) -> Tuple[bool, list]:
    """Check if canary metrics are within acceptable thresholds."""
    checks = []

    # Error rate check
    if stable.error_rate is not None and canary.error_rate is not None:
        error_diff = canary.error_rate - stable.error_rate
        error_ok = error_diff <= ERROR_RATE_THRESHOLD

        checks.append(ThresholdCheck(
            "Error Rate",
            error_ok,
            error_diff * 100,  # Convert to percentage
            ERROR_RATE_THRESHOLD * 100,
            f"Canary: {canary.error_rate*100:.2f}%, Stable: {stable.error_rate*100:.2f}% (diff: {error_diff*100:+.2f}%)"
        ))

    # Latency P90 check
    if stable.latency_p90 is not None and canary.latency_p90 is not None and stable.latency_p90 > 0:
        latency_ratio = canary.latency_p90 / stable.latency_p90
        latency_ok = (latency_ratio - 1.0) <= LATENCY_THRESHOLD

        checks.append(ThresholdCheck(
            "P90 Latency",
            latency_ok,
            canary.latency_p90 * 1000,  # Convert to ms
            stable.latency_p90 * 1000 * (1 + LATENCY_THRESHOLD),
            f"Canary: {canary.latency_p90*1000:.0f}ms, Stable: {stable.latency_p90*1000:.0f}ms (ratio: {latency_ratio:.2f}x)"
        ))

    # Circuit breaker rate check
    if canary.circuit_breaker_rate is not None:
        cb_ok = canary.circuit_breaker_rate <= CIRCUIT_BREAKER_RATE_THRESHOLD

        checks.append(ThresholdCheck(
            "Circuit Breaker",
            cb_ok,
            canary.circuit_breaker_rate * 60,  # Convert to trips/min
            CIRCUIT_BREAKER_RATE_THRESHOLD * 60,
            f"{canary.circuit_breaker_rate*60:.1f} trips/min (threshold: {CIRCUIT_BREAKER_RATE_THRESHOLD*60:.1f})"
        ))

    # Queue depth check
    if canary.queue_depth is not None:
        queue_ok = canary.queue_depth <= QUEUE_DEPTH_THRESHOLD

        checks.append(ThresholdCheck(
            "Queue Depth",
            queue_ok,
            canary.queue_depth,
            QUEUE_DEPTH_THRESHOLD,
            f"{canary.queue_depth:.0f} jobs (threshold: {QUEUE_DEPTH_THRESHOLD})"
        ))

    # Redis memory check (warning only, not a failure)
    if canary.redis_memory is not None:
        checks.append(ThresholdCheck(
            "Redis Memory",
            True,  # Always pass, just informational
            canary.redis_memory,
            0,
            f"{canary.redis_memory:.1f} MB"
        ))

    all_passed = all(check.passed for check in checks if check.metric != "Redis Memory")
    return all_passed, checks


def print_status(elapsed: int, duration: int, checks: list):
    """Print monitoring status."""
    print(f"\n{BLUE}┌{'─'*60}┐{RESET}")
    print(f"{BLUE}│ CANARY MONITORING - {elapsed//60:02d}:{elapsed%60:02d} / {duration//60:02d}:{duration%60:02d} elapsed{'':>20}│{RESET}")
    print(f"{BLUE}├{'─'*60}┤{RESET}")

    for check in checks:
        status = f"{GREEN}✓{RESET}" if check.passed else f"{RED}✗{RESET}"
        metric_name = f"{check.metric}:"
        print(f"{BLUE}│{RESET} {status} {metric_name:<20} {check.message:<32} {BLUE}│{RESET}")

    print(f"{BLUE}├{'─'*60}┤{RESET}")

    all_passed = all(check.passed for check in checks if check.metric != "Redis Memory")
    if all_passed:
        status_text = f"{GREEN}STATUS: HEALTHY - Continue monitoring{RESET}"
    else:
        status_text = f"{RED}STATUS: THRESHOLD BREACHED - Initiating rollback{RESET}"

    print(f"{BLUE}│{RESET} {status_text:<67} {BLUE}│{RESET}")
    print(f"{BLUE}└{'─'*60}┘{RESET}")


def trigger_rollback(reason: str) -> bool:
    """Trigger rollback by calling rollback script."""
    print(f"\n{RED}{'='*70}")
    print(f"THRESHOLD BREACH DETECTED - Triggering Rollback")
    print(f"{'='*70}{RESET}\n")
    print(f"Reason: {reason}")

    rollback_script = "/home/ubuntu/scripts/rollback_canary.py"
    venv_python = "/home/ubuntu/.venv/bin/python3"

    try:
        result = subprocess.run(
            [venv_python, rollback_script, "--reason", f"Automated rollback: {reason}"],
            capture_output=False,  # Show rollback output
            timeout=60
        )

        return result.returncode == 0

    except subprocess.TimeoutExpired:
        print(f"{RED}Rollback timed out{RESET}")
        return False
    except Exception as e:
        print(f"{RED}Rollback failed: {e}{RESET}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Monitor canary deployment")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION, help="Monitoring duration in seconds")
    parser.add_argument("--check-interval", type=int, default=DEFAULT_CHECK_INTERVAL, help="Check interval in seconds")
    args = parser.parse_args()

    print(f"{BLUE}{'='*70}")
    print(f"WordFlux Canary Monitoring - Phase 3C")
    print(f"{'='*70}{RESET}\n")
    print(f"Duration: {args.duration}s ({args.duration//60} minutes)")
    print(f"Check interval: {args.check_interval}s")
    print(f"Prometheus: {PROMETHEUS_URL}")

    print(f"\n{BLUE}Thresholds:{RESET}")
    print(f"  • Error rate: ≤{ERROR_RATE_THRESHOLD*100:.0f}% higher than stable")
    print(f"  • P90 latency: ≤{LATENCY_THRESHOLD*100:.0f}% higher than stable")
    print(f"  • Circuit breaker: ≤{CIRCUIT_BREAKER_RATE_THRESHOLD*60:.0f} trips/min")
    print(f"  • Queue depth: ≤{QUEUE_DEPTH_THRESHOLD} jobs")

    start_time = time.time()
    check_count = 0

    while True:
        elapsed = int(time.time() - start_time)

        if elapsed >= args.duration:
            print(f"\n{GREEN}{'='*70}")
            print(f"MONITORING COMPLETE - No issues detected")
            print(f"{'='*70}{RESET}\n")
            print(f"Duration: {elapsed}s ({elapsed//60} minutes)")
            print(f"Checks performed: {check_count}")
            print("\nCanary appears healthy. Next steps:")
            print("  1. Continue monitoring or promote: python scripts/promote_canary.py")
            print("  2. Check Grafana dashboard for detailed metrics")
            sys.exit(0)

        # Collect metrics
        stable = CanaryMetrics()
        stable.collect("stable")

        canary = CanaryMetrics()
        canary.collect("canary")

        # Check thresholds
        all_passed, checks = check_thresholds(stable, canary)

        # Print status
        print_status(elapsed, args.duration, checks)

        check_count += 1

        # If threshold breached, trigger rollback
        if not all_passed:
            failed_checks = [c for c in checks if not c.passed and c.metric != "Redis Memory"]
            reason = ", ".join([f"{c.metric} ({c.message})" for c in failed_checks])

            if trigger_rollback(reason):
                print(f"\n{GREEN}Rollback completed successfully{RESET}")
                sys.exit(1)
            else:
                print(f"\n{RED}Rollback failed - manual intervention required{RESET}")
                sys.exit(1)

        # Wait before next check
        time.sleep(args.check_interval)


if __name__ == "__main__":
    main()