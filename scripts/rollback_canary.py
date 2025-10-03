#!/usr/bin/env python3
"""
Rollback Canary Deployment - Phase 3A

Emergency rollback of canary deployment to 100% stable traffic.
Restores nginx configuration and stops canary service.

Usage:
    python scripts/rollback_canary.py [--reason "error description"]
    python scripts/rollback_canary.py --dry-run  # Test without changes

Exit codes:
    0: Rollback successful
    1: Rollback failed
    2: Already rolled back (no canary active)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

# Color codes
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
RESET = "\033[0m"

# Configuration
NGINX_CONFIG_PATH = "/etc/nginx/sites-available/wordflux-api"
NGINX_CONFIG_BACKUP = "/etc/nginx/sites-available/wordflux-api.backup"
CANARY_SERVICE_NAME = "wordflux-api-canary"
ROLLBACK_LOG = "/var/log/wordflux-canary-rollback.log"
SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL", "")


def log_message(message: str, level: str = "INFO"):
    """Log message to file and stdout."""
    timestamp = datetime.now(timezone.utc).isoformat()
    log_entry = f"[{timestamp}] [{level}] {message}"

    print(log_entry)

    try:
        with open(ROLLBACK_LOG, "a") as f:
            f.write(log_entry + "\n")
    except Exception as e:
        print(f"{YELLOW}Warning: Could not write to log file: {e}{RESET}")


def check_backup_exists() -> bool:
    """Check if nginx config backup exists."""
    if not Path(NGINX_CONFIG_BACKUP).exists():
        log_message(f"Backup config not found: {NGINX_CONFIG_BACKUP}", "ERROR")
        return False

    log_message(f"Backup config found: {NGINX_CONFIG_BACKUP}", "INFO")
    return True


def validate_backup_config() -> Tuple[bool, str]:
    """Validate backup nginx config is readable and has content."""
    try:
        # Check if file is readable
        with open(NGINX_CONFIG_BACKUP, 'r') as f:
            content = f.read()

        if len(content) < 100:  # Sanity check - config should be reasonable size
            log_message(f"Backup config suspiciously small: {len(content)} bytes", "ERROR")
            return False, "Config file too small"

        # Check for key nginx directives
        if 'server' not in content:
            log_message("Backup config missing 'server' directive", "ERROR")
            return False, "Invalid nginx config structure"

        log_message(f"Backup config validated ({len(content)} bytes)", "INFO")
        return True, ""

    except FileNotFoundError:
        log_message(f"Cannot read backup file: {NGINX_CONFIG_BACKUP}", "ERROR")
        return False, "File not readable"
    except PermissionError:
        log_message(f"Permission denied reading backup: {NGINX_CONFIG_BACKUP}", "ERROR")
        return False, "Permission denied"
    except Exception as e:
        log_message(f"Config validation error: {e}", "ERROR")
        return False, str(e)


def restore_nginx_config(dry_run: bool = False) -> bool:
    """Restore nginx config from backup."""
    log_message(f"Restoring nginx config from backup...", "INFO")

    if dry_run:
        log_message("[DRY RUN] Would copy backup to active config", "INFO")
        return True

    try:
        # Copy backup to active config
        subprocess.run(
            ["sudo", "cp", NGINX_CONFIG_BACKUP, NGINX_CONFIG_PATH],
            check=True,
            capture_output=True,
            timeout=10
        )

        log_message(f"Config restored: {NGINX_CONFIG_PATH}", "INFO")
        return True

    except subprocess.CalledProcessError as e:
        log_message(f"Failed to restore config: {e.stderr.decode() if e.stderr else str(e)}", "ERROR")
        return False
    except Exception as e:
        log_message(f"Config restoration error: {e}", "ERROR")
        return False


def reload_nginx(dry_run: bool = False) -> bool:
    """Reload nginx to apply config changes."""
    log_message("Reloading nginx...", "INFO")

    if dry_run:
        log_message("[DRY RUN] Would reload nginx", "INFO")
        return True

    try:
        # Test config before reload
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
        result = subprocess.run(
            ["sudo", "systemctl", "reload", "nginx"],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            log_message("Nginx reloaded successfully", "INFO")
            return True
        else:
            log_message(f"Nginx reload failed: {result.stderr}", "ERROR")
            return False

    except subprocess.TimeoutExpired:
        log_message("Nginx reload timed out", "ERROR")
        return False
    except Exception as e:
        log_message(f"Nginx reload error: {e}", "ERROR")
        return False


def stop_canary_service(dry_run: bool = False) -> bool:
    """Stop canary service."""
    log_message(f"Stopping {CANARY_SERVICE_NAME} service...", "INFO")

    if dry_run:
        log_message(f"[DRY RUN] Would stop {CANARY_SERVICE_NAME}", "INFO")
        return True

    try:
        # Check if service exists and is active
        result = subprocess.run(
            ["systemctl", "is-active", CANARY_SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.stdout.strip() != "active":
            log_message(f"{CANARY_SERVICE_NAME} is not active, skipping stop", "INFO")
            return True

        # Stop the service
        result = subprocess.run(
            ["sudo", "systemctl", "stop", CANARY_SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            log_message(f"{CANARY_SERVICE_NAME} stopped successfully", "INFO")
            return True
        else:
            log_message(f"Failed to stop {CANARY_SERVICE_NAME}: {result.stderr}", "WARNING")
            return False

    except FileNotFoundError:
        log_message("systemctl not found, cannot stop service", "WARNING")
        return False
    except subprocess.TimeoutExpired:
        log_message("Service stop timed out", "ERROR")
        return False
    except Exception as e:
        log_message(f"Service stop error: {e}", "WARNING")
        return False


def send_notification(reason: str, success: bool):
    """Send Slack notification about rollback."""
    if not SLACK_WEBHOOK:
        log_message("No Slack webhook configured, skipping notification", "INFO")
        return

    status_emoji = "✅" if success else "❌"
    status_text = "SUCCESSFUL" if success else "FAILED"
    color = "good" if success else "danger"

    message = {
        "attachments": [{
            "color": color,
            "title": f"{status_emoji} Canary Rollback {status_text}",
            "fields": [
                {"title": "Timestamp", "value": datetime.now(timezone.utc).isoformat(), "short": True},
                {"title": "Reason", "value": reason, "short": True},
                {"title": "Action", "value": "Reverted to 100% stable traffic", "short": False}
            ],
            "footer": "WordFlux Deployment System"
        }]
    }

    try:
        import requests
        response = requests.post(SLACK_WEBHOOK, json=message, timeout=5)
        if response.status_code == 200:
            log_message("Slack notification sent", "INFO")
        else:
            log_message(f"Slack notification failed: {response.status_code}", "WARNING")
    except Exception as e:
        log_message(f"Failed to send notification: {e}", "WARNING")


def verify_rollback() -> bool:
    """Verify rollback was successful."""
    log_message("Verifying rollback...", "INFO")

    # Check nginx is running
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "nginx"],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.stdout.strip() != "active":
            log_message("Nginx is not active after rollback", "ERROR")
            return False

        log_message("Nginx is active", "INFO")

    except Exception as e:
        log_message(f"Could not verify nginx status: {e}", "WARNING")

    # Check canary service is stopped
    try:
        result = subprocess.run(
            ["systemctl", "is-active", CANARY_SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.stdout.strip() == "active":
            log_message(f"Warning: {CANARY_SERVICE_NAME} is still active", "WARNING")
        else:
            log_message(f"{CANARY_SERVICE_NAME} is inactive", "INFO")

    except Exception:
        pass  # Service may not exist yet

    return True


def main():
    parser = argparse.ArgumentParser(description="Rollback canary deployment")
    parser.add_argument("--reason", default="Manual rollback", help="Reason for rollback")
    parser.add_argument("--dry-run", action="store_true", help="Test without making changes")
    args = parser.parse_args()

    print(f"{BLUE}{'='*70}")
    print(f"WordFlux Canary Rollback - Phase 3A")
    print(f"{'='*70}{RESET}\n")

    if args.dry_run:
        print(f"{YELLOW}DRY RUN MODE - No changes will be made{RESET}\n")

    log_message(f"Rollback initiated - Reason: {args.reason}", "INFO")

    # Step 1: Check backup exists
    print(f"{BLUE}[1/5] Checking backup config...{RESET}")
    if not check_backup_exists():
        print(f"{RED}✗ Backup config not found{RESET}")
        log_message("Rollback aborted - no backup found", "ERROR")
        sys.exit(1)
    print(f"{GREEN}✓ Backup found{RESET}")

    # Step 2: Validate backup
    print(f"\n{BLUE}[2/5] Validating backup config...{RESET}")
    valid, error = validate_backup_config()
    if not valid:
        print(f"{RED}✗ Backup config validation failed: {error}{RESET}")
        log_message(f"Rollback aborted - invalid backup: {error}", "ERROR")
        sys.exit(1)
    print(f"{GREEN}✓ Backup config is valid{RESET}")

    # Step 3: Restore config
    print(f"\n{BLUE}[3/5] Restoring nginx config...{RESET}")
    if not restore_nginx_config(args.dry_run):
        print(f"{RED}✗ Failed to restore config{RESET}")
        log_message("Rollback failed at config restoration", "ERROR")
        send_notification(args.reason, False)
        sys.exit(1)
    print(f"{GREEN}✓ Config restored{RESET}")

    # Step 4: Reload nginx
    print(f"\n{BLUE}[4/5] Reloading nginx...{RESET}")
    if not reload_nginx(args.dry_run):
        print(f"{RED}✗ Failed to reload nginx{RESET}")
        log_message("Rollback failed at nginx reload", "ERROR")
        send_notification(args.reason, False)
        sys.exit(1)
    print(f"{GREEN}✓ Nginx reloaded{RESET}")

    # Step 5: Stop canary service
    print(f"\n{BLUE}[5/5] Stopping canary service...{RESET}")
    if not stop_canary_service(args.dry_run):
        print(f"{YELLOW}⚠ Could not stop canary service (may not exist yet){RESET}")
    else:
        print(f"{GREEN}✓ Canary service stopped{RESET}")

    # Verify
    if not args.dry_run:
        print(f"\n{BLUE}Verifying rollback...{RESET}")
        if verify_rollback():
            print(f"{GREEN}✓ Rollback verification passed{RESET}")
        else:
            print(f"{YELLOW}⚠ Rollback verification had warnings{RESET}")

    # Summary
    print(f"\n{GREEN}{'='*70}")
    if args.dry_run:
        print("DRY RUN COMPLETE - No changes made")
    else:
        print("ROLLBACK COMPLETE")
    print(f"{'='*70}{RESET}\n")

    log_message(f"Rollback completed successfully - Reason: {args.reason}", "INFO")

    if not args.dry_run:
        send_notification(args.reason, True)

    print("Traffic is now 100% on stable instance")
    print(f"Logs: {ROLLBACK_LOG}")

    sys.exit(0)


if __name__ == "__main__":
    main()