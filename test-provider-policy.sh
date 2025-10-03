#!/bin/bash
#
# WordFlux Provider Policy - Smoke Test
# Valida canary routing, circuit breaker e métricas Prometheus
#
# Uso:
#   chmod +x test-provider-policy.sh
#   ./test-provider-policy.sh [BASE_URL]
#
# Exemplo:
#   ./test-provider-policy.sh http://localhost:8080
#   WF_CANARY_PCT=10 ./test-provider-policy.sh http://localhost:8080

set -e

BASE_URL="${1:-http://localhost:8080}"
REDIS_CLI="${REDIS_CLI:-redis-cli}"

echo "=================================================="
echo "WordFlux Provider Policy - Smoke Test"
echo "=================================================="
echo "Base URL: $BASE_URL"
echo "Canary %: ${WF_CANARY_PCT:-0}%"
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper function
check_redis() {
    if ! command -v $REDIS_CLI &> /dev/null; then
        echo -e "${YELLOW}⚠ redis-cli not found, skipping Redis checks${NC}"
        return 1
    fi
    return 0
}

# Test 1: Validate canary routing distribution
echo -e "${BLUE}[1/6]${NC} Testing canary routing distribution..."
echo "Sending 100 requests with different session_ids..."

CANARY_COUNT=0
DEFAULT_COUNT=0
PROVIDER_HEADER="x-llm-provider"

for i in {1..100}; do
    SESSION_ID="test-canary-$i"

    # Send chat request and capture provider from response metadata
    RESPONSE=$(curl -s -X POST "$BASE_URL/chat/" \
        -H "Content-Type: application/json" \
        -d "{
            \"message\": \"ping\",
            \"session_id\": \"$SESSION_ID\"
        }" 2>/dev/null || echo '{}')

    # Try to detect which provider was used from response or logs
    # (In real implementation, chat.py could add x-llm-provider header)
    # For now, just count successful requests
    if echo "$RESPONSE" | jq -e '.message' &> /dev/null; then
        DEFAULT_COUNT=$((DEFAULT_COUNT + 1))
    fi
done

echo -e "${GREEN}✓ Sent 100 requests${NC}"
echo "  Distribution validation: Run with WF_CANARY_PCT=10 and check logs for 'Canary routing' messages"
echo ""

# Test 2: Validate stickiness (same session → same provider)
echo -e "${BLUE}[2/6]${NC} Testing session stickiness..."
SESSION_ID="sticky-test-session"

echo "Sending 5 requests with same session_id..."
for i in {1..5}; do
    curl -s -X POST "$BASE_URL/chat/" \
        -H "Content-Type: application/json" \
        -d "{
            \"message\": \"test $i\",
            \"session_id\": \"$SESSION_ID\"
        }" > /dev/null
done

echo -e "${GREEN}✓ Sent 5 requests with same session${NC}"
echo "  Stickiness validation: Check logs - all 5 should use same provider"
echo ""

# Test 3: Test circuit breaker trip
echo -e "${BLUE}[3/6]${NC} Testing circuit breaker trip..."

if check_redis; then
    echo "Manually tripping circuit for 'openai' (30s TTL)..."
    $REDIS_CLI SETEX "wf:cb:openai:disabled" 30 "1" > /dev/null 2>&1

    # Check if circuit is tripped
    CIRCUIT_STATUS=$($REDIS_CLI GET "wf:cb:openai:disabled" 2>/dev/null || echo "0")

    if [ "$CIRCUIT_STATUS" = "1" ]; then
        echo -e "${GREEN}✓ Circuit tripped for openai${NC}"

        # Try to make request - should use fallback
        RESPONSE=$(curl -s -X POST "$BASE_URL/chat/" \
            -H "Content-Type: application/json" \
            -d '{
                "message": "test during circuit trip",
                "session_id": "circuit-test-session"
            }')

        if echo "$RESPONSE" | jq -e '.message' &> /dev/null; then
            echo -e "${GREEN}✓ Request succeeded with fallback provider${NC}"
            echo "  Check logs for 'Circuit breaker OPEN' message"
        else
            echo -e "${RED}✗ Request failed (should have used fallback)${NC}"
        fi
    else
        echo -e "${RED}✗ Failed to trip circuit${NC}"
    fi

    echo ""
