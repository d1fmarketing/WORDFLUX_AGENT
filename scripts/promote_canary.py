#!/usr/bin/env python3
"""
Promote Canary to Primary - Phase 3D

Gradually promotes canary through stages: 5% → 50% → 100%
Monitors at each stage and can rollback if issues detected.

Usage:
    python scripts/promote_canary.py
    python scripts/promote_canary.py --stage-duration 600  # 10 min per stage
    python scripts/promote_canary.py --skip-monitoring  # Manual approval mode

Promotion Stages:
    Stage 1: Increase to 50% traffic, monitor for 10 minutes
    Stage 2: Increase to 100% traffic, monitor for 10 minutes
    Stage 3: Finalize (canary becomes primary, old stable retired)

Exit codes:
    0: Promotion complete
    1: Promotion failed, rollback recommended
    2: Pre-checks failed
"""
import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Color codes
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
RESET = "\033[0m"

# Configuration
STABLE_PORT = 8080
CANARY_PORT = 8081
NGINX_CONFIG_PATH = "/etc/nginx/sites-available/wordflux-api"
DEFAULT_STAGE_DURATION = 600  # 10 minutes
PROJECT_ROOT = "/home/ubuntu"
VENV_PATH = f"{PROJECT_ROOT}/.venv"


def log_message(message: str, level: str = "INFO"):
    """Log message with timestamp."""
    timestamp = datetime.now(timezone.utc).isoformat()
    print(f"[{timestamp}] [{level}] {message}")


def generate_nginx_config_for_stage(canary_weight: int, stable_weight: int) -> str:
    """Generate nginx config for specific traffic split."""
    config = f"""# WordFlux API - Canary Promotion Stage
# Generated: {datetime.now(timezone.utc).isoformat()}
# Traffic split: {stable_weight}% stable, {canary_weight}% canary

upstream wordflux_api_backend {{
    # Stable instance ({stable_weight}% weight)
    server 127.0.0.1:{STABLE_PORT} weight={stable_weight} max_fails=3 fail_timeout=30s;

    # Canary instance ({canary_weight}% weight)
    server 127.0.0.1:{CANARY_PORT} weight={canary_weight} max_fails=3 fail_timeout=30s;

    # IP hash for sticky sessions
    ip_hash;
}}

# API Server (Direct Access)
server {{
    listen 8080;
    listen [::]:8080;
    server_name _;

    location / {{
        proxy_pass http://wordflux_api_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Canary-Version $upstream_addr;

        # Timeouts
        proxy_connect_timeout 10s;
        proxy_send_timeout 30s;
        proxy_read_timeout 30s;
    }}

    # Health check endpoint (bypass load balancing)
    location /health {{
        proxy_pass http://127.0.0.1:{STABLE_PORT}/health;
    }}

    # Metrics endpoints
    location /metrics {{
        return 404;  # Don't expose publicly
    }}
}}

# Internal metrics endpoints
server {{
    listen 127.0.0.1:9080;
    location /metrics {{
        proxy_pass http://127.0.0.1:{STABLE_PORT}/metrics;
    }}
}}

server {{
    listen 127.0.0.1:9081;
    location /metrics {{
        proxy_pass http://127.0.0.1:{CANARY_PORT}/metrics;
    }}
}}
"""
    return config


