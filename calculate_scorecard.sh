#!/bin/bash
#
# WordFlux A/B Test Scorecard Calculator
# Automatiza comparação de métricas entre providers
#
# Usage:
#   ./calculate_scorecard.sh [metrics_file]
#
# Example:
#   curl -s http://localhost:8080/metrics > metrics.txt
#   ./calculate_scorecard.sh metrics.txt

set -e

METRICS_FILE="${1:-/dev/stdin}"

if [ "$METRICS_FILE" != "/dev/stdin" ] && [ ! -f "$METRICS_FILE" ]; then
    echo "Error: Metrics file not found: $METRICS_FILE"
    exit 1
fi

echo "========================================================================"
echo "WordFlux A/B Test Scorecard Calculator"
echo "========================================================================"
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

# ========================================================================
# Extract metrics from file
# ========================================================================

# Total requests per provider
REQUESTS_OPENAI=$(grep 'wordflux_chat_requests_total{provider="openai"' "$METRICS_FILE" | grep 'status="success"' | awk '{sum+=$2} END {print sum+0}')
REQUESTS_ANTHROPIC=$(grep 'wordflux_chat_requests_total{provider="anthropic"' "$METRICS_FILE" | grep 'status="success"' | awk '{sum+=$2} END {print sum+0}')

# Tool calls per provider
TOOLS_OPENAI=$(grep 'wordflux_chat_tool_calls_total{.*provider="openai"' "$METRICS_FILE" | awk '{sum+=$2} END {print sum+0}')
TOOLS_ANTHROPIC=$(grep 'wordflux_chat_tool_calls_total{.*provider="anthropic"' "$METRICS_FILE" | awk '{sum+=$2} END {print sum+0}')

# P95 latency per provider
P95_OPENAI=$(grep 'wordflux_chat_latency_seconds_bucket{provider="openai".*le="+Inf"' "$METRICS_FILE" | awk '{print $2}' | head -1)
P95_ANTHROPIC=$(grep 'wordflux_chat_latency_seconds_bucket{provider="anthropic".*le="+Inf"' "$METRICS_FILE" | awk '{print $2}' | head -1)

# If histogram not available, use quantile
if [ -z "$P95_OPENAI" ]; then
    P95_OPENAI=$(grep 'wordflux_chat_latency_seconds{provider="openai".*quantile="0.95"' "$METRICS_FILE" | awk '{print $2}')
fi
if [ -z "$P95_ANTHROPIC" ]; then
    P95_ANTHROPIC=$(grep 'wordflux_chat_latency_seconds{provider="anthropic".*quantile="0.95"' "$METRICS_FILE" | awk '{print $2}')
fi

# Cost per provider
COST_OPENAI=$(grep 'wordflux_chat_cost_usd_daily{provider="openai"}' "$METRICS_FILE" | awk '{print $2}')
COST_ANTHROPIC=$(grep 'wordflux_chat_cost_usd_daily{provider="anthropic"}' "$METRICS_FILE" | awk '{print $2}')

# ========================================================================
# Calculate derived metrics
# ========================================================================

echo -e "${BLUE}[1/4] Tool-Use Success Rate${NC}"
echo "----------------------------------------------------------------------"

if [ "$REQUESTS_OPENAI" -gt 0 ]; then
    TOOL_SUCCESS_OPENAI=$(echo "scale=2; ($TOOLS_OPENAI / $REQUESTS_OPENAI) * 100" | bc)
else
    TOOL_SUCCESS_OPENAI=0
fi

if [ "$REQUESTS_ANTHROPIC" -gt 0 ]; then
    TOOL_SUCCESS_ANTHROPIC=$(echo "scale=2; ($TOOLS_ANTHROPIC / $REQUESTS_ANTHROPIC) * 100" | bc)
else
    TOOL_SUCCESS_ANTHROPIC=0
fi

echo "OpenAI:     ${TOOL_SUCCESS_OPENAI}% ($TOOLS_OPENAI / $REQUESTS_OPENAI requests)"
echo "Anthropic:  ${TOOL_SUCCESS_ANTHROPIC}% ($TOOLS_ANTHROPIC / $REQUESTS_ANTHROPIC requests)"

