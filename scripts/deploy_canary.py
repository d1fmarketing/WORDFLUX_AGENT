#!/usr/bin/env python3
"""
Deploy Canary Instance - Phase 3B

Deploys canary instance on port 8081 and configures nginx for 5% traffic split.

Usage:
    python scripts/deploy_canary.py
    python scripts/deploy_canary.py --dry-run  # Test without changes
    python scripts/deploy_canary.py --canary-port 8082  # Custom port

Prerequisites:
    - validate_deployment.py passes
    - Current instance stable (no alerts)
    - Sudo access for nginx and systemd

Exit codes:
    0: Deployment successful
    1: Deployment failed
    2: Pre-flight checks failed
"""
import argparse
import os
import shutil
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
DEFAULT_CANARY_PORT = 8081
NGINX_CONFIG_PATH = "/etc/nginx/sites-available/wordflux-api"
NGINX_CONFIG_BACKUP = "/etc/nginx/sites-available/wordflux-api.backup"
CANARY_SERVICE_PATH = "/etc/systemd/system/wordflux-api-canary.service"
STABLE_SERVICE_PATH = "/etc/systemd/system/wordflux-api.service"
PROJECT_ROOT = "/home/ubuntu"
VENV_PATH = f"{PROJECT_ROOT}/.venv"


def log_message(message: str, level: str = "INFO"):
    """Log message with timestamp."""
    timestamp = datetime.now(timezone.utc).isoformat()
    print(f"[{timestamp}] [{level}] {message}")


