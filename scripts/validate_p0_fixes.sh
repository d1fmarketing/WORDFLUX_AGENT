#!/bin/bash
# P0 Fix Validation Suite
# Validates all 4 P0 bug fixes are working correctly

echo "=== P0 Fix Validation Suite ==="
echo "Started: $(date)"
echo ""

PASS_COUNT=0
FAIL_COUNT=0

# Test 1: Worker Stability (P0-1 - No crashes without prometheus_client)
echo "[Test 1] Worker Stability - Check for crashes"
CRASHES=$(sudo journalctl -u wordflux-worker --since "10 minutes ago" | grep -c "Traceback")
if [ "$CRASHES" -eq 0 ]; then
  echo "✅ PASS: No worker crashes in last 10 minutes"
  ((PASS_COUNT++))
else
  echo "❌ FAIL: Found $CRASHES crashes"
  ((FAIL_COUNT++))
fi
echo ""

# Test 2: Job Metadata Validation (P0-2 - No queued_at missing errors)
echo "[Test 2] Job Metadata Validation - Check for missing queued_at"
METADATA_ERRORS=$(sudo journalctl -u wordflux-worker --since "10 minutes ago" | grep -c "queued_at.*missing")
if [ "$METADATA_ERRORS" -eq 0 ]; then
  echo "✅ PASS: No metadata validation errors"
  ((PASS_COUNT++))
else
  echo "❌ FAIL: Found $METADATA_ERRORS metadata errors"
  ((FAIL_COUNT++))
fi
echo ""

# Test 3: Gauge Drift Prevention (P0-3 - Gauge remains non-negative)
echo "[Test 3] Gauge Drift Prevention - Check gauge values"
GAUGE_VALUES=$(curl -s http://localhost:9300/metrics 2>/dev/null | grep 'wordflux_optimistic_cards_pending{' | awk '{print $2}')
NEGATIVE_GAUGES=0
for value in $GAUGE_VALUES; do
  if (( $(echo "$value < 0" | bc -l 2>/dev/null || echo 0) )); then
    ((NEGATIVE_GAUGES++))
  fi
done

if [ "$NEGATIVE_GAUGES" -eq 0 ]; then
  echo "✅ PASS: All gauges non-negative"
  ((PASS_COUNT++))
else
  echo "❌ FAIL: Found $NEGATIVE_GAUGES negative gauge values"
  ((FAIL_COUNT++))
fi
echo ""

# Test 4: Clock Skew Observability (P0-4 - Metric registered)
echo "[Test 4] Clock Skew Observability - Check metric exists"
CLOCK_METRIC=$(curl -s http://localhost:9300/metrics 2>/dev/null | grep -c "wordflux_clock_skew_fallback_total")
if [ "$CLOCK_METRIC" -gt 0 ]; then
  FALLBACK_COUNT=$(curl -s http://localhost:9300/metrics 2>/dev/null | grep "wordflux_clock_skew_fallback_total" | awk '{print $2}' | head -1)
  echo "✅ PASS: Clock skew metric registered (count: ${FALLBACK_COUNT:-0})"
  ((PASS_COUNT++))
else
  echo "❌ FAIL: Clock skew metric not found"
  ((FAIL_COUNT++))
fi
echo ""

# Summary
echo "=== Validation Complete: $(date) ==="
echo "Results: $PASS_COUNT passed, $FAIL_COUNT failed"
echo ""

if [ "$FAIL_COUNT" -eq 0 ]; then
  echo "✅ ALL TESTS PASSED - P0 fixes validated"
  exit 0
else
  echo "❌ SOME TESTS FAILED - Investigate issues"
  exit 1
fi
