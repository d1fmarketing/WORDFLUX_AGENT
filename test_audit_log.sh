#!/bin/bash
#
# test_audit_log.sh - Comprehensive audit system test suite
#
# Tests:
# 1. 403 without API key
# 2. 403 with invalid API key
# 3. 200 with valid API key returns JSON
# 4. PT-BR fields present (sessao, usuario, acao, dados)
# 5. Text endpoint returns human-readable format
# 6. Sanitization works correctly (secrets masked)
#
# Usage:
#   export WF_OPERATOR_API_KEY=your-key-here
#   bash test_audit_log.sh
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
API_URL="${API_URL:-http://localhost:8080}"
AUDIT_ENDPOINT="$API_URL/audit/tail"
AUDIT_TEXT_ENDPOINT="$API_URL/audit/tail/text"
CHAT_ENDPOINT="$API_URL/chat/"  # Note: trailing slash required

# Test counters
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Helper functions
log_test() {
    echo -e "\n${YELLOW}[TEST $((TESTS_RUN + 1))]${NC} $1"
    TESTS_RUN=$((TESTS_RUN + 1))
}

pass() {
    echo -e "${GREEN}✅ PASS${NC}: $1"
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

fail() {
    echo -e "${RED}❌ FAIL${NC}: $1"
    TESTS_FAILED=$((TESTS_FAILED + 1))
}

# Validate prerequisites
if [ -z "$WF_OPERATOR_API_KEY" ]; then
    echo -e "${RED}ERROR:${NC} WF_OPERATOR_API_KEY environment variable not set"
    echo "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
    exit 1
fi

echo "================================================"
echo "WordFlux Audit System Test Suite"
echo "================================================"
echo "API URL: $API_URL"
echo "API Key: ${WF_OPERATOR_API_KEY:0:8}..."
echo ""

# TEST 1: Unauthorized without API key (422 or 403)
log_test "Unauthorized without API key"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$AUDIT_ENDPOINT")
if [ "$HTTP_CODE" == "422" ] || [ "$HTTP_CODE" == "403" ]; then
    pass "Returned $HTTP_CODE (unauthorized) as expected"
else
    fail "Expected 422 or 403, got $HTTP_CODE"
fi

# TEST 2: 403 with invalid API key
log_test "403 with invalid API key"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "X-API-Key: invalid-key-12345" \
    "$AUDIT_ENDPOINT")
if [ "$HTTP_CODE" == "403" ]; then
    pass "Returned 403 for invalid key"
else
    fail "Expected 403, got $HTTP_CODE"
fi

# TEST 3: 200 with valid API key returns JSON
log_test "200 with valid API key returns JSON"
RESPONSE=$(curl -s -w "\n%{http_code}" \
    -H "X-API-Key: $WF_OPERATOR_API_KEY" \
    "$AUDIT_ENDPOINT?n=10")

HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" == "200" ]; then
    # Validate JSON structure
    if echo "$BODY" | jq -e '.' > /dev/null 2>&1; then
        pass "Returned valid JSON with status 200"
    else
        fail "Response is not valid JSON: $BODY"
    fi
else
    fail "Expected 200, got $HTTP_CODE"
fi

# TEST 4: PT-BR fields present
log_test "PT-BR fields present (sessao, usuario, acao, dados)"
RESPONSE=$(curl -s \
    -H "X-API-Key: $WF_OPERATOR_API_KEY" \
    "$AUDIT_ENDPOINT?n=5")

if echo "$RESPONSE" | jq -e '.' > /dev/null 2>&1; then
    # Check if array has entries
    ENTRIES=$(echo "$RESPONSE" | jq 'length')

    if [ "$ENTRIES" -gt 0 ]; then
        # Check for PT-BR field names
        HAS_SESSAO=$(echo "$RESPONSE" | jq -e '.[0] | has("sessao")' && echo "true" || echo "false")
        HAS_USUARIO=$(echo "$RESPONSE" | jq -e '.[0] | has("usuario")' && echo "true" || echo "false")
        HAS_ACAO=$(echo "$RESPONSE" | jq -e '.[0] | has("acao")' && echo "true" || echo "false")
        HAS_DADOS=$(echo "$RESPONSE" | jq -e '.[0] | has("dados")' && echo "true" || echo "false")

        # Check for English field names (should NOT exist)
        HAS_SESSION_ID=$(echo "$RESPONSE" | jq -e '.[0] | has("session_id")' && echo "true" || echo "false")
        HAS_ACTION=$(echo "$RESPONSE" | jq -e '.[0] | has("action")' && echo "true" || echo "false")

        if [ "$HAS_SESSAO" == "true" ] && [ "$HAS_USUARIO" == "true" ] && \
           [ "$HAS_ACAO" == "true" ] && [ "$HAS_DADOS" == "true" ] && \
           [ "$HAS_SESSION_ID" == "false" ] && [ "$HAS_ACTION" == "false" ]; then
            pass "All PT-BR fields present, no English fields"
        else
            fail "Missing PT-BR fields or English fields present (sessao=$HAS_SESSAO, usuario=$HAS_USUARIO, acao=$HAS_ACAO, dados=$HAS_DADOS, session_id=$HAS_SESSION_ID, action=$HAS_ACTION)"
        fi
    else
        echo -e "${YELLOW}⚠️  SKIP${NC}: No audit entries in database yet"
    fi
