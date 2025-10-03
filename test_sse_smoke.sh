#!/bin/bash
################################################################################
# SSE Smoke Test - Production Validation Script
#
# Purpose: Validate Server-Sent Events (SSE) functionality in WordFlux system
# Duration: 60 seconds
# Success Criteria:
#   - Receive ≥3 heartbeats (: hb comments, every 15s)
#   - Receive ≥1 job_queued event (triggered by user via POST /chat)
#   - Reconnect once if connection drops
#
# Usage: ./test_sse_smoke.sh [--cockpit-url URL] [--chat-url URL]
#
# Exit Codes:
#   0 = Success (SSE OK)
#   1 = Failure (insufficient heartbeats or events)
#   2 = Connection failure (cannot reach SSE endpoint)
################################################################################

set -euo pipefail

################################################################################
# Configuration
################################################################################
COCKPIT_URL="${1:-http://localhost:8081}"
CHAT_URL="${2:-http://localhost:8080}"
SSE_URL="${COCKPIT_URL}/events/stream"
DURATION=60
MIN_HEARTBEATS=3
MIN_JOB_EVENTS=1
MAX_RETRIES=1

################################################################################
# State Variables
################################################################################
heartbeat_count=0
job_queued_count=0
connection_attempts=0
start_time=$(date +%s)

################################################################################
# Cleanup Handler
################################################################################
cleanup() {
    # Kill any background processes
    jobs -p | xargs -r kill 2>/dev/null || true
}
trap cleanup EXIT INT TERM

################################################################################
# Monitor SSE Stream
################################################################################
monitor_stream() {
    local line
    local data_line

    while IFS= read -r line; do
        # Remove carriage returns (Windows compatibility)
        line="${line%$'\r'}"

        # Heartbeat detection: ": hb" (colon + space + hb)
        if [[ "$line" == ": hb" ]]; then
            ((heartbeat_count++))
            echo "  [$(date +%H:%M:%S)] 💓 Heartbeat #$heartbeat_count received" >&2

        # Data event detection: "data: {...}"
        elif [[ "$line" == data:* ]]; then
            # Extract JSON (remove "data: " prefix)
            data_line="${line#data:}"
            data_line="${data_line# }"  # Remove leading space

            # Check for job_queued event
            if echo "$data_line" | grep -q '"kind":"job_queued"'; then
                ((job_queued_count++))
                echo "  [$(date +%H:%M:%S)] 🎯 job_queued event #$job_queued_count received" >&2
            fi

            # Log other event types (for debugging)
            if echo "$data_line" | grep -q '"kind"'; then
                event_kind=$(echo "$data_line" | grep -oP '"kind"\s*:\s*"\K[^"]+' || echo "unknown")
                if [[ "$event_kind" != "job_queued" ]]; then
                    echo "  [$(date +%H:%M:%S)] 📦 Event: $event_kind" >&2
                fi
            fi
        fi

        # Check if we've met success criteria early
        if [ $heartbeat_count -ge $MIN_HEARTBEATS ] && [ $job_queued_count -ge $MIN_JOB_EVENTS ]; then
            echo "  [$(date +%H:%M:%S)] ✅ Success criteria met early!" >&2
            # Continue monitoring until timeout (validate sustained connection)
        fi
    done
}

################################################################################
# Connect and Monitor with Retry
################################################################################
connect_and_monitor() {
    ((connection_attempts++))
    echo "  [$(date +%H:%M:%S)] 🔌 Connection attempt #$connection_attempts to $SSE_URL" >&2

    # Use timeout to enforce duration limit
    # -N: no buffer, -f: fail on HTTP errors, -s: silent, --max-time: connection timeout
    # 2>/dev/null suppresses curl progress, but we redirect stderr to see our messages
    timeout ${DURATION}s curl -N -f -s --max-time 10 "$SSE_URL" 2>/dev/null | monitor_stream

    # Capture curl exit code from pipeline
    local curl_exit_code=${PIPESTATUS[0]}
    return $curl_exit_code
}

