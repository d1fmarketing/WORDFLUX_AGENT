#!/bin/bash
################################################################################
# WordFlux Smoke Test - End-to-End Validation
#
# Purpose: Validate complete flow from API to SSE events
# Tests:
#   1. GET /board/state (HTTP 200 + 5 columns)
#   2. POST /chat "Crie card 'Landing Setembro' em Espera" → SSE card.created
#   3. POST /chat "Mova... para Finalizado" → confirmation + "sim" → SSE job.queued
#
# Usage:
#   ./scripts/smoke.sh [BASE_URL]
#
# Example:
#   ./scripts/smoke.sh http://localhost:8080
#   ./scripts/smoke.sh http://wordflux.3-228-174-188.nip.io
#
# Exit Codes:
#   0 = All tests passed
#   1 = One or more tests failed
################################################################################

set -euo pipefail

################################################################################
# Configuration
################################################################################
BASE_URL="${1:-http://localhost:8080}"
COCKPIT_URL="${BASE_URL%:*}:8081"  # Assume cockpit on 8081
SESSION_ID="smoke-$(date +%s)-$$"
TIMEOUT_SECONDS=30
SSE_FIFO="/tmp/wordflux-sse-$$.fifo"

################################################################################
# Colors
################################################################################
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

################################################################################
# Cleanup Handler
################################################################################
cleanup() {
    # Kill background SSE listener
    jobs -p | xargs -r kill 2>/dev/null || true

    # Remove named pipe
    rm -f "$SSE_FIFO"
}
trap cleanup EXIT INT TERM

################################################################################
# Helper Functions
################################################################################
print_header() {
    echo -e "\n${BOLD}${CYAN}========================================${RESET}"
    echo -e "${BOLD}${CYAN}$1${RESET}"
    echo -e "${BOLD}${CYAN}========================================${RESET}\n"
}

print_success() {
    echo -e "${GREEN}✅ $1${RESET}"
}

print_error() {
    echo -e "${RED}❌ $1${RESET}"
}

print_info() {
    echo -e "${BLUE}ℹ️  $1${RESET}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${RESET}"
}

wait_for_sse_event() {
    local event_kind="$1"
    local timeout_sec="${2:-10}"
    local fifo="$SSE_FIFO"

    print_info "Aguardando evento SSE: ${event_kind} (timeout: ${timeout_sec}s)..."

    timeout "$timeout_sec" grep -m1 "\"kind\":\"${event_kind}\"" "$fifo" 2>/dev/null && return 0
    return 1
}

################################################################################
# SSE Listener (Background Process)
################################################################################
start_sse_listener() {
    # Create named pipe for SSE events
    mkfifo "$SSE_FIFO"

    print_info "Iniciando listener SSE: ${COCKPIT_URL}/events/stream"

    # Start SSE listener in background (writes to FIFO)
    curl -N -s "${COCKPIT_URL}/events/stream" 2>/dev/null | \
        grep --line-buffered "^data:" | \
        sed 's/^data: //' > "$SSE_FIFO" &

    local sse_pid=$!
    print_info "SSE listener PID: ${sse_pid}"

    # Wait for SSE connection to establish
    sleep 2
}

################################################################################
# Test 1: GET /board/state
################################################################################
test_board_state() {
    print_header "Test 1: GET /board/state"

    local url="${COCKPIT_URL}/board/state"
    print_info "GET ${url}"

    # Make request
    local response
    response=$(curl -s -w "\n%{http_code}" "$url" 2>/dev/null)

    local http_code
    http_code=$(echo "$response" | tail -1)

    local body
    body=$(echo "$response" | head -n-1)

    # Check HTTP 200
    if [[ "$http_code" != "200" ]]; then
        print_error "HTTP status: ${http_code} (expected 200)"
        return 1
    fi
    print_success "HTTP 200 OK"

    # Check JSON structure
    local columns
    columns=$(echo "$body" | jq -r '.columns | keys | .[]' 2>/dev/null)

    if [[ -z "$columns" ]]; then
        print_error "Falta campo 'columns' na resposta"
        return 1
    fi

    # Validate 5 columns exist
    local expected_columns=("Agendado" "Aprovação" "Espera" "Finalizado" "Produção")
    local found_count=0

    for col in "${expected_columns[@]}"; do
        if echo "$columns" | grep -q "^${col}$"; then
            found_count=$((found_count + 1))
        else
            print_warning "Coluna faltando: ${col}"
        fi
    done

    if [[ $found_count -eq 5 ]]; then
        print_success "5 colunas encontradas: $(echo "$columns" | tr '\n' ', ' | sed 's/,$//')"
        return 0
    else
        print_error "Apenas ${found_count}/5 colunas encontradas"
        return 1
    fi
}

