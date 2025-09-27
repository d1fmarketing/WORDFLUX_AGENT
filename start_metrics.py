#!/usr/bin/env python3
"""Start standalone metrics server for testing."""
from src.core.metrics import start_metrics_server
import time

if __name__ == "__main__":
    start_metrics_server(9300)
    print("Metrics server running on http://localhost:9300/metrics")
    while True:
        time.sleep(1)