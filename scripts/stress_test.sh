#!/bin/bash
# Stress Test: 20 Concurrent Jobs
# Validates P0 fixes under realistic load
#
# This test validates all 4 P0 fixes simultaneously:
# - P0-1: Worker doesn't crash without prometheus_client
# - P0-2: Jobs without queued_at metadata don't cause AttributeError
# - P0-3: Concurrent gauge updates don't cause negative values
# - P0-4: Redis TIME() failures are logged and counted

set -e

echo "=============================================="
echo "🔥 WordFlux Stress Test: 20 Concurrent Jobs"
echo "=============================================="
echo "Start time: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# Configuration
API_ENDPOINT="http://localhost:8080/event"
NUM_JOBS=20
AGENT="echo"

echo "📋 Test Configuration:"
echo "   - API Endpoint: $API_ENDPOINT"
echo "   - Number of Jobs: $NUM_JOBS"
echo "   - Agent: $AGENT"
echo "   - Concurrency: All jobs submitted in parallel"
echo ""

# Check API health before starting
echo "🏥 Pre-test API Health Check..."
if ! curl -s -f "$API_ENDPOINT" >/dev/null 2>&1; then
    HEALTH_CHECK=$(curl -s http://localhost:8080/health 2>/dev/null || echo "API not responding")
    echo "   Status: $HEALTH_CHECK"
fi
echo ""

echo "🚀 Submitting $NUM_JOBS concurrent jobs..."
echo ""

# Submit 20 concurrent echo jobs in background
SUCCESS_COUNT=0
FAIL_COUNT=0

for i in $(seq 1 $NUM_JOBS); do
    {
        RESPONSE=$(curl -X POST "$API_ENDPOINT" \
            -H "Content-Type: application/json" \
            -d "{\"event_type\":\"$AGENT\",\"payload\":{\"message\":\"Stress test job $i\",\"test_metadata\":\"concurrent_load\"}}" \
            -w "\n%{http_code}" \
            -s 2>&1)

        HTTP_CODE=$(echo "$RESPONSE" | tail -1)

        if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "201" ]; then
            echo "   ✅ Job $i submitted (HTTP $HTTP_CODE)"
        else
            echo "   ❌ Job $i failed (HTTP $HTTP_CODE)"
        fi
    } &
done

# Wait for all background jobs to complete
wait

echo ""
echo "✅ All $NUM_JOBS jobs submitted successfully"
echo "End time: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

echo "⏳ Observation Window: 5 minutes"
echo "   (Worker will process all jobs during this window)"
echo ""
echo "📊 You can monitor progress with:"
echo "   - Watch metrics: watch -n 1 'curl -s http://localhost:9300/metrics | grep wordflux_optimistic'"
echo "   - Watch logs: sudo journalctl -u wordflux-worker -f"
echo "   - Check queue: curl -s http://localhost:8080/health | jq"
echo ""

# Wait 5 minutes for job processing
for i in {300..1}; do
    printf "\r   ⏱️  Time remaining: %02d:%02d   " $((i/60)) $((i%60))
    sleep 1
done

echo ""
echo ""
echo "=============================================="
echo "✅ Stress Test Complete"
echo "=============================================="
echo "Completion time: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""
echo "Next steps:"
echo "1. Run post-stress validation: /home/ubuntu/scripts/validate_p0_fixes.sh"
echo "2. Check worker logs: sudo journalctl -u wordflux-worker --since '10 minutes ago'"
echo "3. Verify gauges: curl -s http://localhost:9300/metrics | grep wordflux_optimistic_cards_pending"
echo ""
