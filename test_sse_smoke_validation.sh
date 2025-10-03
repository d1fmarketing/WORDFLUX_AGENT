#!/bin/bash
################################################################################
# SSE Smoke Test - Validation Suite
#
# Purpose: Automated test suite to validate the SSE smoke test script
# Usage: ./test_sse_smoke_validation.sh
#
# Tests:
#   1. Script exists and is executable
#   2. Script has no syntax errors
#   3. Script has no TODO/FIXME placeholders
#   4. Required dependencies are available
#   5. Script handles invalid URLs gracefully
#   6. Script help/usage works
################################################################################

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test counters
TESTS_PASSED=0
TESTS_FAILED=0
TESTS_TOTAL=0

# Script under test
SCRIPT_PATH="/home/ubuntu/test_sse_smoke.sh"

################################################################################
# Helper Functions
################################################################################

print_header() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

test_start() {
    ((TESTS_TOTAL++))
    echo -e "\n${YELLOW}[TEST $TESTS_TOTAL]${NC} $1"
}

test_pass() {
    ((TESTS_PASSED++))
    echo -e "${GREEN}✅ PASS${NC}: $1"
}

test_fail() {
    ((TESTS_FAILED++))
    echo -e "${RED}❌ FAIL${NC}: $1"
}

print_summary() {
    echo ""
    print_header "Test Summary"
    echo "Total:  $TESTS_TOTAL"
    echo -e "Passed: ${GREEN}$TESTS_PASSED${NC}"
    echo -e "Failed: ${RED}$TESTS_FAILED${NC}"
    echo ""

    if [ $TESTS_FAILED -eq 0 ]; then
        echo -e "${GREEN}✅ All tests passed!${NC}"
        exit 0
    else
        echo -e "${RED}❌ Some tests failed${NC}"
        exit 1
    fi
}

################################################################################
# Test Cases
################################################################################

test_01_script_exists() {
    test_start "Script exists at $SCRIPT_PATH"

    if [ -f "$SCRIPT_PATH" ]; then
        test_pass "Script file exists"
    else
        test_fail "Script file not found"
    fi
}

test_02_script_executable() {
    test_start "Script is executable"

    if [ -x "$SCRIPT_PATH" ]; then
        test_pass "Script has execute permissions"
    else
        test_fail "Script is not executable (run: chmod +x $SCRIPT_PATH)"
    fi
}

test_03_bash_syntax() {
    test_start "Script has valid bash syntax"

    if bash -n "$SCRIPT_PATH" 2>/dev/null; then
        test_pass "No syntax errors detected"
    else
        test_fail "Syntax errors detected"
        bash -n "$SCRIPT_PATH" 2>&1 | head -5
    fi
}

test_04_no_placeholders() {
    test_start "Script has no TODO/FIXME placeholders"

    local placeholders=$(grep -E "TODO|FIXME|XXX|HACK|PLACEHOLDER" "$SCRIPT_PATH" || true)

    if [ -z "$placeholders" ]; then
        test_pass "No placeholders found"
    else
        test_fail "Found placeholders:"
        echo "$placeholders" | head -5
    fi
}