################################################################################
# Test 2: POST /chat - Create Card (Low Risk)
################################################################################
test_create_card() {
    print_header "Test 2: Criar Card 'Landing Setembro' em Espera"

    local url="${BASE_URL}/chat"
    local message="Crie card 'Landing Setembro' em Espera"

    print_info "POST ${url}"
    print_info "Message: ${message}"

    # Send chat message
    local response
    response=$(curl -s -X POST "$url" \
        -H "Content-Type: application/json" \
        -d "{\"session_id\":\"${SESSION_ID}\",\"message\":\"${message}\"}" \
        2>/dev/null)

    # Check response has message field
    local reply
    reply=$(echo "$response" | jq -r '.message' 2>/dev/null)

    if [[ -z "$reply" || "$reply" == "null" ]]; then
        print_error "Resposta inválida (falta campo 'message')"
        return 1
    fi

    print_success "Resposta recebida: ${reply:0:80}..."

    # Wait for SSE event: card.created
    if wait_for_sse_event "card.created" 10; then
        print_success "Evento SSE 'card.created' recebido"
        return 0
    else
        print_error "Timeout aguardando evento SSE 'card.created'"
        return 1
    fi
}

################################################################################
# Test 3: POST /chat - Move Card (High Risk + Confirmation)
################################################################################
test_move_card_with_confirmation() {
    print_header "Test 3: Mover 'Landing Setembro' para Finalizado (High-Risk)"

    local url="${BASE_URL}/chat"
    local message1="Mova 'Landing Setembro' para Finalizado"

    # Step 1: Request move (high-risk action)
    print_info "POST ${url}"
    print_info "Message: ${message1}"

    local response1
    response1=$(curl -s -X POST "$url" \
        -H "Content-Type: application/json" \
        -d "{\"session_id\":\"${SESSION_ID}\",\"message\":\"${message1}\"}" \
        2>/dev/null)

    local reply1
    reply1=$(echo "$response1" | jq -r '.message' 2>/dev/null)

    # Check if confirmation requested
    if ! echo "$reply1" | grep -qi "confirmação\|confirmar\|sim.*não"; then
        print_error "Confirmação não solicitada (esperado 'Ação de alto risco')"
        return 1
    fi

    print_success "Confirmação solicitada: ${reply1:0:60}..."

    # Step 2: Send confirmation "sim"
    sleep 1
    local message2="sim"

    print_info "Confirmando com: ${message2}"

    local response2
    response2=$(curl -s -X POST "$url" \
        -H "Content-Type: application/json" \
        -d "{\"session_id\":\"${SESSION_ID}\",\"message\":\"${message2}\"}" \
        2>/dev/null)

    local reply2
    reply2=$(echo "$response2" | jq -r '.message' 2>/dev/null)

    print_success "Confirmação enviada: ${reply2:0:80}..."

    # Step 3: Wait for SSE event: job.queued (card.moved would also work)
    if wait_for_sse_event "card.moved\|job_queued" 10; then
        print_success "Evento SSE recebido (job enfileirado)"
        return 0
    else
        print_error "Timeout aguardando evento SSE 'card.moved' ou 'job_queued'"
        return 1
    fi
}

################################################################################
# Main Test Suite
################################################################################
main() {
    local tests_passed=0
    local tests_failed=0

    print_header "WordFlux Smoke Test"
    print_info "BASE_URL: ${BASE_URL}"
    print_info "COCKPIT_URL: ${COCKPIT_URL}"
    print_info "SESSION_ID: ${SESSION_ID}"

    # Start SSE listener (background)
    start_sse_listener

    # Run tests
    echo ""
    if test_board_state; then
        tests_passed=$((tests_passed + 1))
    else
        tests_failed=$((tests_failed + 1))
    fi

    echo ""
    if test_create_card; then
        tests_passed=$((tests_passed + 1))
    else
        tests_failed=$((tests_failed + 1))
    fi

    echo ""
    if test_move_card_with_confirmation; then
        tests_passed=$((tests_passed + 1))
    else
        tests_failed=$((tests_failed + 1))
    fi

    # Summary
    print_header "Resumo"
    echo -e "${GREEN}Passed: ${tests_passed}${RESET}"
    echo -e "${RED}Failed: ${tests_failed}${RESET}"

    if [[ $tests_failed -eq 0 ]]; then
        echo -e "\n${BOLD}${GREEN}✅ OK - Todos os testes passaram!${RESET}\n"
        exit 0
    else
        echo -e "\n${BOLD}${RED}❌ FAIL - ${tests_failed} teste(s) falharam${RESET}\n"
        exit 1
    fi
}

main "$@"
