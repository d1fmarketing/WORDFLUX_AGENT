#!/bin/bash
# SSE Keepalive Test Script
# Tests that /events/stream maintains connection with 15s heartbeats

set -e

HOST="${1:-localhost:8081}"
DURATION="${2:-60}"

echo "╔════════════════════════════════════════════════════════════╗"
echo "║           SSE Keepalive Test (15s heartbeat)               ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "Target: http://$HOST/events/stream"
echo "Duration: ${DURATION}s"
echo "Expected heartbeats: ~$((DURATION / 15))"
echo ""

# Create temp file
TEMP_LOG=$(mktemp)
trap "rm -f $TEMP_LOG" EXIT

# Start SSE connection
echo "Starting SSE stream..."
timeout ${DURATION}s curl -N -s http://$HOST/events/stream > "$TEMP_LOG" 2>&1 &
CURL_PID=$!

# Wait for duration
sleep $DURATION

# Kill if still running
kill $CURL_PID 2>/dev/null || true
wait $CURL_PID 2>/dev/null || true

# Count heartbeats
HB_COUNT=$(grep -c ': hb' "$TEMP_LOG" || echo 0)
EXPECTED=$((DURATION / 15))

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║                        RESULTS                             ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "✓ Heartbeats received: $HB_COUNT"
echo "✓ Expected (±1): ~$EXPECTED"
echo ""

# Check if within expected range
MIN_HB=$((EXPECTED - 1))
MAX_HB=$((EXPECTED + 2))

if [ "$HB_COUNT" -ge "$MIN_HB" ] && [ "$HB_COUNT" -le "$MAX_HB" ]; then
    echo "✅ PASS: Heartbeat interval is working correctly (~15s)"
    echo ""
    echo "Sample output:"
    head -10 "$TEMP_LOG"
    exit 0
else
    echo "❌ FAIL: Heartbeat count ($HB_COUNT) outside expected range ($MIN_HB-$MAX_HB)"
    echo ""
    echo "Full output:"
    cat "$TEMP_LOG"
    exit 1
fi
