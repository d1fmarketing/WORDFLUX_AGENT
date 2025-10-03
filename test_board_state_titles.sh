#!/bin/bash
# Test /board/state Schema - Ensure Column Titles Are Never Null
# Acceptance Criteria:
#   - curl -s /board/state | jq -r '.columns[]|.title' prints valid titles
#   - .columns[] | select(.title=="Espera") | .cards[0].id works

set -e

HOST="${1:-localhost:8081}"

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║      /board/state Schema Validation Test                 ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "Target: http://$HOST/board/state"
echo ""

# Fetch board state
RESPONSE=$(curl -s http://$HOST/board/state)

# Test 1: No null titles
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 1: All column titles are non-null"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

NULL_TITLES=$(echo "$RESPONSE" | jq -r '.columns[] | select(.title == null) | .id')

if [ -z "$NULL_TITLES" ]; then
    echo "✅ PASS: All titles are non-null"
else
    NULL_COUNT=$(echo "$NULL_TITLES" | wc -l)
    echo "❌ FAIL: Found $NULL_COUNT columns with null titles:"
    echo "$NULL_TITLES"
    exit 1
fi

echo ""

# Test 2: All titles are strings
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 2: All column titles are strings"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

NON_STRING=$(echo "$RESPONSE" | jq -r '.columns[] | select((.title | type) != "string") | .id')

if [ -z "$NON_STRING" ]; then
    echo "✅ PASS: All titles are strings"
else
    NON_STRING_COUNT=$(echo "$NON_STRING" | wc -l)
    echo "❌ FAIL: Found $NON_STRING_COUNT columns with non-string titles"
    exit 1
fi

echo ""

# Test 3: Print all titles (acceptance criteria #1)
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 3: Print all column titles (Acceptance Criteria #1)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Command: curl -s /board/state | jq -r '.columns[]|.title'"
echo ""

TITLES=$(echo "$RESPONSE" | jq -r '.columns[]|.title')
echo "$TITLES"

if [ -n "$TITLES" ]; then
    echo ""
    TITLE_COUNT=$(echo "$TITLES" | grep -c "." || echo "0")
    echo "✅ PASS: Found $TITLE_COUNT column titles"
else
    echo "❌ FAIL: No titles found"
    exit 1
fi

echo ""

# Test 4: Query Espera column (acceptance criteria #2)
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 4: Query Espera column (Acceptance Criteria #2)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Command: .columns[] | select(.title==\"Espera\") | .cards[0].id"
echo ""

ESPERA_QUERY=$(echo "$RESPONSE" | jq '.columns[] | select(.title=="Espera") | .cards[0].id')

if [ -n "$ESPERA_QUERY" ]; then
    if [ "$ESPERA_QUERY" = "null" ]; then
        echo "✅ PASS: Query executed successfully (no cards in Espera)"
    else
        echo "✅ PASS: Query returned card ID: $ESPERA_QUERY"
    fi
else
    echo "❌ FAIL: Query failed - no Espera column found"
    exit 1
fi

echo ""

# Test 5: Verify Portuguese titles
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 5: Verify Portuguese titles"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

EXPECTED_TITLES=("Espera" "Produção" "Aprovação" "Agendado" "Finalizado")
ALL_VALID=true

for title in $(echo "$RESPONSE" | jq -r '.columns[].title'); do
    VALID=false
    for expected in "${EXPECTED_TITLES[@]}"; do
        if [ "$title" = "$expected" ]; then
            VALID=true
            break
        fi
    done

    if [ "$VALID" = false ]; then
        echo "⚠️  WARNING: Unexpected title found: $title"
        ALL_VALID=false
    fi
done

if [ "$ALL_VALID" = true ]; then
    echo "✅ PASS: All titles are valid Portuguese names"
else
    echo "⚠️  Some titles don't match expected Portuguese names"
fi

echo ""

# Test 6: Verify column structure
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 6: Verify column structure (id, title, cards)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

MISSING_FIELDS=$(echo "$RESPONSE" | jq -r '.columns[] | select(.id == null or .title == null or .cards == null) | .id // "unknown"')

if [ -z "$MISSING_FIELDS" ]; then
    echo "✅ PASS: All columns have required fields (id, title, cards)"
else
    MISSING_COUNT=$(echo "$MISSING_FIELDS" | wc -l)
    echo "❌ FAIL: Found $MISSING_COUNT columns missing required fields"
    exit 1
fi

echo ""

# Summary
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║                    SUMMARY                                ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "✅ All column titles are non-null"
echo "✅ All titles are strings"
echo "✅ Title extraction query works"
echo "✅ Espera column query works"
echo "✅ All columns have required fields"
echo ""
echo "🎉 All tests PASSED!"
echo ""
echo "Sample output:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "$RESPONSE" | jq '.columns[] | {id, title, card_count: (.cards | length)}'
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

exit 0
