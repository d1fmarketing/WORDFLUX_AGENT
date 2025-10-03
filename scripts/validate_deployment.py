#!/usr/bin/env python3
"""
Deployment Validation Script - Phase 3 Foundation

Pre-flight checks before any deployment to production.
Validates infrastructure health, configuration, and readiness.

Usage:
    python scripts/validate_deployment.py
    python scripts/validate_deployment.py --strict  # Fail on warnings

Exit codes:
    0: All checks passed
    1: Critical failures detected
    2: Warnings detected (strict mode only)
"""
import argparse
import json
import os
import sys
import time
from typing import Dict, List, Tuple
import requests

# Color codes for output
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
RESET = "\033[0m"

# Configuration
API_URL = os.getenv("API_URL", "http://localhost:8080")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
TIMEOUT = 5  # seconds


class ValidationResult:
    """Track validation results."""
    def __init__(self):
        self.passed = []
        self.warnings = []
        self.failures = []

    def add_pass(self, check: str, message: str = ""):
        self.passed.append((check, message))

    def add_warning(self, check: str, message: str):
        self.warnings.append((check, message))

    def add_failure(self, check: str, message: str):
        self.failures.append((check, message))

    def has_failures(self) -> bool:
        return len(self.failures) > 0

    def has_warnings(self) -> bool:
        return len(self.warnings) > 0

    def print_summary(self):
        print(f"\n{'='*70}")
        print(f"{BLUE}DEPLOYMENT VALIDATION SUMMARY{RESET}")
        print(f"{'='*70}")

        if self.passed:
            print(f"\n{GREEN}✓ PASSED ({len(self.passed)} checks):{RESET}")
            for check, msg in self.passed:
                if msg:
                    print(f"  • {check}: {msg}")
                else:
                    print(f"  • {check}")

        if self.warnings:
            print(f"\n{YELLOW}⚠ WARNINGS ({len(self.warnings)}):{RESET}")
            for check, msg in self.warnings:
                print(f"  • {check}: {msg}")

        if self.failures:
            print(f"\n{RED}✗ FAILURES ({len(self.failures)}):{RESET}")
            for check, msg in self.failures:
                print(f"  • {check}: {msg}")

        print(f"\n{'='*70}")

        if self.has_failures():
            print(f"{RED}VALIDATION FAILED - Cannot proceed with deployment{RESET}")
            return 1
        elif self.has_warnings():
            print(f"{YELLOW}VALIDATION PASSED WITH WARNINGS - Review before deploying{RESET}")
            return 2
        else:
            print(f"{GREEN}VALIDATION PASSED - Ready for deployment{RESET}")
            return 0