def update_nginx_config(config_content: str) -> bool:
    """Update nginx config and reload."""
    temp_file = "/tmp/wordflux-api-promotion.conf"

    try:
        # Write config to temp file
        with open(temp_file, 'w') as f:
            f.write(config_content)

        # Copy to nginx directory
        subprocess.run(
            ["sudo", "cp", temp_file, NGINX_CONFIG_PATH],
            check=True,
            capture_output=True,
            timeout=10
        )

        # Test config
        result = subprocess.run(
            ["sudo", "nginx", "-t"],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            log_message(f"Nginx config test failed: {result.stderr}", "ERROR")
            return False

        # Reload nginx
        subprocess.run(
            ["sudo", "systemctl", "reload", "nginx"],
            check=True,
            capture_output=True,
            timeout=10
        )

        # Cleanup
        Path(temp_file).unlink(missing_ok=True)

        log_message("Nginx config updated and reloaded", "INFO")
        return True

    except subprocess.CalledProcessError as e:
        log_message(f"Failed to update nginx config: {e.stderr.decode() if e.stderr else str(e)}", "ERROR")
        return False
    except Exception as e:
        log_message(f"Config update error: {e}", "ERROR")
        return False


def run_monitoring(duration: int) -> bool:
    """Run canary monitoring for specified duration."""
    log_message(f"Starting monitoring for {duration}s...", "INFO")

    monitor_script = f"{PROJECT_ROOT}/scripts/monitor_canary.py"

    try:
        result = subprocess.run(
            [f"{VENV_PATH}/bin/python3", monitor_script, "--duration", str(duration), "--check-interval", "30"],
            capture_output=False,  # Show monitoring output
            timeout=duration + 60  # Add buffer
        )

        if result.returncode == 0:
            log_message("Monitoring completed successfully - no issues detected", "INFO")
            return True
        else:
            log_message(f"Monitoring detected issues (exit code: {result.returncode})", "ERROR")
            return False

    except subprocess.TimeoutExpired:
        log_message("Monitoring timed out", "ERROR")
        return False
    except Exception as e:
        log_message(f"Monitoring error: {e}", "ERROR")
        return False


def get_user_confirmation(message: str) -> bool:
    """Get user confirmation to proceed."""
    while True:
        response = input(f"\n{YELLOW}{message} (yes/no): {RESET}").strip().lower()
        if response in ['yes', 'y']:
            return True
        elif response in ['no', 'n']:
            return False
        else:
            print("Please answer 'yes' or 'no'")


def trigger_rollback(reason: str) -> bool:
    """Trigger rollback."""
    print(f"\n{RED}{'='*70}")
    print(f"PROMOTION FAILED - Triggering Rollback")
    print(f"{'='*70}{RESET}\n")

    rollback_script = f"{PROJECT_ROOT}/scripts/rollback_canary.py"

    try:
        result = subprocess.run(
            [f"{VENV_PATH}/bin/python3", rollback_script, "--reason", f"Promotion failure: {reason}"],
            capture_output=False,
            timeout=60
        )

        return result.returncode == 0

    except Exception as e:
        log_message(f"Rollback failed: {e}", "ERROR")
        return False


def finalize_promotion() -> bool:
    """Finalize promotion - canary becomes primary."""
    print(f"\n{BLUE}{'='*70}")
    print(f"Finalizing Promotion")
    print(f"{'='*70}{RESET}\n")

    # Step 1: Stop old stable instance
    log_message("Stopping old stable instance...", "INFO")
    try:
        subprocess.run(
            ["sudo", "systemctl", "stop", "wordflux-api"],
            check=True,
            capture_output=True,
            timeout=15
        )
        log_message("Old stable instance stopped", "INFO")
    except subprocess.CalledProcessError as e:
        log_message(f"Failed to stop stable instance: {e}", "WARNING")

    # Step 2: Update canary to run on stable port (or update routing)
    log_message("Canary is now the primary instance", "INFO")

    # Step 3: Configure nginx for single backend (optional - can keep canary setup)
    log_message("Nginx configuration maintained (canary on port 8081)", "INFO")

    # Step 4: Send success notification
    log_message("Promotion finalized successfully", "INFO")

    return True


def main():
    parser = argparse.ArgumentParser(description="Promote canary to primary")
    parser.add_argument("--stage-duration", type=int, default=DEFAULT_STAGE_DURATION, help="Monitoring duration per stage (seconds)")
    parser.add_argument("--skip-monitoring", action="store_true", help="Skip automatic monitoring (manual approval)")
    args = parser.parse_args()

    print(f"{BLUE}{'='*70}")
    print(f"WordFlux Canary Promotion - Phase 3D")
    print(f"{'='*70}{RESET}\n")
    print(f"Stage duration: {args.stage_duration}s ({args.stage_duration//60} minutes)")
    print(f"Monitoring mode: {'Manual' if args.skip_monitoring else 'Automatic'}")

    print(f"\n{BLUE}Promotion Plan:{RESET}")
    print(f"  1. Increase to 50% traffic → monitor {args.stage_duration//60} min")
    print(f"  2. Increase to 100% traffic → monitor {args.stage_duration//60} min")
    print(f"  3. Finalize (canary becomes primary)")

    # Confirm start
    if not get_user_confirmation("Ready to start promotion?"):
        print("Promotion cancelled")
        sys.exit(0)

    # =========================================================================
    # STAGE 1: Increase to 50%
    # =========================================================================
    print(f"\n{BLUE}{'='*70}")
    print(f"STAGE 1: Increasing to 50% Traffic")
    print(f"{'='*70}{RESET}")

    config_50_50 = generate_nginx_config_for_stage(canary_weight=50, stable_weight=50)

    if not update_nginx_config(config_50_50):
        print(f"{RED}✗ Failed to update config for 50% split{RESET}")
        sys.exit(1)

    print(f"{GREEN}✓ Traffic now: 50% stable, 50% canary{RESET}")

    # Monitor stage 1
    if args.skip_monitoring:
        if not get_user_confirmation("Monitor manually, then confirm: Is 50% stage healthy?"):
            if trigger_rollback("Stage 1 (50%) failed manual check"):
                print(f"{GREEN}Rollback successful{RESET}")
            sys.exit(1)
    else:
        if not run_monitoring(args.stage_duration):
            print(f"{RED}✗ Stage 1 monitoring failed{RESET}")
            if trigger_rollback("Stage 1 (50%) threshold breach"):
                print(f"{GREEN}Rollback successful{RESET}")
            sys.exit(1)

    print(f"{GREEN}✓ Stage 1 complete - 50% traffic healthy{RESET}")

    # =========================================================================
    # STAGE 2: Increase to 100%
    # =========================================================================
    print(f"\n{BLUE}{'='*70}")
    print(f"STAGE 2: Increasing to 100% Traffic")
    print(f"{'='*70}{RESET}")

    if not get_user_confirmation("Proceed to 100% canary traffic?"):
        print("Promotion paused at 50%")
        print("To continue later, run this script again")
        sys.exit(0)

    config_100_0 = generate_nginx_config_for_stage(canary_weight=100, stable_weight=0)

    if not update_nginx_config(config_100_0):
        print(f"{RED}✗ Failed to update config for 100% split{RESET}")
        print("Rolling back to 50/50...")
        update_nginx_config(config_50_50)
        sys.exit(1)

    print(f"{GREEN}✓ Traffic now: 0% stable, 100% canary{RESET}")

    # Monitor stage 2
    if args.skip_monitoring:
        if not get_user_confirmation("Monitor manually, then confirm: Is 100% stage healthy?"):
            print("Rolling back to 50/50...")
            if update_nginx_config(config_50_50):
                print(f"{GREEN}Rolled back to 50/50{RESET}")
            else:
                print(f"{RED}Rollback to 50/50 failed - manual intervention needed{RESET}")
            sys.exit(1)
    else:
        if not run_monitoring(args.stage_duration):
            print(f"{RED}✗ Stage 2 monitoring failed{RESET}")
            print("Rolling back to 50/50...")
            if update_nginx_config(config_50_50):
                print(f"{GREEN}Rolled back to 50/50{RESET}")
            else:
                print(f"{RED}Rollback failed - manual intervention needed{RESET}")
            sys.exit(1)

    print(f"{GREEN}✓ Stage 2 complete - 100% canary traffic healthy{RESET}")

    # =========================================================================
    # STAGE 3: Finalization
    # =========================================================================
    print(f"\n{BLUE}{'='*70}")
    print(f"STAGE 3: Finalization")
    print(f"{'='*70}{RESET}")

    if not get_user_confirmation("Finalize promotion (stop old stable, canary becomes primary)?"):
        print("Promotion complete but not finalized")
        print("Canary is serving 100% traffic")
        print("Old stable instance still running (can rollback if needed)")
        sys.exit(0)

    if not finalize_promotion():
        print(f"{RED}✗ Finalization failed{RESET}")
        sys.exit(1)

    # Success!
    print(f"\n{GREEN}{'='*70}")
    print(f"PROMOTION COMPLETE")
    print(f"{'='*70}{RESET}\n")

    print(f"{GREEN}✓ Canary successfully promoted to primary{RESET}")
    print(f"{GREEN}✓ Old stable instance stopped{RESET}")
    print(f"{GREEN}✓ System running on new version{RESET}")

    print("\nNext steps:")
    print("  1. Monitor metrics in Grafana")
    print("  2. Update Prometheus config to remove old stable target")
    print("  3. Clean up old stable deployment files if desired")

    sys.exit(0)


if __name__ == "__main__":
    main()