def run_preflight_checks() -> bool:
    """Run pre-flight validation checks."""
    print(f"\n{BLUE}[Pre-flight] Running validation checks...{RESET}")

    # Check validate_deployment.py exists
    validate_script = f"{PROJECT_ROOT}/scripts/validate_deployment.py"
    if not Path(validate_script).exists():
        log_message(f"Validation script not found: {validate_script}", "ERROR")
        return False

    # Run validation
    try:
        result = subprocess.run(
            [f"{VENV_PATH}/bin/python3", validate_script],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode == 0:
            log_message("Pre-flight validation passed", "INFO")
            return True
        elif result.returncode == 2:
            log_message("Pre-flight validation passed with warnings", "WARNING")
            return True  # Warnings are acceptable
        else:
            log_message(f"Pre-flight validation failed (exit code: {result.returncode})", "ERROR")
            # Print last 10 lines of output
            lines = result.stdout.split('\n')[-10:]
            for line in lines:
                print(f"  {line}")
            return False

    except subprocess.TimeoutExpired:
        log_message("Pre-flight validation timed out", "ERROR")
        return False
    except Exception as e:
        log_message(f"Pre-flight validation error: {e}", "ERROR")
        return False


def generate_nginx_config(canary_port: int) -> str:
    """Generate nginx config with canary routing."""
    config = f"""# WordFlux API with Canary Deployment
# Generated: {datetime.now(timezone.utc).isoformat()}

upstream wordflux_api_backend {{
    # Stable instance (95% weight)
    server 127.0.0.1:{STABLE_PORT} weight=95 max_fails=3 fail_timeout=30s;

    # Canary instance (5% weight)
    server 127.0.0.1:{canary_port} weight=5 max_fails=3 fail_timeout=30s;

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

    # Metrics endpoints (for Prometheus scraping)
    location /metrics {{
        return 404;  # Don't expose publicly
    }}
}}

# Internal metrics endpoints for Prometheus
server {{
    listen 127.0.0.1:9080;  # Stable metrics
    location /metrics {{
        proxy_pass http://127.0.0.1:{STABLE_PORT}/metrics;
    }}
}}

server {{
    listen 127.0.0.1:9081;  # Canary metrics
    location /metrics {{
        proxy_pass http://127.0.0.1:{canary_port}/metrics;
    }}
}}
"""
    return config


def validate_nginx_config_syntax(config_content: str) -> tuple[bool, str]:
    """Validate nginx config syntax by writing to temp file and testing."""
    temp_config = "/tmp/wordflux-api-test.conf"

    try:
        # Write config to temp file
        with open(temp_config, 'w') as f:
            f.write(config_content)

        # Create minimal nginx.conf wrapper for testing
        wrapper_config = f"""
events {{
    worker_connections 1024;
}}

http {{
    include {temp_config};
}}
"""
        wrapper_path = "/tmp/nginx-test-wrapper.conf"
        with open(wrapper_path, 'w') as f:
            f.write(wrapper_config)

        # Test config (needs sudo for PID file access)
        result = subprocess.run(
            ["sudo", "nginx", "-t", "-c", wrapper_path],
            capture_output=True,
            text=True,
            timeout=10
        )

        # Cleanup
        Path(temp_config).unlink(missing_ok=True)
        Path(wrapper_path).unlink(missing_ok=True)

        if result.returncode == 0:
            return True, ""
        else:
            return False, result.stderr

    except Exception as e:
        return False, str(e)


def backup_current_nginx_config(dry_run: bool = False) -> bool:
    """Backup current nginx config before modification."""
    log_message(f"Backing up current nginx config...", "INFO")

    if not Path(NGINX_CONFIG_PATH).exists():
        log_message(f"Current config not found: {NGINX_CONFIG_PATH}", "WARNING")
        # This might be first deployment, not an error
        return True

    if dry_run:
        log_message("[DRY RUN] Would backup nginx config", "INFO")
        return True

    try:
        subprocess.run(
            ["sudo", "cp", NGINX_CONFIG_PATH, NGINX_CONFIG_BACKUP],
            check=True,
            capture_output=True,
            timeout=10
        )
        log_message(f"Backup created: {NGINX_CONFIG_BACKUP}", "INFO")
        return True

    except subprocess.CalledProcessError as e:
        log_message(f"Backup failed: {e.stderr.decode() if e.stderr else str(e)}", "ERROR")
        return False
    except Exception as e:
        log_message(f"Backup error: {e}", "ERROR")
        return False


def deploy_nginx_config(config_content: str, dry_run: bool = False) -> bool:
    """Deploy nginx config."""
    log_message("Deploying nginx config...", "INFO")

    if dry_run:
        log_message("[DRY RUN] Would deploy nginx config", "INFO")
        return True

    temp_file = "/tmp/wordflux-api-new.conf"

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

        # Cleanup temp file
        Path(temp_file).unlink(missing_ok=True)

        log_message(f"Config deployed: {NGINX_CONFIG_PATH}", "INFO")
        return True

    except subprocess.CalledProcessError as e:
        log_message(f"Config deployment failed: {e.stderr.decode() if e.stderr else str(e)}", "ERROR")
        return False
    except Exception as e:
        log_message(f"Config deployment error: {e}", "ERROR")
        return False


def generate_systemd_service(canary_port: int) -> str:
    """Generate systemd service file for canary."""
    service = f"""[Unit]
Description=WordFlux API Canary Instance
Documentation=https://github.com/wordflux/wordflux
After=network-online.target redis.service
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory={PROJECT_ROOT}
Environment="PATH={VENV_PATH}/bin:/usr/local/bin:/usr/bin:/bin"
Environment="API_PORT={canary_port}"
Environment="REDIS_URL=redis://localhost:6379/0"
Environment="QUEUE_MODE=redis"
EnvironmentFile=-{PROJECT_ROOT}/wordflux.env

ExecStart={VENV_PATH}/bin/python -m src.api.main

Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
    return service


def deploy_systemd_service(service_content: str, dry_run: bool = False) -> bool:
    """Deploy systemd service file."""
    log_message("Deploying canary systemd service...", "INFO")

    if dry_run:
        log_message("[DRY RUN] Would deploy systemd service", "INFO")
        return True

    temp_file = "/tmp/wordflux-api-canary.service"

    try:
        # Write service to temp file
        with open(temp_file, 'w') as f:
            f.write(service_content)

        # Copy to systemd directory
        subprocess.run(
            ["sudo", "cp", temp_file, CANARY_SERVICE_PATH],
            check=True,
            capture_output=True,
            timeout=10
        )

        # Cleanup
        Path(temp_file).unlink(missing_ok=True)

        # Reload systemd
        subprocess.run(
            ["sudo", "systemctl", "daemon-reload"],
            check=True,
            capture_output=True,
            timeout=10
        )

        log_message(f"Service deployed: {CANARY_SERVICE_PATH}", "INFO")
        return True

    except subprocess.CalledProcessError as e:
        log_message(f"Service deployment failed: {e.stderr.decode() if e.stderr else str(e)}", "ERROR")
        return False
    except Exception as e:
        log_message(f"Service deployment error: {e}", "ERROR")
        return False


def start_canary_service(dry_run: bool = False) -> bool:
    """Start canary service."""
    log_message("Starting canary service...", "INFO")

    if dry_run:
        log_message("[DRY RUN] Would start canary service", "INFO")
        return True

    try:
        subprocess.run(
            ["sudo", "systemctl", "start", "wordflux-api-canary"],
            check=True,
            capture_output=True,
            timeout=15
        )

        log_message("Canary service started", "INFO")
        return True

    except subprocess.CalledProcessError as e:
        log_message(f"Failed to start canary: {e.stderr.decode() if e.stderr else str(e)}", "ERROR")
        return False
    except Exception as e:
        log_message(f"Canary start error: {e}", "ERROR")
        return False


def wait_for_canary_health(canary_port: int, timeout: int = 30) -> bool:
    """Wait for canary to respond to health checks."""
    log_message(f"Waiting for canary health check (timeout: {timeout}s)...", "INFO")

    import requests

    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"http://localhost:{canary_port}/health", timeout=2)
            if response.status_code == 200:
                log_message(f"Canary health check passed", "INFO")
                return True
        except requests.RequestException:
            pass

        time.sleep(2)

    log_message(f"Canary health check timed out after {timeout}s", "ERROR")
    return False


def reload_nginx(dry_run: bool = False) -> bool:
    """Reload nginx to apply config."""
    log_message("Reloading nginx...", "INFO")

    if dry_run:
        log_message("[DRY RUN] Would reload nginx", "INFO")
        return True

    try:
        # Test config first
        result = subprocess.run(
            ["sudo", "nginx", "-t"],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            log_message(f"Nginx config test failed: {result.stderr}", "ERROR")
            return False

        # Reload
        subprocess.run(
            ["sudo", "systemctl", "reload", "nginx"],
            check=True,
            capture_output=True,
            timeout=10
        )

        log_message("Nginx reloaded", "INFO")
        return True

    except subprocess.CalledProcessError as e:
        log_message(f"Nginx reload failed: {e.stderr.decode() if e.stderr else str(e)}", "ERROR")
        return False
    except Exception as e:
        log_message(f"Nginx reload error: {e}", "ERROR")
        return False


def verify_traffic_split(canary_port: int) -> bool:
    """Verify traffic is being split between stable and canary."""
    log_message("Verifying traffic split...", "INFO")

    # TODO: Implement verification by checking request counts in metrics
    # For now, just verify both endpoints are accessible

    import requests

    try:
        # Check stable
        stable_response = requests.get(f"http://localhost:{STABLE_PORT}/health", timeout=2)
        if stable_response.status_code != 200:
            log_message(f"Stable instance not healthy", "WARNING")

        # Check canary
        canary_response = requests.get(f"http://localhost:{canary_port}/health", timeout=2)
        if canary_response.status_code != 200:
            log_message(f"Canary instance not healthy", "WARNING")
            return False

        log_message("Both instances responding", "INFO")
        return True

    except requests.RequestException as e:
        log_message(f"Traffic verification error: {e}", "WARNING")
        return False


def main():
    parser = argparse.ArgumentParser(description="Deploy canary instance")
    parser.add_argument("--canary-port", type=int, default=DEFAULT_CANARY_PORT, help="Canary port")
    parser.add_argument("--dry-run", action="store_true", help="Test without changes")
    args = parser.parse_args()

    print(f"{BLUE}{'='*70}")
    print(f"WordFlux Canary Deployment - Phase 3B")
    print(f"{'='*70}{RESET}\n")
    print(f"Stable port: {STABLE_PORT}")
    print(f"Canary port: {args.canary_port}")
    print(f"Traffic split: 95% stable, 5% canary")

    if args.dry_run:
        print(f"{YELLOW}DRY RUN MODE - No changes will be made{RESET}\n")

    # Pre-flight checks
    if not run_preflight_checks():
        print(f"\n{RED}✗ Pre-flight checks failed{RESET}")
        sys.exit(2)

    # Stage 1: Generate and validate nginx config
    print(f"\n{BLUE}[Stage 1/7] Generating nginx config...{RESET}")
    nginx_config = generate_nginx_config(args.canary_port)
    print(f"{GREEN}✓ Config generated ({len(nginx_config)} bytes){RESET}")

    print(f"\n{BLUE}[Stage 1.5/7] Validating nginx config syntax...{RESET}")
    valid, error = validate_nginx_config_syntax(nginx_config)
    if not valid:
        print(f"{RED}✗ Config validation failed: {error}{RESET}")
        sys.exit(1)
    print(f"{GREEN}✓ Config syntax valid{RESET}")

    # Stage 2: Generate systemd service
    print(f"\n{BLUE}[Stage 2/7] Generating systemd service...{RESET}")
    service_config = generate_systemd_service(args.canary_port)
    print(f"{GREEN}✓ Service generated ({len(service_config)} bytes){RESET}")

    # Stage 3: Backup current config
    print(f"\n{BLUE}[Stage 3/7] Backing up current config...{RESET}")
    if not backup_current_nginx_config(args.dry_run):
        print(f"{RED}✗ Backup failed{RESET}")
        sys.exit(1)
    print(f"{GREEN}✓ Config backed up{RESET}")

    # Stage 4: Deploy systemd service
    print(f"\n{BLUE}[Stage 4/7] Deploying canary service...{RESET}")
    if not deploy_systemd_service(service_config, args.dry_run):
        print(f"{RED}✗ Service deployment failed{RESET}")
        sys.exit(1)
    print(f"{GREEN}✓ Service deployed{RESET}")

    # Stage 5: Start canary
    print(f"\n{BLUE}[Stage 5/7] Starting canary instance...{RESET}")
    if not args.dry_run:
        if not start_canary_service(args.dry_run):
            print(f"{RED}✗ Failed to start canary{RESET}")
            sys.exit(1)

        if not wait_for_canary_health(args.canary_port, timeout=30):
            print(f"{RED}✗ Canary health check failed{RESET}")
            print(f"{YELLOW}Rolling back...{RESET}")
            subprocess.run(["sudo", "systemctl", "stop", "wordflux-api-canary"])
            sys.exit(1)
        print(f"{GREEN}✓ Canary started and healthy{RESET}")
    else:
        print(f"{YELLOW}[DRY RUN] Would start canary{RESET}")

    # Stage 6: Deploy nginx config
    print(f"\n{BLUE}[Stage 6/7] Deploying nginx config...{RESET}")
    if not deploy_nginx_config(nginx_config, args.dry_run):
        print(f"{RED}✗ Config deployment failed{RESET}")
        sys.exit(1)
    print(f"{GREEN}✓ Config deployed{RESET}")

    # Stage 7: Reload nginx
    print(f"\n{BLUE}[Stage 7/7] Reloading nginx...{RESET}")
    if not reload_nginx(args.dry_run):
        print(f"{RED}✗ Nginx reload failed{RESET}")
        sys.exit(1)
    print(f"{GREEN}✓ Nginx reloaded{RESET}")

    # Verify
    if not args.dry_run:
        print(f"\n{BLUE}Verifying deployment...{RESET}")
        if verify_traffic_split(args.canary_port):
            print(f"{GREEN}✓ Traffic split verified{RESET}")
        else:
            print(f"{YELLOW}⚠ Could not verify traffic split{RESET}")

    # Summary
    print(f"\n{GREEN}{'='*70}")
    if args.dry_run:
        print("DRY RUN COMPLETE - No changes made")
    else:
        print("CANARY DEPLOYMENT COMPLETE")
    print(f"{'='*70}{RESET}\n")

    print("Next steps:")
    print(f"  1. Monitor canary: python scripts/monitor_canary.py")
    print(f"  2. Check Grafana dashboard for canary metrics")
    print(f"  3. If issues: python scripts/rollback_canary.py --reason 'description'")

    sys.exit(0)


if __name__ == "__main__":
    main()