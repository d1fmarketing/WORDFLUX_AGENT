#!/bin/bash
# WordFlux Cockpit - Deployment Test Script
# Tests all critical endpoints and functionality

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
BASE_URL="http://localhost:8080"
NGINX_URL="http://localhost"

echo -e "${BLUE}======================================${NC}"
echo -e "${BLUE}WordFlux Cockpit - Deployment Testing${NC}"
echo -e "${BLUE}======================================${NC}\n"

# Function to test endpoint
test_endpoint() {
    local method=$1
    local url=$2
    local data=$3
    local description=$4

    echo -e "${YELLOW}Testing: ${description}${NC}"

    if [ "$method" = "GET" ]; then
        response=$(curl -s -w "\n%{http_code}" "$url")
    else
        response=$(curl -s -X "$method" -H "Content-Type: application/json" -d "$data" -w "\n%{http_code}" "$url")
    fi

    http_code=$(echo "$response" | tail -n 1)
    body=$(echo "$response" | head -n -1)

    if [ "$http_code" -eq 200 ]; then
        echo -e "${GREEN}✓ $description (HTTP $http_code)${NC}"
        return 0
    else
        echo -e "${RED}✗ $description failed (HTTP $http_code)${NC}"
        echo "Response: $body"
        return 1
    fi
}

# Track test results
PASSED=0
FAILED=0

echo -e "${BLUE}1. Testing Health & Status${NC}"
echo "=============================="

# Health check
if test_endpoint GET "${BASE_URL}/health" "" "Health check"; then
    ((PASSED++))
else
    ((FAILED++))
fi

# Queue status
if test_endpoint GET "${BASE_URL}/queue/status" "" "Queue status"; then
    ((PASSED++))
else
    ((FAILED++))
fi

echo -e "\n${BLUE}2. Testing Board Operations${NC}"
echo "================================"

# Get board state
if test_endpoint GET "${BASE_URL}/board/state" "" "Get board state"; then
    ((PASSED++))
else
    ((FAILED++))
fi

# Create a test card
CARD_DATA='{"title":"Test Card '$(date +%s)'","meta":{"assignee":"test","priority":"normal"}}'
CREATE_RESPONSE=$(curl -s -X POST -H "Content-Type: application/json" -d "$CARD_DATA" "${BASE_URL}/board/card")
CARD_ID=$(echo "$CREATE_RESPONSE" | grep -o '"id":"[^"]*' | head -1 | cut -d'"' -f4)

if [ -n "$CARD_ID" ]; then
    echo -e "${GREEN}✓ Created card: $CARD_ID${NC}"
    ((PASSED++))
else
    echo -e "${RED}✗ Failed to create card${NC}"
    ((FAILED++))
fi

# Move card if created
if [ -n "$CARD_ID" ]; then
    MOVE_DATA='{"card_id":"'$CARD_ID'","to":"In Progress"}'
    if test_endpoint POST "${BASE_URL}/board/move" "$MOVE_DATA" "Move card to In Progress"; then
        ((PASSED++))
    else
        ((FAILED++))
    fi
fi

echo -e "\n${BLUE}3. Testing Agent Operations${NC}"
echo "================================"

# Get suggestions
if [ -n "$CARD_ID" ]; then
    if test_endpoint GET "${BASE_URL}/agent/suggest?card_id=$CARD_ID" "" "Get action suggestions"; then
        ((PASSED++))
    else
        ((FAILED++))
    fi

    # Execute action
    ACTION_DATA='{"card_id":"'$CARD_ID'","action":"send_for_review"}'
    if test_endpoint POST "${BASE_URL}/agent/act" "$ACTION_DATA" "Execute agent action"; then
        echo -e "${GREEN}✓ Job queued successfully${NC}"
        ((PASSED++))
    else
        ((FAILED++))
    fi
fi

# Toggle autopilot
AUTOPILOT_DATA='{"on":false}'
if test_endpoint POST "${BASE_URL}/agent/autopilot" "$AUTOPILOT_DATA" "Toggle autopilot mode"; then
    ((PASSED++))
else
    ((FAILED++))
fi

echo -e "\n${BLUE}4. Testing KPI Endpoints${NC}"
echo "============================"

# Completed tasks KPI
if test_endpoint GET "${BASE_URL}/kpis/completed?days=7" "" "Get completed tasks (7 days)"; then
    ((PASSED++))