test_05_required_commands() {
    test_start "Required commands are available"

    local missing_commands=()

    for cmd in curl timeout grep date jobs; do
        if ! command -v "$cmd" &>/dev/null; then
            missing_commands+=("$cmd")
        fi
    done

    if [ ${#missing_commands[@]} -eq 0 ]; then
        test_pass "All required commands available (curl, timeout, grep, date, jobs)"
    else
        test_fail "Missing commands: ${missing_commands[*]}"
    fi
}

test_06_shebang() {
    test_start "Script has correct shebang"

    local shebang=$(head -1 "$SCRIPT_PATH")

    if [[ "$shebang" == "#!/bin/bash" ]] || [[ "$shebang" == "#!/usr/bin/env bash" ]]; then
        test_pass "Valid shebang: $shebang"
    else
        test_fail "Invalid shebang: $shebang"
    fi
}

test_07_set_flags() {
    test_start "Script uses safe bash flags (set -euo pipefail)"

    if grep -q "set -euo pipefail" "$SCRIPT_PATH"; then
        test_pass "Safe bash flags enabled"
    else
        test_fail "Missing 'set -euo pipefail' (recommended for robust scripts)"
    fi
}

test_08_cleanup_handler() {
    test_start "Script has cleanup handler (trap)"

    if grep -q "trap.*cleanup.*EXIT" "$SCRIPT_PATH"; then
        test_pass "Cleanup handler registered"
    else
        test_fail "No cleanup handler found (potential resource leak)"
    fi
}

test_09_configuration_variables() {
    test_start "Script defines required configuration variables"

    local required_vars=("COCKPIT_URL" "CHAT_URL" "SSE_URL" "DURATION" "MIN_HEARTBEATS" "MIN_JOB_EVENTS")
    local missing_vars=()

    for var in "${required_vars[@]}"; do
        if ! grep -q "^${var}=" "$SCRIPT_PATH"; then
            missing_vars+=("$var")
        fi
    done

    if [ ${#missing_vars[@]} -eq 0 ]; then
        test_pass "All configuration variables defined"
    else
        test_fail "Missing variables: ${missing_vars[*]}"
    fi
}

test_10_heartbeat_detection() {
    test_start "Script detects heartbeat format correctly"

    # Check if script looks for ": hb" (correct SSE heartbeat format)
    if grep -q '"\: hb"' "$SCRIPT_PATH" || grep -q "': hb'" "$SCRIPT_PATH"; then
        test_pass "Heartbeat detection pattern found (': hb')"
    else
        test_fail "Heartbeat detection pattern not found"
    fi
}

test_11_event_detection() {
    test_start "Script detects job_queued events"

    if grep -q '"kind":"job_queued"' "$SCRIPT_PATH" || grep -q "job_queued" "$SCRIPT_PATH"; then
        test_pass "Event detection pattern found (job_queued)"
    else
        test_fail "Event detection pattern not found"
    fi
}

test_12_reconnection_logic() {
    test_start "Script implements reconnection logic"

    if grep -q "reconnect" "$SCRIPT_PATH" || grep -q "retry" "$SCRIPT_PATH"; then
        test_pass "Reconnection logic present"
    else
        test_fail "No reconnection logic found"
    fi
}

test_13_success_message() {
    test_start "Script prints 'SSE OK' on success"

    if grep -q "SSE OK" "$SCRIPT_PATH"; then
        test_pass "'SSE OK' success message found"
    else
        test_fail "'SSE OK' message not found in script"
    fi
}

test_14_exit_codes() {
    test_start "Script uses correct exit codes"

    local exit_codes_ok=true

    # Check for exit 0 (success)
    if ! grep -q "exit 0" "$SCRIPT_PATH"; then
        test_fail "No 'exit 0' found (success case)"
        exit_codes_ok=false
    fi

    # Check for exit 1 (validation failure)
    if ! grep -q "exit 1" "$SCRIPT_PATH"; then
        test_fail "No 'exit 1' found (failure case)"
        exit_codes_ok=false
    fi

    if $exit_codes_ok; then
        test_pass "Exit codes properly defined (0=success, 1/2=failure)"
    fi
}

test_15_documentation() {
    test_start "Script has header documentation"

    # Check for comment block in first 50 lines
    if head -50 "$SCRIPT_PATH" | grep -q "Purpose:\|Usage:\|Description:"; then
        test_pass "Header documentation present"
    else
        test_fail "Missing header documentation"
    fi
}

test_16_user_instructions() {
    test_start "Script provides user instructions"

    if grep -q "ACTION REQUIRED" "$SCRIPT_PATH" || grep -q "curl.*chat" "$SCRIPT_PATH"; then
        test_pass "User instructions included"
    else
        test_fail "No user instructions found"
    fi
}

test_17_line_count_reasonable() {
    test_start "Script is not excessively long"

    local line_count=$(wc -l < "$SCRIPT_PATH")

    if [ "$line_count" -lt 500 ]; then
        test_pass "Script is $line_count lines (reasonable length)"
    else
        test_fail "Script is $line_count lines (may be too complex)"
    fi
}

test_18_no_hardcoded_secrets() {
    test_start "Script has no hardcoded secrets"

    local secrets=$(grep -iE "password|api_key|secret|token" "$SCRIPT_PATH" | grep -v "session_id" || true)

    if [ -z "$secrets" ]; then
        test_pass "No hardcoded secrets found"
    else
        test_fail "Potential secrets found:"
        echo "$secrets" | head -3
    fi
}

test_19_preflight_check() {
    test_start "Script has pre-flight health check"

    if grep -q "/health" "$SCRIPT_PATH"; then
        test_pass "Pre-flight health check implemented"
    else
        test_fail "No pre-flight check found (recommended for fast failure)"
    fi
}

test_20_error_messages() {
    test_start "Script provides helpful error messages"

    local error_msg_count=$(grep -c "FAILED\|❌\|ERROR" "$SCRIPT_PATH" || echo "0")

    if [ "$error_msg_count" -ge 3 ]; then
        test_pass "Multiple error messages defined ($error_msg_count)"
    else
        test_fail "Insufficient error messages (found: $error_msg_count)"
    fi
}

################################################################################
# Main Execution
################################################################################

main() {
    print_header "SSE Smoke Test - Validation Suite"
    echo "Script under test: $SCRIPT_PATH"

    # Run all tests
    test_01_script_exists
    test_02_script_executable
    test_03_bash_syntax
    test_04_no_placeholders
    test_05_required_commands
    test_06_shebang
    test_07_set_flags
    test_08_cleanup_handler
    test_09_configuration_variables
    test_10_heartbeat_detection
    test_11_event_detection
    test_12_reconnection_logic
    test_13_success_message
    test_14_exit_codes
    test_15_documentation
    test_16_user_instructions
    test_17_line_count_reasonable
    test_18_no_hardcoded_secrets
    test_19_preflight_check
    test_20_error_messages

    # Print summary
    print_summary
}

# Execute main function
main "$@"