def check_api_health(result: ValidationResult):
    """Check API service health."""
    print(f"\n{BLUE}[1/8] Checking API Health...{RESET}")

    try:
        response = requests.get(f"{API_URL}/health", timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()

        if data.get("status") == "healthy":
            result.add_pass("API Health", f"Service healthy at {API_URL}")
        else:
            result.add_failure("API Health", f"Service unhealthy: {data}")
            return

        # Check Redis connectivity
        if data.get("redis") == "connected":
            result.add_pass("Redis Connectivity", "Connected via API")
        else:
            result.add_failure("Redis Connectivity", f"Redis status: {data.get('redis')}")

        # Check queue mode
        queue_mode = data.get("queue_mode", "unknown")
        if queue_mode == "redis":
            result.add_pass("Queue Mode", "Using Redis (production mode)")
        elif queue_mode == "memory":
            result.add_warning("Queue Mode", "Using memory mode (not production-ready)")
        else:
            result.add_failure("Queue Mode", f"Unknown queue mode: {queue_mode}")

    except requests.RequestException as e:
        result.add_failure("API Health", f"Cannot reach API at {API_URL}: {e}")
    except Exception as e:
        result.add_failure("API Health", f"Unexpected error: {e}")


def check_prometheus(result: ValidationResult):
    """Check Prometheus is running and scraping."""
    print(f"\n{BLUE}[2/8] Checking Prometheus...{RESET}")

    try:
        # Check Prometheus health
        response = requests.get(f"{PROMETHEUS_URL}/-/healthy", timeout=TIMEOUT)
        response.raise_for_status()
        result.add_pass("Prometheus Health", f"Running at {PROMETHEUS_URL}")

        # Check targets
        response = requests.get(f"{PROMETHEUS_URL}/api/v1/targets", timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()

        active_targets = data.get("data", {}).get("activeTargets", [])
        healthy_targets = [t for t in active_targets if t.get("health") == "up"]

        if len(healthy_targets) > 0:
            result.add_pass("Prometheus Targets", f"{len(healthy_targets)}/{len(active_targets)} targets healthy")
        else:
            result.add_failure("Prometheus Targets", "No healthy scrape targets found")

    except requests.RequestException as e:
        result.add_failure("Prometheus", f"Cannot reach Prometheus at {PROMETHEUS_URL}: {e}")
    except Exception as e:
        result.add_failure("Prometheus", f"Unexpected error: {e}")


def check_metrics(result: ValidationResult):
    """Check critical metrics are present."""
    print(f"\n{BLUE}[3/8] Checking Critical Metrics...{RESET}")

    critical_metrics = [
        "wordflux_redis_memory_used_bytes",
        "wordflux_redis_memory_max_bytes",
        "wordflux_jobs_in_queue",
        "wordflux_jobs_in_processing"
    ]

    for metric in critical_metrics:
        try:
            response = requests.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": metric},
                timeout=TIMEOUT
            )
            response.raise_for_status()
            data = response.json()

            if data.get("data", {}).get("result"):
                result.add_pass(f"Metric: {metric}", "Present")
            else:
                result.add_warning(f"Metric: {metric}", "No data yet (may be new)")

        except Exception as e:
            result.add_failure(f"Metric: {metric}", f"Query failed: {e}")


def check_alert_rules(result: ValidationResult):
    """Check alert rules are loaded."""
    print(f"\n{BLUE}[4/8] Checking Alert Rules...{RESET}")

    try:
        response = requests.get(f"{PROMETHEUS_URL}/api/v1/rules", timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()

        groups = data.get("data", {}).get("groups", [])
        total_rules = sum(len(g.get("rules", [])) for g in groups)

        if total_rules >= 6:  # We expect 6 rules minimum
            result.add_pass("Alert Rules", f"{total_rules} rules loaded across {len(groups)} groups")
        else:
            result.add_warning("Alert Rules", f"Only {total_rules} rules loaded (expected >= 6)")

    except Exception as e:
        result.add_failure("Alert Rules", f"Cannot check rules: {e}")


def check_redis_config(result: ValidationResult):
    """Check Redis memory configuration."""
    print(f"\n{BLUE}[5/8] Checking Redis Configuration...{RESET}")

    try:
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": "wordflux_redis_memory_max_bytes"},
            timeout=TIMEOUT
        )
        response.raise_for_status()
        data = response.json()

        results = data.get("data", {}).get("result", [])
        if results:
            max_bytes = float(results[0]["value"][1])
            if max_bytes > 0:
                max_mb = max_bytes / 1024 / 1024
                result.add_pass("Redis Memory Limit", f"Configured: {max_mb:.0f} MB")
            else:
                result.add_warning(
                    "Redis Memory Limit",
                    "Not configured (maxmemory=0). Run: sudo bash scripts/configure_redis_memory.sh"
                )
        else:
            result.add_warning("Redis Memory Limit", "Metric not found")

    except Exception as e:
        result.add_warning("Redis Memory Limit", f"Cannot check: {e}")


def check_system_resources(result: ValidationResult):
    """Check system resource availability."""
    print(f"\n{BLUE}[6/8] Checking System Resources...{RESET}")

    try:
        import shutil

        # Check disk space
        stat = shutil.disk_usage("/")
        free_gb = stat.free / (1024**3)
        percent_free = (stat.free / stat.total) * 100

        if percent_free > 20:
            result.add_pass("Disk Space", f"{free_gb:.1f} GB free ({percent_free:.1f}%)")
        elif percent_free > 10:
            result.add_warning("Disk Space", f"Only {free_gb:.1f} GB free ({percent_free:.1f}%)")
        else:
            result.add_failure("Disk Space", f"Low disk space: {free_gb:.1f} GB free ({percent_free:.1f}%)")

    except Exception as e:
        result.add_warning("System Resources", f"Cannot check: {e}")


def check_environment(result: ValidationResult):
    """Check environment configuration."""
    print(f"\n{BLUE}[7/8] Checking Environment Configuration...{RESET}")

    required_vars = {
        "REDIS_URL": "Redis connection string",
        "AWS_REGION": "AWS region for S3"
    }

    for var, description in required_vars.items():
        if os.getenv(var):
            result.add_pass(f"ENV: {var}", "Set")
        else:
            result.add_warning(f"ENV: {var}", f"Not set ({description})")


def check_services(result: ValidationResult):
    """Check systemd services are running."""
    print(f"\n{BLUE}[8/8] Checking Services...{RESET}")

    services = [
        "wordflux-api",
        "wordflux-prometheus",
        "grafana-server"
    ]

    try:
        import subprocess

        for service in services:
            try:
                result_check = subprocess.run(
                    ["systemctl", "is-active", service],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result_check.stdout.strip() == "active":
                    result.add_pass(f"Service: {service}", "Active")
                else:
                    result.add_failure(f"Service: {service}", f"Not active: {result_check.stdout.strip()}")
            except subprocess.TimeoutExpired:
                result.add_warning(f"Service: {service}", "Check timed out")
            except FileNotFoundError:
                result.add_warning(f"Service: {service}", "systemctl not available")
                break

    except Exception as e:
        result.add_warning("Services", f"Cannot check: {e}")


def main():
    parser = argparse.ArgumentParser(description="Validate deployment readiness")
    parser.add_argument("--strict", action="store_true", help="Fail on warnings")
    args = parser.parse_args()

    print(f"{BLUE}{'='*70}")
    print(f"WordFlux Deployment Validation - Phase 3")
    print(f"{'='*70}{RESET}\n")
    print(f"API URL: {API_URL}")
    print(f"Prometheus URL: {PROMETHEUS_URL}")
    print(f"Strict mode: {'ON' if args.strict else 'OFF'}")

    result = ValidationResult()

    # Run all checks
    check_api_health(result)
    check_prometheus(result)
    check_metrics(result)
    check_alert_rules(result)
    check_redis_config(result)
    check_system_resources(result)
    check_environment(result)
    check_services(result)

    # Print summary
    exit_code = result.print_summary()

    # In strict mode, treat warnings as failures
    if args.strict and exit_code == 2:
        print(f"\n{RED}Strict mode: Treating warnings as failures{RESET}")
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()