# Compare (threshold: Anthropic >= OpenAI - 5%)
DIFF_TOOL=$(echo "$TOOL_SUCCESS_ANTHROPIC - $TOOL_SUCCESS_OPENAI" | bc)
if (( $(echo "$DIFF_TOOL >= -5.0" | bc -l) )); then
    echo -e "${GREEN}✓ Anthropic meets threshold (>= OpenAI - 5%)${NC}"
    SCORE_TOOL_ANTHROPIC=0.3
    SCORE_TOOL_OPENAI=0.0
else
    echo -e "${YELLOW}⚠ OpenAI wins (Anthropic < OpenAI - 5%)${NC}"
    SCORE_TOOL_OPENAI=0.3
    SCORE_TOOL_ANTHROPIC=0.0
fi
echo ""

echo -e "${BLUE}[2/4] Approval Rate${NC}"
echo "----------------------------------------------------------------------"
echo "Note: Using tool success as proxy (actual approval rate requires additional instrumentation)"
APPROVAL_OPENAI=$TOOL_SUCCESS_OPENAI
APPROVAL_ANTHROPIC=$TOOL_SUCCESS_ANTHROPIC
echo "OpenAI:     ${APPROVAL_OPENAI}%"
echo "Anthropic:  ${APPROVAL_ANTHROPIC}%"

DIFF_APPROVAL=$(echo "$APPROVAL_ANTHROPIC - $APPROVAL_OPENAI" | bc)
if (( $(echo "$DIFF_APPROVAL >= -3.0" | bc -l) )); then
    echo -e "${GREEN}✓ Anthropic meets threshold (>= OpenAI - 3%)${NC}"
    SCORE_APPROVAL_ANTHROPIC=0.2
    SCORE_APPROVAL_OPENAI=0.0
else
    echo -e "${YELLOW}⚠ OpenAI wins (Anthropic < OpenAI - 3%)${NC}"
    SCORE_APPROVAL_OPENAI=0.2
    SCORE_APPROVAL_ANTHROPIC=0.0
fi
echo ""

echo -e "${BLUE}[3/4] P95 Latency${NC}"
echo "----------------------------------------------------------------------"

# Default to 0 if empty
P95_OPENAI=${P95_OPENAI:-0}
P95_ANTHROPIC=${P95_ANTHROPIC:-0}

echo "OpenAI:     ${P95_OPENAI}s"
echo "Anthropic:  ${P95_ANTHROPIC}s"

# Compare (threshold: Anthropic <= OpenAI + 2s)
DIFF_LATENCY=$(echo "$P95_ANTHROPIC - $P95_OPENAI" | bc)
if (( $(echo "$DIFF_LATENCY <= 2.0" | bc -l) )); then
    echo -e "${GREEN}✓ Anthropic meets threshold (<= OpenAI + 2s)${NC}"
    SCORE_LATENCY_ANTHROPIC=0.25
    SCORE_LATENCY_OPENAI=0.0
else
    echo -e "${YELLOW}⚠ OpenAI wins (Anthropic > OpenAI + 2s)${NC}"
    SCORE_LATENCY_OPENAI=0.25
    SCORE_LATENCY_ANTHROPIC=0.0
fi
echo ""

echo -e "${BLUE}[4/4] Cost per Message${NC}"
echo "----------------------------------------------------------------------"

# Default to 0 if empty
COST_OPENAI=${COST_OPENAI:-0}
COST_ANTHROPIC=${COST_ANTHROPIC:-0}

if [ "$REQUESTS_OPENAI" -gt 0 ]; then
    CPM_OPENAI=$(echo "scale=6; $COST_OPENAI / $REQUESTS_OPENAI" | bc)
else
    CPM_OPENAI=0
fi

if [ "$REQUESTS_ANTHROPIC" -gt 0 ]; then
    CPM_ANTHROPIC=$(echo "scale=6; $COST_ANTHROPIC / $REQUESTS_ANTHROPIC" | bc)
else
    CPM_ANTHROPIC=0
fi

echo "OpenAI:     \$$CPM_OPENAI/msg (total: \$$COST_OPENAI)"
echo "Anthropic:  \$$CPM_ANTHROPIC/msg (total: \$$COST_ANTHROPIC)"

