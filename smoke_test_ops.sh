#!/bin/bash
#
# WordFlux Chat Agent - Operational Smoke Test
# DevOps validation script with curl commands, metrics checks, and rollback procedures
#
# Usage:
#   ./smoke_test_ops.sh [BASE_URL]
#
# Example:
#   ./smoke_test_ops.sh http://localhost:8080
#   ./smoke_test_ops.sh http://wordflux.3-228-174-188.nip.io:8080

set -e

BASE_URL="${1:-http://localhost:8080}"
SESSION_ID="smoke-ops-$(date +%s)"
PROPOSAL_ID=""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "========================================================================"
echo "WordFlux Chat Agent - Operational Smoke Test"
echo "========================================================================"
echo "Base URL: $BASE_URL"
echo "Session ID: $SESSION_ID"
echo ""

# ========================================================================
# COMANDO 1: POST /chat (High-Risk Action)
# ========================================================================
echo -e "${BLUE}[1/3] POST /chat - Mover card c-1 para Aprovação${NC}"
echo "----------------------------------------------------------------------"

CHAT_RESPONSE=$(curl -s -X POST "$BASE_URL/chat/" \
  -H "Content-Type: application/json" \
  -d "{
    \"message\": \"Mova o card c-1 para Aprovação\",
    \"session_id\": \"$SESSION_ID\"
  }")

echo "$CHAT_RESPONSE" | jq .

# Extract proposal_id
PROPOSAL_ID=$(echo "$CHAT_RESPONSE" | jq -r '.proposal_id // empty')
REQUIRES_APPROVAL=$(echo "$CHAT_RESPONSE" | jq -r '.requires_approval')

if [ "$REQUIRES_APPROVAL" = "true" ] && [ -n "$PROPOSAL_ID" ]; then
    echo -e "${GREEN}✓ High-risk action detected, approval required${NC}"
    echo -e "${GREEN}✓ Proposal ID: $PROPOSAL_ID${NC}"
else
    echo -e "${RED}✗ Expected requires_approval=true with proposal_id${NC}"
    exit 1
fi

echo ""
sleep 2

# ========================================================================
# COMANDO 2: SSE /events/stream (Capturar pending_approval)
# ========================================================================
echo -e "${BLUE}[2/3] SSE /events/stream - Capturar pending_approval${NC}"
echo "----------------------------------------------------------------------"
echo "SSE stream conectado. Aguardando eventos..."
echo ""

# Start SSE listener in background
SSE_LOG="/tmp/sse_smoke_${SESSION_ID}.log"
curl -N -s "$BASE_URL/events/stream" > "$SSE_LOG" 2>&1 &
SSE_PID=$!

# Wait for pending_approval event (max 10 seconds)
echo "Aguardando evento pending_approval no SSE..."
for i in {1..10}; do
    if grep -q "pending_approval" "$SSE_LOG" 2>/dev/null; then
        echo -e "${GREEN}✓ Evento pending_approval capturado no SSE${NC}"

        # Extract proposal_id from SSE
        SSE_PROPOSAL=$(grep -o '"proposal_id":"[^"]*"' "$SSE_LOG" | head -1 | cut -d'"' -f4)

        if [ "$SSE_PROPOSAL" = "$PROPOSAL_ID" ]; then
            echo -e "${GREEN}✓ Proposal ID no SSE corresponde: $SSE_PROPOSAL${NC}"
        else
            echo -e "${YELLOW}⚠ Proposal ID no SSE diferente (pode ser de outra sessão)${NC}"
        fi

        # Show SSE event
        echo ""
        echo "Evento SSE capturado:"
        grep "pending_approval" "$SSE_LOG" | head -1 | sed 's/^data: //' | jq .
        break
    fi
    sleep 1
done

# Kill SSE process
kill $SSE_PID 2>/dev/null || true

# Cleanup
rm -f "$SSE_LOG"

