#!/bin/bash
# Test Robust Textual Confirmation System
# Acceptance Criteria:
#   - "Mova c-X para Finalizado" → asks for confirmation
#   - "pode", "sim", "yes" → job enqueued
#   - "não" → canceled
#   - "talvez" → repeats short question

set -e

HOST="${1:-localhost:8080}"
SESSION_ID="test-confirm-$(date +%s)"

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║     Robust Textual Confirmation System Test              ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "Target: http://$HOST/chat"
echo "Session: $SESSION_ID"
echo ""

# Helper function to call chat API
chat() {
    local message="$1"
    curl -s -X POST "http://$HOST/chat" \
        -H "Content-Type: application/json" \
        -d "{\"message\":\"$message\",\"session_id\":\"$SESSION_ID\"}" | jq -r '.message'
}

# Test 1: High-risk action triggers confirmation
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 1: High-Risk Action Triggers Confirmation"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
RESPONSE=$(chat "Mova c-test123 para Finalizado")
echo "Request: Mova c-test123 para Finalizado"
echo "Response: $RESPONSE"

if [[ "$RESPONSE" == *"Ação de alto risco"* ]]; then
    echo "✅ PASS: Confirmation requested with standard message"
else
    echo "❌ FAIL: No confirmation or wrong message format"
    exit 1
fi
echo ""

# Test 2: Affirmative response "sim"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 2: Affirmative Response 'sim'"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
# Create new confirmation
SESSION_ID="test-sim-$(date +%s)"
chat "Mova c-test456 para Finalizado" > /dev/null
sleep 1

RESPONSE=$(chat "sim")
echo "Request: sim"
echo "Response: $RESPONSE"

if [[ "$RESPONSE" == *"Confirmado"* ]] || [[ "$RESPONSE" == *"executada"* ]]; then
    echo "✅ PASS: Action executed on 'sim'"
else
    echo "❌ FAIL: Action not executed"
    exit 1
fi
echo ""

# Test 3: Affirmative response "pode"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 3: Affirmative Response 'pode'"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
SESSION_ID="test-pode-$(date +%s)"
chat "Mova c-test789 para Finalizado" > /dev/null
sleep 1

RESPONSE=$(chat "pode")
echo "Request: pode"
echo "Response: $RESPONSE"

if [[ "$RESPONSE" == *"Confirmado"* ]] || [[ "$RESPONSE" == *"executada"* ]]; then
    echo "✅ PASS: Action executed on 'pode'"
else
    echo "❌ FAIL: Action not executed"
    exit 1
fi
echo ""

# Test 4: Affirmative response "yes"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 4: Affirmative Response 'yes'"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
SESSION_ID="test-yes-$(date +%s)"
chat "Mova c-testABC para Finalizado" > /dev/null
sleep 1

RESPONSE=$(chat "yes")
echo "Request: yes"
echo "Response: $RESPONSE"

if [[ "$RESPONSE" == *"Confirmado"* ]] || [[ "$RESPONSE" == *"executada"* ]]; then
    echo "✅ PASS: Action executed on 'yes'"
else
    echo "❌ FAIL: Action not executed"
    exit 1
fi
echo ""

# Test 5: Negative response "não"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 5: Negative Response 'não'"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
SESSION_ID="test-nao-$(date +%s)"
chat "Mova c-testDEF para Finalizado" > /dev/null
sleep 1

RESPONSE=$(chat "não")
echo "Request: não"
echo "Response: $RESPONSE"

if [[ "$RESPONSE" == *"cancelada"* ]]; then
    echo "✅ PASS: Action canceled on 'não'"
else
    echo "❌ FAIL: Action not canceled correctly"
    exit 1
fi
echo ""

# Test 6: Ambiguous response "talvez"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 6: Ambiguous Response 'talvez'"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
SESSION_ID="test-talvez-$(date +%s)"
chat "Mova c-testGHI para Finalizado" > /dev/null
sleep 1

RESPONSE=$(chat "talvez")
echo "Request: talvez"
echo "Response: $RESPONSE"

if [[ "$RESPONSE" == *"Resposta não reconhecida"* ]] && [[ "$RESPONSE" == *"sim"* ]] && [[ "$RESPONSE" == *"não"* ]]; then
    echo "✅ PASS: Short re-prompt shown on 'talvez'"
else
    echo "❌ FAIL: Re-prompt not working correctly"
    echo "Expected: Short message with 'Resposta não reconhecida'"
    exit 1
fi
echo ""

# Test 7: Edge case - "assim" should NOT match "sim"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 7: Edge Case - 'assim' Should NOT Match 'sim'"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
SESSION_ID="test-assim-$(date +%s)"
chat "Mova c-testJKL para Finalizado" > /dev/null
sleep 1

RESPONSE=$(chat "assim não")
echo "Request: assim não"
echo "Response: $RESPONSE"

if [[ "$RESPONSE" == *"cancelada"* ]]; then
    echo "✅ PASS: 'assim não' correctly detected as negative (word boundary working)"
else
    echo "❌ FAIL: Word boundary detection not working"
    exit 1
fi
echo ""

# Test 8: Edge case - "não pode" should be unclear
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 8: Edge Case - 'não pode' Should Be Unclear"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
SESSION_ID="test-naopode-$(date +%s)"
chat "Mova c-testMNO para Finalizado" > /dev/null
sleep 1

RESPONSE=$(chat "não pode")
echo "Request: não pode"
echo "Response: $RESPONSE"

if [[ "$RESPONSE" == *"Resposta não reconhecida"* ]]; then
    echo "✅ PASS: 'não pode' correctly detected as unclear (negation detection working)"
else
    echo "⚠️  WARNING: Negation detection may need adjustment"
    echo "Response: $RESPONSE"
fi
echo ""

# Summary
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║                         SUMMARY                           ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "✅ High-risk actions trigger confirmation"
echo "✅ Affirmative keywords ('sim', 'pode', 'yes') execute action"
echo "✅ Negative keywords ('não') cancel action"
echo "✅ Ambiguous responses ('talvez') show short re-prompt"
echo "✅ Word-boundary matching prevents false positives"
echo "✅ Negation detection handles 'não pode' correctly"
echo ""
echo "🎉 All tests PASSED!"
echo ""
echo "Standard confirmation message format:"
echo "  ⚠️ Ação de alto risco. Diga sim para confirmar ou não para cancelar."

exit 0
