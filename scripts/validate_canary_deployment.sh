#!/bin/bash
# Canary Routing Deployment Validation Script
# Pre-deployment checks + Post-deployment validation

set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "======================================================================"
echo "  CANARY ROUTING DEPLOYMENT VALIDATION"
echo "======================================================================"
echo ""

# Configuration
API_URL="${API_URL:-http://localhost:8081}"
METRICS_URL="${METRICS_URL:-http://localhost:9300/metrics}"

# Test counters
PASSED=0
FAILED=0

pass_test() {
    echo -e "  ${GREEN}✅ PASS${NC}: $1"
    ((PASSED++))
}

fail_test() {
    echo -e "  ${RED}❌ FAIL${NC}: $1"
    ((FAILED++))
}

warn_test() {
    echo -e "  ${YELLOW}⚠️  WARN${NC}: $1"
}

# ==============================================================================
# PRE-DEPLOYMENT CHECKS
# ==============================================================================

echo "[1] PRE-DEPLOYMENT CHECKS"
echo "----------------------------------------------------------------------"

# Check 1: API keys present
if [ -z "$ANTHROPIC_API_KEY" ]; then
    warn_test "ANTHROPIC_API_KEY not set (required for Anthropic provider)"
else
    pass_test "ANTHROPIC_API_KEY present"
fi

if [ -z "$OPENAI_API_KEY" ]; then
    warn_test "OPENAI_API_KEY not set (required for OpenAI provider)"
else
    pass_test "OPENAI_API_KEY present"
fi

# Check 2: Redis connectivity
if redis-cli ping > /dev/null 2>&1; then
    pass_test "Redis connectivity OK"
else
    fail_test "Redis not accessible"
fi

# Check 3: Git clean state (for rollback)
if git diff-index --quiet HEAD -- 2>/dev/null; then
    pass_test "Git working directory clean"
else
    warn_test "Git has uncommitted changes (may complicate rollback)"
fi

# Check 4: Python dependencies
if /home/ubuntu/.venv/bin/python3 -c "import anthropic" 2>/dev/null; then
    pass_test "Anthropic SDK installed"
else
    fail_test "Anthropic SDK not installed"
fi

# ==============================================================================
# POST-DEPLOYMENT VALIDATION (if service is running)
# ==============================================================================

echo ""
echo "[2] POST-DEPLOYMENT VALIDATION"
echo "----------------------------------------------------------------------"

# Check 5: Health endpoint
if curl -sf "$API_URL/health" > /dev/null 2>&1; then
    pass_test "API health endpoint responding"

    # Check 6: Metrics endpoint
    if curl -sf "$METRICS_URL" > /dev/null 2>&1; then
        pass_test "Metrics endpoint accessible"

        # Check 7: Required metrics present
        METRICS_DATA=$(curl -s "$METRICS_URL")
        if echo "$METRICS_DATA" | grep -q "wordflux_chat_requests_total"; then
            pass_test "Chat metrics present"
        else
            warn_test "Chat metrics not yet populated (wait for requests)"
        fi

        # Check 8: Provider labels
        if echo "$METRICS_DATA" | grep -q 'provider='; then
            pass_test "Provider labels present in metrics"
        else
            warn_test "Provider labels not found (may be zero requests)"
        fi
    else
        warn_test "Metrics endpoint not accessible (check port 9300)"
    fi
else
    warn_test "API not running (skip post-deployment checks)"
fi

# ==============================================================================
# SUMMARY
# ==============================================================================

echo ""
echo "======================================================================"
echo "  VALIDATION SUMMARY"
echo "======================================================================"
echo "  Passed: $PASSED"
echo "  Failed: $FAILED"
echo "======================================================================"

if [ $FAILED -gt 0 ]; then
    echo -e "  ${RED}❌ VALIDATION FAILED${NC} - Fix issues before deployment"
    exit 1
else
    echo -e "  ${GREEN}✅ VALIDATION PASSED${NC} - Safe to deploy"
    exit 0
fi