echo ""
sleep 2

# ========================================================================
# COMANDO 3: POST /chat/approve (Aprovar Proposta)
# ========================================================================
echo -e "${BLUE}[3/3] POST /chat/approve - Aprovar proposta${NC}"
echo "----------------------------------------------------------------------"

if [ -z "$PROPOSAL_ID" ] || [ "$PROPOSAL_ID" = "null" ]; then
    echo -e "${RED}✗ No proposal_id to approve${NC}"
    exit 1
fi

APPROVE_RESPONSE=$(curl -s -X POST "$BASE_URL/chat/approve" \
  -H "Content-Type: application/json" \
  -d "{
    \"proposal_id\": \"$PROPOSAL_ID\"
  }")

echo "$APPROVE_RESPONSE" | jq .

# Validate approval
SUCCESS=$(echo "$APPROVE_RESPONSE" | jq -r '.success')
JOB_ID=$(echo "$APPROVE_RESPONSE" | jq -r '.job_id // empty')

if [ "$SUCCESS" = "true" ] && [ -n "$JOB_ID" ]; then
    echo -e "${GREEN}✓ Approval succeeded${NC}"
    echo -e "${GREEN}✓ Job ID: $JOB_ID${NC}"
else
    echo -e "${RED}✗ Approval failed${NC}"
    exit 1
fi

echo ""
echo "========================================================================"
echo "Smoke Test: PASSED ✓"
echo "========================================================================"
echo ""

# ========================================================================
# MÉTRICAS PROMETHEUS - 6 Critical Metrics
# ========================================================================
echo -e "${BLUE}[MÉTRICAS] Consultando Prometheus /metrics${NC}"
echo "----------------------------------------------------------------------"

METRICS=$(curl -s "$BASE_URL/metrics" 2>/dev/null || echo "")

if [ -z "$METRICS" ]; then
    echo -e "${YELLOW}⚠ Metrics endpoint not accessible${NC}"
    exit 0
fi

echo ""
echo "=== 6 MÉTRICAS CRÍTICAS ==="
echo ""

# Metric 1: wordflux_chat_requests_total
echo -e "${BLUE}[1/6] wordflux_chat_requests_total${NC}"
echo "$METRICS" | grep "^wordflux_chat_requests_total" | head -5
echo ""

# Metric 2: wordflux_chat_latency_seconds
echo -e "${BLUE}[2/6] wordflux_chat_latency_seconds (p95, p99)${NC}"
echo "$METRICS" | grep "^wordflux_chat_latency_seconds" | grep -E 'quantile="0.95"|quantile="0.99"'
echo ""

# Metric 3: wordflux_llm_fallback_total
echo -e "${BLUE}[3/6] wordflux_llm_fallback_total${NC}"
echo "$METRICS" | grep "^wordflux_llm_fallback_total" || echo "(no fallbacks yet - good!)"
echo ""

# Metric 4: wordflux_circuit_breaker_trips_total
echo -e "${BLUE}[4/6] wordflux_circuit_breaker_trips_total${NC}"
echo "$METRICS" | grep "^wordflux_circuit_breaker_trips_total" || echo "(no trips yet - good!)"
echo ""

# Metric 5: wordflux_chat_tool_calls_total
echo -e "${BLUE}[5/6] wordflux_chat_tool_calls_total${NC}"
echo "$METRICS" | grep "^wordflux_chat_tool_calls_total" | head -5
echo ""

# Metric 6: wordflux_chat_cost_usd_daily
echo -e "${BLUE}[6/6] wordflux_chat_cost_usd_daily${NC}"
echo "$METRICS" | grep "^wordflux_chat_cost_usd_daily"
echo ""

# ========================================================================
# NO-GO CONDITIONS - Health Check
# ========================================================================
echo "========================================================================"
echo -e "${BLUE}[NO-GO] Checking Deployment Health${NC}"
echo "========================================================================"
echo ""

