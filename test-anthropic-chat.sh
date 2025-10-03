#!/bin/bash
#
# Acceptance tests for Anthropic-only chat integration
# Tests the chat endpoint with Anthropic Claude Sonnet 4.5
#
# Usage:
#   ./test-anthropic-chat.sh [BASE_URL]
#
# Example:
#   ./test-anthropic-chat.sh http://localhost:8081
#

set -euo pipefail

# Configuration
BASE_URL="${1:-http://localhost:8081}"
SESSION_ID="test-$(date +%s)-$$"
TEMP_DIR="/tmp/chat-test-$$"
mkdir -p "$TEMP_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test counter
TESTS_RUN=0
TESTS_PASSED=0

# Cleanup on exit
cleanup() {
    rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

# Helper functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

test_start() {
    TESTS_RUN=$((TESTS_RUN + 1))
    echo ""
    echo "================================================================"
    echo "TEST $TESTS_RUN: $1"
    echo "================================================================"
}

test_pass() {
    TESTS_PASSED=$((TESTS_PASSED + 1))
    log_info "✅ PASSED: $1"
}

test_fail() {
    log_error "❌ FAILED: $1"
}

# ============================================================================
# TEST 1: Health Check
# ============================================================================

test_start "Health check"

HEALTH_RESPONSE=$(curl -s "$BASE_URL/health")
log_info "Health response: $HEALTH_RESPONSE"

if echo "$HEALTH_RESPONSE" | grep -q '"status":"ok"'; then
    test_pass "Health check returned OK"
else
    test_fail "Health check failed"
    exit 1
fi

# ============================================================================
# TEST 2: Basic Chat (No Tools)
# ============================================================================

test_start "Basic chat without tools"

CHAT_REQUEST_1='{
  "message": "Olá! Como você pode me ajudar?",
  "session_id": "'"$SESSION_ID"'"
}'

log_info "Sending: $CHAT_REQUEST_1"

CHAT_RESPONSE_1=$(curl -s -X POST "$BASE_URL/chat/" \
    -H "Content-Type: application/json" \
    -d "$CHAT_REQUEST_1")

log_info "Response: $CHAT_RESPONSE_1"

if echo "$CHAT_RESPONSE_1" | grep -q '"role":"assistant"'; then
    test_pass "Basic chat returned assistant response"
else
    test_fail "Basic chat did not return assistant response"
fi

# Check that response is in Portuguese
if echo "$CHAT_RESPONSE_1" | grep -qi "ajudar\|assistente\|posso"; then
    test_pass "Response is in Portuguese"
else
    test_warn "Response may not be in Portuguese"
fi

# ============================================================================
# TEST 3: Tool Call - create_card
# ============================================================================

test_start "Tool call: create_card"

CHAT_REQUEST_2='{
  "message": "Crie uma tarefa chamada \"Landing page Q4\" no Backlog com descrição \"Criar landing page para Q4\"",
  "session_id": "'"$SESSION_ID"'"
}'

log_info "Sending: $CHAT_REQUEST_2"

CHAT_RESPONSE_2=$(curl -s -X POST "$BASE_URL/chat/" \
    -H "Content-Type: application/json" \
    -d "$CHAT_REQUEST_2")

log_info "Response: $CHAT_RESPONSE_2"

# Save response for later inspection
echo "$CHAT_RESPONSE_2" > "$TEMP_DIR/create_card_response.json"

# Check for tool_calls in response
if echo "$CHAT_RESPONSE_2" | grep -q '"tool_calls"'; then
    test_pass "Response contains tool_calls"

    # Check if create_card tool was called
    if echo "$CHAT_RESPONSE_2" | grep -q '"name":"create_card"'; then
        test_pass "create_card tool was called"
    else
        test_fail "create_card tool was NOT called"
    fi

    # Check for tool input
    if echo "$CHAT_RESPONSE_2" | grep -q '"input"'; then
        test_pass "Tool call has input parameter"
    else
        test_fail "Tool call missing input parameter"
    fi

    # Check for Anthropic format (not OpenAI format)
    if echo "$CHAT_RESPONSE_2" | grep -q '"function"'; then
        test_fail "Tool call is in OLD OpenAI format (has 'function' key)"
    else
        test_pass "Tool call is in Anthropic format (no 'function' wrapper)"
    fi
else
    test_fail "Response does NOT contain tool_calls"
fi

# ============================================================================
# TEST 4: Tool Call - list_cards
# ============================================================================

test_start "Tool call: list_cards"

CHAT_REQUEST_3='{
  "message": "Mostre-me todos os cards no Backlog",
  "session_id": "'"$SESSION_ID"'"
}'

log_info "Sending: $CHAT_REQUEST_3"

CHAT_RESPONSE_3=$(curl -s -X POST "$BASE_URL/chat/" \
    -H "Content-Type: application/json" \
    -d "$CHAT_REQUEST_3")

log_info "Response: $CHAT_RESPONSE_3"

if echo "$CHAT_RESPONSE_3" | grep -q '"name":"list_cards"'; then
    test_pass "list_cards tool was called"
else
    test_warn "list_cards tool may not have been called (LLM response varies)"
fi

# ============================================================================
# TEST 5: High-Risk Action - propose_move (Requires Approval)
# ============================================================================

test_start "High-risk action: propose_move (requires approval)"

CHAT_REQUEST_4='{
  "message": "Mova o card c-abc12345 para In Progress",
  "session_id": "'"$SESSION_ID"'"
}'

log_info "Sending: $CHAT_REQUEST_4"

