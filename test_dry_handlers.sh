#!/bin/bash
# Verify Dry Handler Pattern - No Direct Board API Calls
# Ensures all tool handlers follow: Job → Queue → SSE pattern

set -e

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║      Dry Handler Pattern Verification                    ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "Verifying agent→queue→SSE→board flow..."
echo ""

VIOLATIONS=0

# Check 1: No /board/write calls anywhere
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "CHECK 1: No /board/write calls in handlers"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

BOARD_WRITE_MATCHES=$(grep -r "/board/write" src/ 2>/dev/null | grep -v ".pyc" | grep -v "__pycache__" || true)

if [ -z "$BOARD_WRITE_MATCHES" ]; then
    echo "✅ PASS: Zero /board/write calls found"
else
    echo "❌ FAIL: Found /board/write calls:"
    echo "$BOARD_WRITE_MATCHES"
    VIOLATIONS=$((VIOLATIONS + 1))
fi
echo ""

# Check 2: No HTTP requests to board in chat.py
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "CHECK 2: No HTTP requests to board in chat.py"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

CHAT_HTTP_MATCHES=$(grep -E "requests\.(post|put|patch|delete)" src/api/chat.py 2>/dev/null || true)

if [ -z "$CHAT_HTTP_MATCHES" ]; then
    echo "✅ PASS: No HTTP requests in chat.py"
else
    echo "❌ FAIL: Found HTTP requests in chat.py:"
    echo "$CHAT_HTTP_MATCHES"
    VIOLATIONS=$((VIOLATIONS + 1))
fi
echo ""

# Check 3: No HTTP requests in board_operator.py
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "CHECK 3: No HTTP requests in board_operator.py"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

BOARD_OP_HTTP_MATCHES=$(grep -E "requests\." src/agents/board_operator.py 2>/dev/null || true)

if [ -z "$BOARD_OP_HTTP_MATCHES" ]; then
    echo "✅ PASS: No HTTP requests in board_operator.py"
else
    echo "❌ FAIL: Found HTTP requests in board_operator.py:"
    echo "$BOARD_OP_HTTP_MATCHES"
    VIOLATIONS=$((VIOLATIONS + 1))
fi
echo ""

# Check 4: execute_tool_call uses queue.publish()
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "CHECK 4: execute_tool_call() uses queue.publish()"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

QUEUE_PUBLISH_COUNT=$(grep "queue\.publish" src/api/chat.py | wc -l)

if [ "$QUEUE_PUBLISH_COUNT" -ge 6 ]; then
    echo "✅ PASS: Found $QUEUE_PUBLISH_COUNT queue.publish() calls (expected ≥6)"
else
    echo "❌ FAIL: Found only $QUEUE_PUBLISH_COUNT queue.publish() calls (expected ≥6)"
    VIOLATIONS=$((VIOLATIONS + 1))
fi
echo ""

# Check 5: emit_sse_event uses Redis pub/sub
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "CHECK 5: emit_sse_event() uses Redis pub/sub"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

SSE_REDIS_PUBLISH=$(grep -A 30 "def emit_sse_event" src/api/chat.py | grep "\.publish" | wc -l)

if [ "$SSE_REDIS_PUBLISH" -ge 1 ]; then
    echo "✅ PASS: emit_sse_event() uses Redis pub/sub"
else
    echo "❌ FAIL: emit_sse_event() not using Redis pub/sub"
    VIOLATIONS=$((VIOLATIONS + 1))
fi
echo ""

# Check 6: board_operator imports cockpit helpers (not HTTP)
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "CHECK 6: board_operator uses cockpit helpers (not HTTP)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

COCKPIT_IMPORTS=$(grep "from wordflux_cockpit import" src/agents/board_operator.py | wc -l)

if [ "$COCKPIT_IMPORTS" -ge 4 ]; then
    echo "✅ PASS: board_operator imports cockpit helpers (found $COCKPIT_IMPORTS imports)"
else
    echo "❌ FAIL: board_operator missing cockpit helper imports"
    VIOLATIONS=$((VIOLATIONS + 1))
fi
echo ""

# Check 7: Job creation format check
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "CHECK 7: Job creation follows standard format"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

JOB_CREATION_COUNT=$(grep -E "Job\(" src/api/chat.py | wc -l)

if [ "$JOB_CREATION_COUNT" -ge 6 ]; then
    echo "✅ PASS: Found $JOB_CREATION_COUNT Job() instantiations"
else
    echo "❌ FAIL: Insufficient Job() instantiations (found $JOB_CREATION_COUNT)"
    VIOLATIONS=$((VIOLATIONS + 1))
fi
echo ""

# Check 8: No direct board state mutation in chat.py
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "CHECK 8: No direct board state mutation in chat.py"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

LPUSH_IN_CHAT=$(grep -E "\.lpush\(.*card" src/api/chat.py 2>/dev/null || true)

if [ -z "$LPUSH_IN_CHAT" ]; then
    echo "✅ PASS: No direct Redis LPUSH to card lists in chat.py"
else
    echo "❌ FAIL: Found direct board mutation in chat.py:"
    echo "$LPUSH_IN_CHAT"
    VIOLATIONS=$((VIOLATIONS + 1))
fi
echo ""

# Summary
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║                    SUMMARY                                ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""

if [ "$VIOLATIONS" -eq 0 ]; then
    echo "✅ ALL CHECKS PASSED ($((8)) checks, 0 violations)"
    echo ""
    echo "Dry Handler Pattern Status:"
    echo "  ✅ Zero /board/write API calls"
    echo "  ✅ Zero HTTP requests in chat handlers"
    echo "  ✅ Zero HTTP requests in board_operator"
    echo "  ✅ All handlers use queue.publish()"
    echo "  ✅ SSE events via Redis pub/sub"
    echo "  ✅ Board operations via cockpit helpers"
    echo "  ✅ Job creation follows standard pattern"
    echo "  ✅ No direct board state mutation"
    echo ""
    echo "🎉 System follows agent→queue→SSE→board flow!"
    echo ""
    echo "Architecture Flow:"
    echo "  User → Chat → Tool Call → Validation → Job"
    echo "                                           ↓"
    echo "  Frontend ← SSE ← Redis Pub/Sub ←— queue.publish()"
    echo "                                           ↓"
    echo "  Frontend ← SSE ← Cockpit Helpers ← board_operator ← Worker"
    exit 0
else
    echo "❌ VIOLATIONS DETECTED ($VIOLATIONS out of 8 checks failed)"
    echo ""
    echo "Please review the violations above and fix the following:"
    echo "  • Remove any /board/write API calls"
    echo "  • Replace HTTP requests with job queue"
    echo "  • Use emit_sse_event() for notifications"
    echo "  • Use cockpit helpers for board operations"
    echo ""
    exit 1
fi