################################################################################
# Main Execution
################################################################################
main() {
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "🧪 WordFlux SSE Smoke Test - Production Validation"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "📡 Monitoring: $SSE_URL"
    echo "⏱️  Duration: ${DURATION}s"
    echo "🎯 Success Criteria:"
    echo "   ✓ Receive ≥${MIN_HEARTBEATS} heartbeats (: hb comments, every ~15s)"
    echo "   ✓ Receive ≥${MIN_JOB_EVENTS} job_queued event (triggered by chat API)"
    echo "   ✓ Reconnect up to ${MAX_RETRIES} time(s) if connection drops"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "⚡ ACTION REQUIRED - Open a new terminal and run:"
    echo ""
    echo "  curl -X POST ${CHAT_URL}/chat/ \\"
    echo "    -H 'Content-Type: application/json' \\"
    echo "    -d '{\"message\": \"create test card\", \"session_id\": \"smoke-test\"}'"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    # Pre-flight check: Can we reach the SSE endpoint?
    echo "🔍 Pre-flight check..."
    if ! curl -f -s --max-time 5 "${COCKPIT_URL}/health" >/dev/null 2>&1; then
        echo "❌ FAILED: Cannot reach Cockpit health endpoint at ${COCKPIT_URL}/health"
        echo "   Is the Cockpit service running?"
        echo "   Try: systemctl status wordflux-cockpit"
        exit 2
    fi
    echo "✓ Cockpit health endpoint reachable"
    echo ""

    # Start monitoring
    echo "🚀 Starting SSE monitoring (${DURATION}s)..."
    echo ""

    # First connection attempt
    if ! connect_and_monitor; then
        local curl_exit_code=$?
        echo ""
        echo "  [$(date +%H:%M:%S)] ⚠️  Connection dropped (curl exit code: $curl_exit_code)" >&2

        # Retry if we haven't exceeded max retries
        if [ $connection_attempts -lt $((MAX_RETRIES + 1)) ]; then
            echo "  [$(date +%H:%M:%S)] 🔄 Reconnecting (attempt $((connection_attempts + 1))/$((MAX_RETRIES + 1)))..." >&2
            sleep 1

            # Second attempt (partial duration)
            local remaining=$((DURATION - ($(date +%s) - start_time)))
            if [ $remaining -gt 10 ]; then
                DURATION=$remaining connect_and_monitor || true
            else
                echo "  [$(date +%H:%M:%S)] ⏰ Insufficient time remaining for retry (${remaining}s)" >&2
            fi
        fi
    fi

    # Final validation
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "📊 Final Results"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "  Heartbeats: $heartbeat_count/$MIN_HEARTBEATS"
    echo "  Events:     $job_queued_count/$MIN_JOB_EVENTS (job_queued)"
    echo "  Connection attempts: $connection_attempts"
    echo ""

    # Success criteria
    if [ $heartbeat_count -ge $MIN_HEARTBEATS ] && [ $job_queued_count -ge $MIN_JOB_EVENTS ]; then
        echo "✅ SSE OK"
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        exit 0
    else
        echo "❌ FAILED"
        echo ""

        # Diagnostic messages
        if [ $heartbeat_count -lt $MIN_HEARTBEATS ]; then
            echo "   ⚠️  Insufficient heartbeats ($heartbeat_count/$MIN_HEARTBEATS)"
            if [ $heartbeat_count -eq 0 ]; then
                echo "      → SSE connection appears dead (no heartbeats at all)"
                echo "      → Check: Is SSE endpoint sending ': hb' comments every 15s?"
                echo "      → Check: Is nginx buffering SSE responses?"
            else
                echo "      → SSE connection unstable (some heartbeats received)"
                echo "      → Check: Network latency or packet loss?"
            fi
        fi

        if [ $job_queued_count -lt $MIN_JOB_EVENTS ]; then
            echo "   ⚠️  No job_queued events received ($job_queued_count/$MIN_JOB_EVENTS)"
            echo "      → Did you trigger the chat command?"
            echo "      → Check: Is chat API queueing jobs correctly?"
            echo "      → Check: Is Redis pub/sub working?"
        fi

        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        exit 1
    fi
}

# Execute main function
main "$@"
