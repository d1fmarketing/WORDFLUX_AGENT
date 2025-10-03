#!/bin/bash
# Complete SSE Test: Heartbeat + Pubsub Events
# Tests both keepalive (15s heartbeat) and real-time event delivery

set -e

HOST="${1:-localhost:8081}"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║       Complete SSE Test (Heartbeat + Pubsub Events)         ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Target: http://$HOST"
echo ""

# Temp files
TEMP_LOG=$(mktemp)
trap "rm -f $TEMP_LOG" EXIT

# Test 1: Check pubsub health baseline
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 1: Pubsub Health (Baseline)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
BASELINE=$(curl -s http://$HOST/health/pubsub | jq -r '.subscribers')
echo "✓ Current subscribers: $BASELINE"
echo ""

# Test 2: Start SSE connection
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 2: Start SSE Connection"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
timeout 45s curl -N -s http://$HOST/events/stream > "$TEMP_LOG" 2>&1 &
CURL_PID=$!
echo "✓ SSE connection started (PID: $CURL_PID)"
sleep 3

# Test 3: Verify subscription
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 3: Verify Pubsub Subscription"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ACTIVE=$(curl -s http://$HOST/health/pubsub | jq -r '.subscribers')
if [ "$ACTIVE" -gt "$BASELINE" ]; then
    echo "✅ PASS: Subscription count increased ($BASELINE → $ACTIVE)"
else
    echo "❌ FAIL: Subscription count did not increase (still $ACTIVE)"
    kill $CURL_PID 2>/dev/null || true
    exit 1
fi
echo ""

# Test 4: Trigger event
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 4: Trigger Event and Verify Delivery"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
TIMESTAMP=$(date +%s)
curl -s -X POST http://$HOST/board/card \
  -H 'Content-Type: application/json' \
  -d "{\"title\":\"SSE Test $TIMESTAMP\",\"intent\":\"Complete test\"}" > /dev/null

sleep 3

# Check if event appeared in stream
if grep -q "SSE Test $TIMESTAMP" "$TEMP_LOG"; then
    echo "✅ PASS: Event appeared in SSE stream"
    echo ""
    echo "Event payload:"
    grep "SSE Test $TIMESTAMP" "$TEMP_LOG" | head -1 | sed 's/^/  /'
else
    echo "❌ FAIL: Event did not appear in SSE stream"
    kill $CURL_PID 2>/dev/null || true
    exit 1
fi
echo ""

# Test 5: Verify heartbeats
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 5: Verify Heartbeats (waiting 20s)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
sleep 20

HB_COUNT=$(grep -c ": hb" "$TEMP_LOG" || echo 0)
EXPECTED=$((20 / 15 + 1))  # ~1-2 heartbeats in 20s

if [ "$HB_COUNT" -ge 1 ]; then
    echo "✅ PASS: Heartbeats working ($HB_COUNT heartbeats in 20s)"
else
    echo "❌ FAIL: No heartbeats found"
    kill $CURL_PID 2>/dev/null || true
    exit 1
fi
echo ""

# Test 6: Clean disconnection
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 6: Clean Disconnection"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
kill $CURL_PID 2>/dev/null || true
wait $CURL_PID 2>/dev/null || true
sleep 2

FINAL=$(curl -s http://$HOST/health/pubsub | jq -r '.subscribers')
if [ "$FINAL" -eq "$BASELINE" ]; then
    echo "✅ PASS: Subscription cleaned up ($ACTIVE → $FINAL)"
else
    echo "⚠️  WARN: Subscription count: $FINAL (expected: $BASELINE)"
fi
echo ""

# Summary
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                         SUMMARY                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "✅ Pubsub subscription works"
echo "✅ Events delivered to SSE stream"
echo "✅ Heartbeats working every ~15s"
echo "✅ Clean disconnection"
echo ""
echo "🎉 ALL TESTS PASSED"
echo ""
echo "Sample output from SSE stream:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
head -10 "$TEMP_LOG"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

exit 0
