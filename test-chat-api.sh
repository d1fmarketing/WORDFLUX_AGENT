#!/bin/bash
#
# WordFlux Chat API - Smoke Test
# Testa os endpoints do chat agent com LLM
#
# Uso:
#   chmod +x test-chat-api.sh
#   ./test-chat-api.sh [BASE_URL]
#
# Exemplo:
#   ./test-chat-api.sh http://localhost:8080
#   ./test-chat-api.sh http://wordflux.3-228-174-188.nip.io:8080

set -e

BASE_URL="${1:-http://localhost:8080}"
SESSION_ID="smoke-$(date +%s)"

echo "=================================================="
echo "WordFlux Chat API - Smoke Test"
echo "=================================================="
echo "Base URL: $BASE_URL"
echo "Session ID: $SESSION_ID"
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Test 1: Health Check
echo -e "${YELLOW}[1/6]${NC} Testing health endpoint..."
curl -s "$BASE_URL/health" | jq . || echo -e "${RED}FAILED${NC}"
echo ""

# Test 2: Low-risk action (summarize - no approval)
echo -e "${YELLOW}[2/6]${NC} Testing LOW-RISK action (summarize tasks)..."
RESPONSE=$(curl -s -X POST "$BASE_URL/chat/" \
  -H "Content-Type: application/json" \
  -d "{
    \"message\": \"Mostrar minhas tarefas\",
    \"session_id\": \"$SESSION_ID\"
  }")

echo "$RESPONSE" | jq .
REQUIRES_APPROVAL=$(echo "$RESPONSE" | jq -r '.requires_approval')

if [ "$REQUIRES_APPROVAL" = "false" ]; then
  echo -e "${GREEN}✓ Low-risk action executed without approval${NC}"
else
  echo -e "${RED}✗ Expected requires_approval=false${NC}"
fi
echo ""

# Test 3: High-risk action (propose_move - requires approval)
echo -e "${YELLOW}[3/6]${NC} Testing HIGH-RISK action (move card to Produção)..."
RESPONSE=$(curl -s -X POST "$BASE_URL/chat/" \
  -H "Content-Type: application/json" \
  -d "{
    \"message\": \"Mover card c-test1234 para Produção\",
    \"session_id\": \"${SESSION_ID}-highrisk\"
  }")

echo "$RESPONSE" | jq .
REQUIRES_APPROVAL=$(echo "$RESPONSE" | jq -r '.requires_approval')
PROPOSAL_ID=$(echo "$RESPONSE" | jq -r '.proposal_id')

if [ "$REQUIRES_APPROVAL" = "true" ] && [ "$PROPOSAL_ID" != "null" ]; then
  echo -e "${GREEN}✓ High-risk action requires approval (proposal_id: $PROPOSAL_ID)${NC}"
else
  echo -e "${RED}✗ Expected requires_approval=true with proposal_id${NC}"
  PROPOSAL_ID=""
fi
echo ""

# Test 4: Approve proposal (if we got one)
if [ -n "$PROPOSAL_ID" ] && [ "$PROPOSAL_ID" != "null" ]; then
  echo -e "${YELLOW}[4/6]${NC} Testing approval endpoint..."
  APPROVAL_RESPONSE=$(curl -s -X POST "$BASE_URL/chat/approve" \
    -H "Content-Type: application/json" \
    -d "{
      \"proposal_id\": \"$PROPOSAL_ID\"
    }")

  echo "$APPROVAL_RESPONSE" | jq .
  JOB_ID=$(echo "$APPROVAL_RESPONSE" | jq -r '.job_id')

  if [ "$JOB_ID" != "null" ] && [ -n "$JOB_ID" ]; then
    echo -e "${GREEN}✓ Approval succeeded, job queued: $JOB_ID${NC}"
  else
    echo -e "${RED}✗ Approval failed or no job_id returned${NC}"
  fi
else
  echo -e "${YELLOW}[4/6]${NC} Skipping approval test (no proposal_id from previous test)"
fi
echo ""

# Test 5: Get conversation history
echo -e "${YELLOW}[5/6]${NC} Testing conversation history endpoint..."
HISTORY=$(curl -s "$BASE_URL/chat/history?session_id=$SESSION_ID")
echo "$HISTORY" | jq .
MESSAGE_COUNT=$(echo "$HISTORY" | jq -r '.count')

if [ "$MESSAGE_COUNT" -gt 0 ]; then
  echo -e "${GREEN}✓ History retrieved: $MESSAGE_COUNT messages${NC}"
else
  echo -e "${RED}✗ No messages in history${NC}"
fi
echo ""

# Test 6: Rate limiting test (optional - can be slow)
echo -e "${YELLOW}[6/6]${NC} Testing rate limiting (20 req/min)..."
echo "Sending 21 rapid requests..."

RATE_LIMITED=false
for i in {1..21}; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/chat/" \
    -H "Content-Type: application/json" \
    -d "{
      \"message\": \"Test $i\",
      \"session_id\": \"rate-limit-test-$SESSION_ID\"
    }")

  if [ "$STATUS" = "429" ]; then
    echo -e "${GREEN}✓ Rate limited after $i requests (HTTP 429)${NC}"
    RATE_LIMITED=true
    break
  fi
done

if [ "$RATE_LIMITED" = "false" ]; then
  echo -e "${YELLOW}⚠ Rate limiting not triggered (may need adjustment)${NC}"
fi
echo ""

# Summary
echo "=================================================="
echo "Smoke Test Complete"
echo "=================================================="
echo ""
echo "Next steps:"
echo "  1. Check SSE stream: curl -N $BASE_URL/events/stream"
echo "  2. Monitor Redis: redis-cli KEYS 'wf:chat:*'"
echo "  3. Check audit log: redis-cli LRANGE wf:chat:audit 0 10"
echo ""
echo "Environment variables:"
echo "  WF_LLM_PROVIDER: $(echo ${WF_LLM_PROVIDER:-mock})"
echo "  WF_RATELIMIT_PER_MIN: $(echo ${WF_RATELIMIT_PER_MIN:-20})"
echo "  WF_CHAT_TTL_SEC: $(echo ${WF_CHAT_TTL_SEC:-86400})"
echo ""