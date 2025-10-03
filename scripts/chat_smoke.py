#!/usr/bin/env python3
"""
Smoke test for WordFlux Chat API with tool-use integration.

Tests:
1. Resumo do quadro (summarize_board - low risk, executes immediately)
2. Criar card (create_card - low risk, executes immediately)
3. Mover para Finalizado (move_card - high risk, generates approval_token)

Usage:
    python scripts/chat_smoke.py [--host HOST] [--port PORT]

Example:
    python scripts/chat_smoke.py --host localhost --port 8080
"""

import argparse
import json
import sys
import time
import uuid
from typing import Dict, Any, Optional

try:
    import requests
except ImportError:
    print("❌ requests library not installed. Run: pip install requests")
    sys.exit(1)


class Colors:
    """ANSI color codes for terminal output."""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


def print_header(text: str):
    """Print section header."""
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * 70}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{text}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 70}{Colors.RESET}\n")


def print_success(text: str):
    """Print success message."""
    print(f"{Colors.GREEN}✅ {text}{Colors.RESET}")


def print_error(text: str):
    """Print error message."""
    print(f"{Colors.RED}❌ {text}{Colors.RESET}")


def print_info(text: str):
    """Print info message."""
    print(f"{Colors.BLUE}ℹ️  {text}{Colors.RESET}")


def print_warning(text: str):
    """Print warning message."""
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.RESET}")