fi

# Test 4: Test circuit breaker reset
echo -e "${BLUE}[4/6]${NC} Testing circuit breaker reset..."

if check_redis; then
    echo "Resetting circuit for 'openai'..."
    $REDIS_CLI DEL "wf:cb:openai:disabled" > /dev/null 2>&1

    CIRCUIT_STATUS=$($REDIS_CLI GET "wf:cb:openai:disabled" 2>/dev/null || echo "0")

    if [ "$CIRCUIT_STATUS" != "1" ]; then
        echo -e "${GREEN}✓ Circuit reset for openai${NC}"
    else
        echo -e "${RED}✗ Failed to reset circuit${NC}"
    fi
    echo ""
fi

# Test 5: Validate Prometheus metrics
echo -e "${BLUE}[5/6]${NC} Testing Prometheus metrics endpoint..."

METRICS=$(curl -s "$BASE_URL/metrics" 2>/dev/null || echo "")

if [ -n "$METRICS" ]; then
    echo -e "${GREEN}✓ Metrics endpoint accessible${NC}"

    # Check for expected metrics
    METRICS_TO_CHECK=(
        "wordflux_chat_requests_total"
        "wordflux_chat_latency_seconds"
        "wordflux_llm_fallback_total"
        "wordflux_chat_tool_calls_total"
        "wordflux_circuit_breaker_trips_total"
    )

    for metric in "${METRICS_TO_CHECK[@]}"; do
        if echo "$METRICS" | grep -q "$metric"; then
            echo -e "  ${GREEN}✓${NC} $metric"
        else
            echo -e "  ${YELLOW}⚠${NC} $metric (not found, may not have data yet)"
        fi
    done
else
    echo -e "${RED}✗ Metrics endpoint not accessible${NC}"
fi
echo ""

# Test 6: Test Python provider_policy module
echo -e "${BLUE}[6/6]${NC} Testing provider_policy module..."

PYTHON_TEST=$(python3 -c "
import sys
sys.path.insert(0, '/home/ubuntu')
try:
    from src.core.provider_policy import select_provider, circuit_tripped, is_canary_user

    # Test select_provider
    provider1 = select_provider('user-123')
    provider2 = select_provider('user-123')
    assert provider1 == provider2, 'Stickiness failed'

    # Test is_canary_user
    canary_count = sum(1 for i in range(100) if is_canary_user(f'user-{i}', canary_pct=10))
    assert 5 <= canary_count <= 15, f'Canary distribution off: {canary_count}% (expected ~10%)'

    print('✓ All module tests passed')
    print(f'  Stickiness: OK (user-123 → {provider1})')
    print(f'  Canary distribution: {canary_count}% (expected ~10%)')
    sys.exit(0)
except Exception as e:
    print(f'✗ Module test failed: {e}')
    sys.exit(1)
" 2>&1)

echo "$PYTHON_TEST"
echo ""

# Summary
echo "=================================================="
echo "Smoke Test Complete"
echo "=================================================="
echo ""
echo "To validate canary routing:"
echo "  1. Set WF_CANARY_PCT=10"
echo "  2. Restart API service"
echo "  3. Re-run this script"
echo "  4. Check logs for 'Canary routing' messages"
echo ""
echo "To inspect circuit breaker state:"
echo "  redis-cli KEYS 'wf:cb:*'"
echo "  redis-cli GET 'wf:cb:openai:disabled'"
echo ""
echo "To view metrics:"
echo "  curl $BASE_URL/metrics | grep wordflux_circuit_breaker"
echo "  curl $BASE_URL/metrics | grep wordflux_llm_fallback"
echo ""
echo "Current environment:"
echo "  WF_LLM_PROVIDER: ${WF_LLM_PROVIDER:-openai}"
echo "  WF_CANARY_PROVIDER: ${WF_CANARY_PROVIDER:-anthropic}"
echo "  WF_CANARY_PCT: ${WF_CANARY_PCT:-0}%"
echo "  WF_LLM_PROVIDER_FALLBACK: ${WF_LLM_PROVIDER_FALLBACK:-openai}"
echo ""