else
    ((FAILED++))
fi

# Efficiency KPI
if test_endpoint GET "${BASE_URL}/kpis/efficiency" "" "Get efficiency metrics"; then
    ((PASSED++))
else
    ((FAILED++))
fi

# Daily plan KPI
if test_endpoint GET "${BASE_URL}/kpis/daily_plan" "" "Get daily plan"; then
    ((PASSED++))
else
    ((FAILED++))
fi

echo -e "\n${BLUE}5. Testing AI/Chat Endpoints${NC}"
echo "================================="

# Get my tasks
if test_endpoint GET "${BASE_URL}/me/tasks?scope=all&when=today" "" "Get user tasks"; then
    ((PASSED++))
else
    ((FAILED++))
fi

# Plan tomorrow (AI)
PLAN_DATA='{"user":"test"}'
if test_endpoint POST "${BASE_URL}/ai/plan_tomorrow" "$PLAN_DATA" "AI plan tomorrow"; then
    ((PASSED++))
else
    ((FAILED++))
fi

# Cleanup cards
CLEANUP_DATA='{"state":"Published","days_old":30}'
if test_endpoint POST "${BASE_URL}/cards/cleanup" "$CLEANUP_DATA" "Cleanup old cards"; then
    ((PASSED++))
else
    ((FAILED++))
fi

echo -e "\n${BLUE}6. Testing Event Streaming${NC}"
echo "==============================="

# Test SSE endpoint (timeout after 2 seconds)
echo -e "${YELLOW}Testing: SSE stream connection${NC}"
SSE_TEST=$(timeout 2 curl -s -N "${BASE_URL}/events/stream" 2>&1 | head -n 1)
if [[ "$SSE_TEST" == *"data:"* ]] || [[ "$SSE_TEST" == *":"* ]]; then
    echo -e "${GREEN}✓ SSE stream working${NC}"
    ((PASSED++))
else
    echo -e "${RED}✗ SSE stream not responding${NC}"
    ((FAILED++))
fi

# Get recent events
if test_endpoint GET "${BASE_URL}/events/recent" "" "Get recent events"; then
    ((PASSED++))
else
    ((FAILED++))
fi

echo -e "\n${BLUE}7. Testing Redis Integration${NC}"
echo "================================"

# Check Redis connectivity
echo -e "${YELLOW}Testing: Redis connectivity${NC}"
if redis-cli ping > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Redis is responsive${NC}"
    ((PASSED++))

    # Check for board data
    BOARD_KEYS=$(redis-cli KEYS "wf:board:*" | wc -l)
    echo -e "${GREEN}✓ Found $BOARD_KEYS board-related keys in Redis${NC}"

    # Check for events
    EVENT_COUNT=$(redis-cli LLEN wf:events:recent)
    echo -e "${GREEN}✓ Recent events list has $EVENT_COUNT items${NC}"
else
    echo -e "${RED}✗ Redis not responding${NC}"
    ((FAILED++))
fi

echo -e "\n${BLUE}8. Testing Nginx Proxy${NC}"
echo "=========================="

# Test via Nginx (if configured)
if curl -s "$NGINX_URL" > /dev/null 2>&1; then
    if test_endpoint GET "${NGINX_URL}/health" "" "Health via Nginx"; then
        ((PASSED++))
        echo -e "${GREEN}✓ Nginx proxy working${NC}"
    else
        ((FAILED++))
    fi
else
    echo -e "${YELLOW}⚠ Nginx not configured or not accessible${NC}"
fi

# Summary
echo -e "\n${BLUE}======================================${NC}"
echo -e "${BLUE}Test Summary${NC}"
echo -e "${BLUE}======================================${NC}"
echo -e "${GREEN}Passed: $PASSED${NC}"
echo -e "${RED}Failed: $FAILED${NC}"

TOTAL=$((PASSED + FAILED))
if [ $FAILED -eq 0 ]; then
    echo -e "\n${GREEN}✅ All tests passed! ($PASSED/$TOTAL)${NC}"
    echo -e "${GREEN}The cockpit is ready for use.${NC}"
    exit 0
else
    echo -e "\n${RED}❌ Some tests failed ($FAILED/$TOTAL)${NC}"
    echo -e "${YELLOW}Please check the failed tests above.${NC}"
    exit 1
fi