# Compare (threshold: Anthropic <= OpenAI × 1.3)
THRESHOLD_COST=$(echo "$CPM_OPENAI * 1.3" | bc)
if (( $(echo "$CPM_ANTHROPIC <= $THRESHOLD_COST" | bc -l) )); then
    echo -e "${GREEN}✓ Anthropic meets threshold (<= OpenAI × 1.3)${NC}"
    SCORE_COST_ANTHROPIC=0.25
    SCORE_COST_OPENAI=0.0
else
    echo -e "${YELLOW}⚠ OpenAI wins (Anthropic > OpenAI × 1.3)${NC}"
    SCORE_COST_OPENAI=0.25
    SCORE_COST_ANTHROPIC=0.0
fi
echo ""

# ========================================================================
# Calculate total scores
# ========================================================================

TOTAL_OPENAI=$(echo "scale=2; $SCORE_TOOL_OPENAI + $SCORE_APPROVAL_OPENAI + $SCORE_LATENCY_OPENAI + $SCORE_COST_OPENAI" | bc)
TOTAL_ANTHROPIC=$(echo "scale=2; $SCORE_TOOL_ANTHROPIC + $SCORE_APPROVAL_ANTHROPIC + $SCORE_LATENCY_ANTHROPIC + $SCORE_COST_ANTHROPIC" | bc)

echo "========================================================================"
echo "SCORECARD SUMMARY"
echo "========================================================================"
echo ""
printf "%-25s %10s %10s %10s\n" "Metric" "Weight" "OpenAI" "Anthropic"
echo "------------------------------------------------------------------------"
printf "%-25s %10s %10.2f %10.2f\n" "Tool Success Rate" "30%" "$SCORE_TOOL_OPENAI" "$SCORE_TOOL_ANTHROPIC"
printf "%-25s %10s %10.2f %10.2f\n" "Approval Rate" "20%" "$SCORE_APPROVAL_OPENAI" "$SCORE_APPROVAL_ANTHROPIC"
printf "%-25s %10s %10.2f %10.2f\n" "P95 Latency" "25%" "$SCORE_LATENCY_OPENAI" "$SCORE_LATENCY_ANTHROPIC"
printf "%-25s %10s %10.2f %10.2f\n" "Cost per Message" "25%" "$SCORE_COST_OPENAI" "$SCORE_COST_ANTHROPIC"
echo "------------------------------------------------------------------------"
printf "%-25s %10s %10.2f %10.2f\n" "TOTAL SCORE" "100%" "$TOTAL_OPENAI" "$TOTAL_ANTHROPIC"
echo ""

# ========================================================================
# Decision
# ========================================================================

echo "========================================================================"
echo "DECISION"
echo "========================================================================"
echo ""

if (( $(echo "$TOTAL_ANTHROPIC >= 0.6" | bc -l) )); then
    echo -e "${GREEN}✅ PROMOTE ANTHROPIC${NC}"
    echo ""
    echo "Anthropic score: $TOTAL_ANTHROPIC (>= 0.6 threshold)"
    echo ""
    echo "Action:"
    echo "  export WF_LLM_PROVIDER=anthropic"
    echo "  export WF_CANARY_PCT=0"
    echo "  sudo systemctl restart wordflux-{api,cockpit}"
    echo "  git tag v0.7.0-chat-anthropic && git push --tags"
    echo ""
    EXIT_CODE=0

elif (( $(echo "$TOTAL_ANTHROPIC < 0.4" | bc -l) )); then
    echo -e "${RED}❌ ROLLBACK (Keep OpenAI)${NC}"
    echo ""
    echo "Anthropic score: $TOTAL_ANTHROPIC (< 0.4 threshold)"
    echo ""
    echo "Action:"
    echo "  export WF_CANARY_PCT=0"
    echo "  sudo systemctl restart wordflux-{api,cockpit}"
    echo "  git tag v0.6.1-chat-fix && git push --tags"
    echo ""
    EXIT_CODE=1

else
    echo -e "${YELLOW}🔄 EXTEND TEST (48h more)${NC}"
    echo ""
    echo "Anthropic score: $TOTAL_ANTHROPIC (between 0.4 - 0.6)"
    echo "Result is inconclusive, need more data."
    echo ""
    echo "Action:"
    echo "  # Keep canary at 50%"
    echo "  # Collect metrics for 48h more"
    echo "  # Re-run scorecard"
    echo ""
    EXIT_CODE=2
fi

echo "========================================================================"
echo "END OF SCORECARD"
echo "========================================================================"

exit $EXIT_CODE