else
    fail "Response is not valid JSON"
fi

# TEST 5: Text endpoint returns human-readable format
log_test "Text endpoint returns human-readable format"
TEXT_RESPONSE=$(curl -s \
    -H "X-API-Key: $WF_OPERATOR_API_KEY" \
    "$AUDIT_TEXT_ENDPOINT?n=5")

# Check format: [timestamp] session | action | data
if echo "$TEXT_RESPONSE" | grep -E '^\[20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9:]+' > /dev/null; then
    if echo "$TEXT_RESPONSE" | grep -E '\|' > /dev/null; then
        pass "Text format valid (timestamp | session | action | data)"
    else
        fail "Text format missing pipe separators"
    fi
else
    if echo "$TEXT_RESPONSE" | grep "Nenhuma entrada" > /dev/null; then
        echo -e "${YELLOW}⚠️  SKIP${NC}: No audit entries in database yet"
    else
        fail "Text format invalid (missing timestamp): $TEXT_RESPONSE"
    fi
fi

# TEST 6: Sanitization works correctly
log_test "Sanitization masks sensitive data"

# Generate a unique session ID for this test
TEST_SESSION="test-sanitize-$(date +%s)"

# Send a message with sensitive data (simulate)
CHAT_PAYLOAD=$(cat <<EOF
{
  "message": "Test with authorization: Bearer sk-ant-secret123456789",
  "session_id": "$TEST_SESSION"
}
EOF
)

CHAT_RESPONSE=$(curl -s -X POST \
    -H "Content-Type: application/json" \
    -d "$CHAT_PAYLOAD" \
    "$CHAT_ENDPOINT")

# Wait for async processing
sleep 2

# Retrieve audit log and check for sanitization
AUDIT_RESPONSE=$(curl -s \
    -H "X-API-Key: $WF_OPERATOR_API_KEY" \
    "$AUDIT_ENDPOINT?n=10")

# Check that the secret is NOT in the audit log
if echo "$AUDIT_RESPONSE" | grep "sk-ant-secret123456789" > /dev/null; then
    fail "Secret found in audit log (not sanitized)"
else
    # Check that we have the test session entry
    if echo "$AUDIT_RESPONSE" | jq -e ".[] | select(.sessao == \"$TEST_SESSION\")" > /dev/null 2>&1; then
        # Check that the text is truncated to 180 chars
        TEXT_LENGTH=$(echo "$AUDIT_RESPONSE" | jq -r ".[] | select(.sessao == \"$TEST_SESSION\") | .dados.texto // .dados.texto_truncado // \"\"" | head -n1 | wc -c)

        if [ "$TEXT_LENGTH" -le 181 ]; then  # 180 chars + newline
            pass "Sanitization works (secret not found, text truncated to ≤180 chars)"
        else
            fail "Text not truncated (length: $TEXT_LENGTH)"
        fi
    else
        echo -e "${YELLOW}⚠️  SKIP${NC}: Test session not found in audit log (may need to wait longer)"
    fi
fi

# Summary
echo ""
echo "================================================"
echo "Test Summary"
echo "================================================"
echo "Total:  $TESTS_RUN"
echo -e "Passed: ${GREEN}$TESTS_PASSED${NC}"
echo -e "Failed: ${RED}$TESTS_FAILED${NC}"
echo ""

if [ "$TESTS_FAILED" -eq 0 ]; then
    echo -e "${GREEN}✅ All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}❌ Some tests failed${NC}"
    exit 1
fi
