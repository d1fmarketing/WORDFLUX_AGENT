#!/bin/bash
# qa_test_simple.sh - Teste QA simplificado sem jq

API_URL="${API_URL:-http://localhost:8080}"
COCKPIT_URL="${COCKPIT_URL:-http://localhost:8081}"
SESSION="qa-$(date +%s)"

echo "=========================================="
echo "🧪 QA Test - Chat → Board (Simplified)"
echo "=========================================="
echo ""

# Teste 1
echo "1️⃣ Criando card 'Landing page Q4'..."
curl -s -X POST "${API_URL}/chat/" \
  -H 'Content-Type: application/json' \
  -d "{\"message\":\"Crie card \\\"Landing page Q4\\\" no Backlog\",\"session_id\":\"${SESSION}\"}"
echo -e "\n"

# Aguardar
sleep 2

# Teste 2
echo "2️⃣ Verificando eventos SSE..."
curl -s "${COCKPIT_URL}/events/recent" | python3 -m json.tool | grep -A 2 "job_queued" | head -10
echo ""

# Teste 3
echo "3️⃣ Tentando mover para Finalizado..."
curl -s -X POST "${API_URL}/chat/" \
  -H 'Content-Type: application/json' \
  -d "{\"message\":\"Mova Landing page Q4 para Finalizado\",\"session_id\":\"${SESSION}\"}"
echo -e "\n"

sleep 1

# Teste 4
echo "4️⃣ Confirmando com 'sim'..."
curl -s -X POST "${API_URL}/chat/" \
  -H 'Content-Type: application/json' \
  -d "{\"message\":\"sim\",\"session_id\":\"${SESSION}\"}"
echo -e "\n"

echo "=========================================="
echo "✅ Teste concluído!"