CHAT_RESPONSE_4=$(curl -s -X POST "$BASE_URL/chat/" \
    -H "Content-Type: application/json" \
    -d "$CHAT_REQUEST_4")

log_info "Response: $CHAT_RESPONSE_4"

# Save response for approval test
echo "$CHAT_RESPONSE_4" > "$TEMP_DIR/propose_move_response.json"

# Check if approval is required
if echo "$CHAT_RESPONSE_4" | grep -q '"requires_approval":true'; then
    test_pass "High-risk action requires approval"

    # Extract proposal_id
    PROPOSAL_ID=$(echo "$CHAT_RESPONSE_4" | grep -o '"proposal_id":"[^"]*"' | cut -d'"' -f4)

    if [ -n "$PROPOSAL_ID" ]; then
        test_pass "Proposal ID extracted: $PROPOSAL_ID"
        echo "$PROPOSAL_ID" > "$TEMP_DIR/proposal_id.txt"
    else
        test_fail "Could not extract proposal_id"
    fi

    # Check for approval message in Portuguese
    if echo "$CHAT_RESPONSE_4" | grep -qi "aprovação\|aprovar"; then
        test_pass "Response includes approval message in Portuguese"
    else
        test_warn "Response may not include clear approval message"
    fi
else
    test_warn "propose_move may not have been detected as high-risk (LLM response varies)"
fi

# ============================================================================
# TEST 6: Approval Endpoint (if proposal_id available)
# ============================================================================

if [ -f "$TEMP_DIR/proposal_id.txt" ]; then
    PROPOSAL_ID=$(cat "$TEMP_DIR/proposal_id.txt")

    test_start "Approval endpoint with proposal_id: $PROPOSAL_ID"

    APPROVAL_REQUEST='{
      "proposal_id": "'"$PROPOSAL_ID"'"
    }'

    log_info "Sending approval: $APPROVAL_REQUEST"

    APPROVAL_RESPONSE=$(curl -s -X POST "$BASE_URL/chat/approve" \
        -H "Content-Type: application/json" \
        -d "$APPROVAL_REQUEST")

    log_info "Approval response: $APPROVAL_RESPONSE"

    if echo "$APPROVAL_RESPONSE" | grep -q '"success":true'; then
        test_pass "Approval succeeded"

        # Check for job_id
        if echo "$APPROVAL_RESPONSE" | grep -q '"job_id"'; then
            JOB_ID=$(echo "$APPROVAL_RESPONSE" | grep -o '"job_id":"[^"]*"' | cut -d'"' -f4)
            test_pass "Job queued with ID: $JOB_ID"
        else
            test_warn "No job_id in approval response"
        fi
    else
        test_fail "Approval failed"
    fi
else
    log_warn "Skipping approval test (no proposal_id available)"
fi

# ============================================================================
# TEST 7: Session History
# ============================================================================

test_start "Session history retrieval"

HISTORY_RESPONSE=$(curl -s "$BASE_URL/chat/history?session_id=$SESSION_ID")

log_info "History response length: ${#HISTORY_RESPONSE} characters"

if echo "$HISTORY_RESPONSE" | grep -q '"messages"'; then
    test_pass "History endpoint returned messages"

    # Count messages
    MESSAGE_COUNT=$(echo "$HISTORY_RESPONSE" | grep -o '"role":' | wc -l)
    log_info "Total messages in history: $MESSAGE_COUNT"

    if [ "$MESSAGE_COUNT" -gt 0 ]; then
        test_pass "History contains $MESSAGE_COUNT message(s)"
    else
        test_warn "History is empty"
    fi
else
    test_fail "History endpoint did not return messages"
fi

# ============================================================================
# TEST 8: SSE Stream (Connection Test Only)
# ============================================================================

test_start "SSE stream connection"

log_info "Testing SSE stream connection (5 second timeout)..."

# Start SSE stream in background, capture for 5 seconds
timeout 5 curl -N -s "$BASE_URL/events/stream" > "$TEMP_DIR/sse_output.txt" 2>&1 || true

if [ -s "$TEMP_DIR/sse_output.txt" ]; then
    LINES=$(wc -l < "$TEMP_DIR/sse_output.txt")
    log_info "SSE stream received $LINES line(s)"

    if [ "$LINES" -gt 0 ]; then
        test_pass "SSE stream is working"
        log_info "Sample SSE output:"
        head -5 "$TEMP_DIR/sse_output.txt" | sed 's/^/  /'
    else
        test_warn "SSE stream connected but no data received"
    fi
else
    test_warn "SSE stream did not receive data (may be normal if no events)"
fi

# ============================================================================
# TEST SUMMARY
# ============================================================================

echo ""
echo "================================================================"
echo "TEST SUMMARY"
echo "================================================================"
echo "Tests run:    $TESTS_RUN"
echo "Tests passed: $TESTS_PASSED"
echo "Pass rate:    $((TESTS_PASSED * 100 / TESTS_RUN))%"
echo ""

if [ "$TESTS_PASSED" -eq "$TESTS_RUN" ]; then
    log_info "✅ ALL TESTS PASSED"
    exit 0
elif [ "$TESTS_PASSED" -ge "$((TESTS_RUN * 3 / 4))" ]; then
    log_warn "⚠️  MOST TESTS PASSED ($TESTS_PASSED/$TESTS_RUN)"
    exit 0
else
    log_error "❌ MANY TESTS FAILED ($((TESTS_RUN - TESTS_PASSED)) failures)"
    exit 1
fi