class ChatSmokeTest:
    """Smoke test suite for WordFlux Chat API."""

    def __init__(self, host: str, port: int):
        """Initialize test suite."""
        self.base_url = f"http://{host}:{port}"
        self.session_id = f"smoke-{uuid.uuid4().hex[:8]}"
        self.tests_passed = 0
        self.tests_failed = 0

    def post_chat(self, message: str) -> Dict[str, Any]:
        """Send chat message to API."""
        url = f"{self.base_url}/chat"
        payload = {
            "message": message,
            "session_id": self.session_id
        }

        print_info(f"POST {url}")
        print_info(f"Payload: {json.dumps(payload, ensure_ascii=False)}")

        try:
            response = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30
            )

            print_info(f"Status: {response.status_code}")

            if response.status_code != 200:
                print_error(f"HTTP {response.status_code}: {response.text}")
                return {}

            data = response.json()
            print_info(f"Response: {json.dumps(data, ensure_ascii=False, indent=2)[:500]}...")
            return data

        except requests.exceptions.Timeout:
            print_error("Request timeout (30s)")
            return {}
        except Exception as e:
            print_error(f"Request failed: {e}")
            return {}

    def test_1_health_check(self) -> bool:
        """Test 1: Health check."""
        print_header("Test 1: Health Check")

        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            data = response.json()

            if response.status_code == 200 and data.get("status") == "healthy":
                print_success(f"API healthy: {data}")
                return True
            else:
                print_error(f"API unhealthy: {data}")
                return False
        except Exception as e:
            print_error(f"Health check failed: {e}")
            return False

    def test_2_summarize_board(self) -> bool:
        """Test 2: Resumo do quadro (summarize_board - low risk)."""
        print_header("Test 2: Resumo do Quadro (Low Risk - Auto Execute)")

        response = self.post_chat("Resumo do quadro")

        # Validate response
        if not response:
            print_error("Empty response")
            return False

        if "message" not in response:
            print_error("Missing 'message' field in response")
            return False

        message = response["message"]
        requires_approval = response.get("requires_approval", False)

        # summarize_board should NOT require approval (low risk)
        if requires_approval:
            print_warning("summarize_board unexpectedly requires approval")
            # Not a failure, but unexpected
        else:
            print_success("Response received inline (no approval required)")

        # Check if response is in Portuguese
        if any(word in message.lower() for word in ["coluna", "card", "quadro", "resumo"]):
            print_success("Response in Portuguese ✓")
        else:
            print_warning("Response might not be in Portuguese")

        print_success(f"Message: {message[:200]}...")
        return True

    def test_3_create_card_low_risk(self) -> bool:
        """Test 3: Criar card (create_card - low risk, should auto-execute)."""
        print_header("Test 3: Criar Card 'Landing de Outubro' (Low Risk - Auto Execute)")

        message = (
            "Crie card 'Landing de Outubro' na coluna Produção "
            "para Daniele, tag 'campanha', entrega 2025-10-15"
        )
        response = self.post_chat(message)

        # Validate response
        if not response:
            print_error("Empty response")
            return False

        if "message" not in response:
            print_error("Missing 'message' field in response")
            return False

        message_text = response["message"]
        requires_approval = response.get("requires_approval", False)
        tool_calls = response.get("tool_calls", [])

        # create_card to "Produção" should NOT require approval (low risk)
        if requires_approval:
            print_error("create_card to Produção unexpectedly requires approval")
            return False
        else:
            print_success("Card creation executed immediately (low risk) ✓")

        # Check if tool was called
        if tool_calls:
            tool_names = [tc.get("name") for tc in tool_calls]
            if "create_card" in tool_names:
                print_success(f"Tool calls: {tool_names} ✓")
            else:
                print_warning(f"Unexpected tools: {tool_names}")
        else:
            print_info("No tool_calls in response (may have been executed already)")

        # Check for job ID in message
        if "job" in message_text.lower():
            print_success(f"Job enqueued confirmation: {message_text[:150]}...")
        else:
            print_warning("No job confirmation in message")

        print_success(f"Response: {message_text[:200]}...")
        return True

    def test_4_move_to_finalizado_high_risk(self) -> bool:
        """Test 4: Mover para Finalizado (move_card - high risk, requires approval)."""
        print_header("Test 4: Mover para Finalizado (High Risk - Requires Approval)")

        message = "Mover o card 'Landing de Outubro' para Finalizado"
        response = self.post_chat(message)

        # Validate response
        if not response:
            print_error("Empty response")
            return False

        if "message" not in response:
            print_error("Missing 'message' field in response")
            return False

        message_text = response["message"]
        requires_approval = response.get("requires_approval", False)

        # move_card to "Finalizado" SHOULD require approval (high risk)
        if not requires_approval:
            print_error("move_card to Finalizado should require approval (high risk)")
            return False
        else:
            print_success("Approval required for high-risk action ✓")

        # Check for confirmation prompt
        if any(word in message_text.lower() for word in ["sim", "não", "confirmar", "cancelar"]):
            print_success("Confirmation prompt detected ✓")
        else:
            print_warning("Confirmation prompt not clear in response")

        print_success(f"Approval prompt: {message_text[:200]}...")

        # Step 2: Approve the action
        print_info("\nApproving action with 'sim'...")
        time.sleep(0.5)

        approval_response = self.post_chat("sim")

        if not approval_response:
            print_error("Empty approval response")
            return False

        approval_message = approval_response.get("message", "")

        # Check for confirmation keywords
        if "confirmado" in approval_message.lower() or "job" in approval_message.lower():
            print_success("Action confirmed and job enqueued ✓")
        else:
            print_warning(f"Unexpected approval response: {approval_message}")

        # Extract job ID (if present)
        if "job" in approval_message.lower():
            # Try to extract job ID from message
            import re
            job_match = re.search(r'job[:\s]+`?([a-z0-9\-]+)`?', approval_message.lower())
            if job_match:
                job_id = job_match.group(1)
                print_success(f"Job ID extracted: {job_id}")
            else:
                print_info("Job ID present but couldn't extract specific ID")
        else:
            print_warning("No job ID in approval response")

        print_success(f"Approval response: {approval_message[:200]}...")
        return True

    def run_all(self) -> int:
        """Run all tests and return exit code."""
        print_header("WordFlux Chat API - Smoke Test Suite")
        print_info(f"Base URL: {self.base_url}")
        print_info(f"Session ID: {self.session_id}")

        tests = [
            ("Health Check", self.test_1_health_check),
            ("Resumo do Quadro", self.test_2_summarize_board),
            ("Criar Card (Low Risk)", self.test_3_create_card_low_risk),
            ("Mover para Finalizado (High Risk)", self.test_4_move_to_finalizado_high_risk)
        ]

        for test_name, test_func in tests:
            try:
                passed = test_func()
                if passed:
                    self.tests_passed += 1
                else:
                    self.tests_failed += 1
            except Exception as e:
                print_error(f"Test '{test_name}' crashed: {e}")
                self.tests_failed += 1

        # Summary
        print_header("Test Summary")
        total = self.tests_passed + self.tests_failed
        print(f"{Colors.BOLD}Total: {total}{Colors.RESET}")
        print(f"{Colors.GREEN}Passed: {self.tests_passed}{Colors.RESET}")
        print(f"{Colors.RED}Failed: {self.tests_failed}{Colors.RESET}")

        if self.tests_failed == 0:
            print(f"\n{Colors.BOLD}{Colors.GREEN}🎉 All tests passed!{Colors.RESET}\n")
            return 0
        else:
            print(f"\n{Colors.BOLD}{Colors.RED}💥 Some tests failed{Colors.RESET}\n")
            return 1


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="WordFlux Chat API smoke test")
    parser.add_argument("--host", default="localhost", help="API host (default: localhost)")
    parser.add_argument("--port", type=int, default=8080, help="API port (default: 8080)")
    args = parser.parse_args()

    suite = ChatSmokeTest(host=args.host, port=args.port)
    exit_code = suite.run_all()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