# Calculate fallback rate
TOTAL_REQUESTS=$(echo "$METRICS" | grep "^wordflux_chat_requests_total" | awk '{sum+=$2} END {print sum}')
TOTAL_FALLBACKS=$(echo "$METRICS" | grep "^wordflux_llm_fallback_total" | awk '{sum+=$2} END {print sum}')

if [ -z "$TOTAL_REQUESTS" ] || [ "$TOTAL_REQUESTS" -eq 0 ]; then
    FALLBACK_RATE=0
else
    FALLBACK_RATE=$(echo "scale=2; ($TOTAL_FALLBACKS / $TOTAL_REQUESTS) * 100" | bc 2>/dev/null || echo "0")
fi

echo -e "${BLUE}[1/2] Fallback Rate: ${FALLBACK_RATE}%${NC}"
if (( $(echo "$FALLBACK_RATE > 8.0" | bc -l 2>/dev/null || echo 0) )); then
    echo -e "${RED}❌ NO-GO: Fallback rate > 8% ($FALLBACK_RATE%)${NC}"
    echo -e "${RED}   Action: Rollback canary deployment${NC}"
    NOGO=true
else
    echo -e "${GREEN}✓ PASS: Fallback rate < 8%${NC}"
fi

echo ""

# Check P95 latency
P95_LATENCY=$(echo "$METRICS" | grep 'wordflux_chat_latency_seconds.*quantile="0.95"' | head -1 | awk '{print $2}')

if [ -z "$P95_LATENCY" ]; then
    P95_LATENCY=0
fi

echo -e "${BLUE}[2/2] P95 Latency: ${P95_LATENCY}s${NC}"
if (( $(echo "$P95_LATENCY > 10.0" | bc -l 2>/dev/null || echo 0) )); then
    echo -e "${RED}❌ NO-GO: P95 latency > 10s (${P95_LATENCY}s)${NC}"
    echo -e "${RED}   Action: Rollback canary deployment${NC}"
    NOGO=true
else
    echo -e "${GREEN}✓ PASS: P95 latency < 10s${NC}"
fi

echo ""

if [ "$NOGO" = "true" ]; then
    echo "========================================================================"
    echo -e "${RED}NO-GO CONDITIONS TRIGGERED - ROLLBACK REQUIRED${NC}"
    echo "========================================================================"
    echo ""
    echo "Execute rollback:"
    echo "  1. export WF_CANARY_PCT=0"
    echo "  2. sudo systemctl restart wordflux-api"
    echo "  3. sudo systemctl restart wordflux-cockpit"
    echo "  4. Monitor metrics for recovery"
    echo ""
    exit 1
else
    echo "========================================================================"
    echo -e "${GREEN}✓ ALL HEALTH CHECKS PASSED${NC}"
    echo "========================================================================"
    echo ""
fi

# ========================================================================
# ROLLBACK INSTRUCTIONS
# ========================================================================
echo ""
echo "========================================================================"
echo "ROLLBACK PLAYBOOK"
echo "========================================================================"
echo ""
echo "If NO-GO conditions are met, execute rollback:"
echo ""
echo "  # 1. Disable canary"
echo "  export WF_CANARY_PCT=0"
echo "  echo 'WF_CANARY_PCT=0' | sudo tee -a /etc/environment"
echo ""
echo "  # 2. Restart services"
echo "  sudo systemctl restart wordflux-api"
echo "  sudo systemctl restart wordflux-cockpit"
echo ""
echo "  # 3. Verify rollback"
echo "  curl -s http://localhost:8080/health | jq"
echo "  tail -f /var/log/wordflux-cockpit.log | grep -i canary"
echo ""
echo "  # 4. Monitor metrics"
echo "  watch -n 5 'curl -s http://localhost:8080/metrics | grep fallback'"
echo ""
echo "========================================================================"
echo "END OF SMOKE TEST"
echo "========================================================================"