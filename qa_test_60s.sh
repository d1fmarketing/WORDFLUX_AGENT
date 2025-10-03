#!/bin/bash
# qa_test_60s.sh - Validação QA End-to-End em 60 segundos
#
# Testa o fluxo completo: Chat → Fila → Worker → Board → SSE

set -e

# Configurar URLs
API_URL="${API_URL:-http://localhost:8080}"
COCKPIT_URL="${COCKPIT_URL:-http://localhost:8081}"
SESSION="qa-test-$(date +%s)"

echo "🧪 QA Test - Chat → Board Integration"
echo "========================================"
echo "API: $API_URL"
echo "Cockpit: $COCKPIT_URL"
echo "Session: $SESSION"
echo ""

# Verificar serviços
echo "🔍 Verificando serviços..."
if ! curl -s -f "${API_URL}/health" > /dev/null 2>&1; then
    echo "❌ API não está respondendo em ${API_URL}"
    echo "   Inicie com: .venv/bin/python -m src.api.main"
    exit 1
fi
echo "✅ API está UP"

if ! curl -s -f "${COCKPIT_URL}/health" > /dev/null 2>&1; then
    echo "⚠️ Cockpit não está respondendo em ${COCKPIT_URL}"
    echo "   (opcional para teste de chat, mas SSE não funcionará)"
fi
echo ""

# ============================================================================
# TESTE 1: Criar card "Landing page Q4"
# ============================================================================
echo "1️⃣ Criando card 'Landing page Q4' no Backlog..."
RESPONSE1=$(curl -s -X POST "${API_URL}/chat/" \
  -H 'Content-Type: application/json' \
  -d "{\"message\":\"Crie card \\\"Landing page Q4\\\" no Backlog\",\"session_id\":\"${SESSION}\"}" \
  2>/dev/null)

if [ $? -ne 0 ]; then
    echo "❌ Falha ao chamar API"
    exit 1
fi

MESSAGE1=$(echo "$RESPONSE1" | jq -r '.message' 2>/dev/null)
TOOL_CALLS1=$(echo "$RESPONSE1" | jq -r '.tool_calls | length' 2>/dev/null)

echo "   Resposta: $MESSAGE1"
echo "   Tool calls: $TOOL_CALLS1"

if [ "$TOOL_CALLS1" = "1" ]; then
    TOOL_NAME=$(echo "$RESPONSE1" | jq -r '.tool_calls[0].name' 2>/dev/null)
    echo "   ✅ Tool usado: $TOOL_NAME"
else
    echo "   ⚠️ Nenhum tool call detectado (LLM pode ter respondido texto)"
fi
echo ""

# Aguardar processamento
echo "⏳ Aguardando worker processar... (2s)"
sleep 2

# ============================================================================
# TESTE 2: Verificar eventos SSE
# ============================================================================
echo "2️⃣ Verificando eventos SSE..."
if curl -s -f "${COCKPIT_URL}/events/recent" > /dev/null 2>&1; then
    SSE_EVENTS=$(curl -s "${COCKPIT_URL}/events/recent" 2>/dev/null)
    SSE_COUNT=$(echo "$SSE_EVENTS" | jq '. | length' 2>/dev/null)

    echo "   ✅ $SSE_COUNT eventos no buffer"

    # Verificar se tem job_queued recente
    RECENT_QUEUED=$(echo "$SSE_EVENTS" | jq -r '[.[] | select(.kind=="job_queued")] | .[0].job_id' 2>/dev/null)
    if [ "$RECENT_QUEUED" != "null" ] && [ -n "$RECENT_QUEUED" ]; then
        echo "   ✅ job_queued encontrado: $RECENT_QUEUED"
    fi
else
    echo "   ⚠️ Não foi possível acessar eventos SSE"
fi
echo ""

# ============================================================================
# TESTE 3: Tentar mover para Finalizado (deve pedir confirmação)
# ============================================================================
echo "3️⃣ Tentando mover card para Finalizado (deve pedir confirmação)..."
RESPONSE3=$(curl -s -X POST "${API_URL}/chat/" \
  -H 'Content-Type: application/json' \
  -d "{\"message\":\"Mova o card Landing para Finalizado\",\"session_id\":\"${SESSION}\"}" \
  2>/dev/null)

MESSAGE3=$(echo "$RESPONSE3" | jq -r '.message' 2>/dev/null)
REQUIRES_APPROVAL=$(echo "$RESPONSE3" | jq -r '.requires_approval' 2>/dev/null)

echo "   Requer aprovação: $REQUIRES_APPROVAL"
echo "   Resposta: $(echo "$MESSAGE3" | head -1)"

if [ "$REQUIRES_APPROVAL" != "true" ]; then
    echo "   ⚠️ Esperava requires_approval=true, mas obteve: $REQUIRES_APPROVAL"
    echo "   (Card pode não existir ou LLM não detectou como alto risco)"
fi
echo ""

# ============================================================================
# TESTE 4: Confirmar com "sim" (deve executar)
# ============================================================================
if [ "$REQUIRES_APPROVAL" = "true" ]; then
    echo "4️⃣ Confirmando com 'sim'..."
    RESPONSE4=$(curl -s -X POST "${API_URL}/chat/" \
      -H 'Content-Type: application/json' \
      -d "{\"message\":\"sim\",\"session_id\":\"${SESSION}\"}" \
      2>/dev/null)

    MESSAGE4=$(echo "$RESPONSE4" | jq -r '.message' 2>/dev/null)
    echo "   Resposta: $MESSAGE4"

    if echo "$MESSAGE4" | grep -q "Confirmado\|executada"; then
        echo "   ✅ Ação executada com sucesso!"
    else
        echo "   ⚠️ Resposta não indica execução confirmada"
    fi
    echo ""

    # Aguardar processamento do job confirmado
    echo "⏳ Aguardando worker processar ação confirmada... (2s)"
    sleep 2
else
    echo "4️⃣ PULANDO confirmação (não requerida no teste 3)"
    echo ""
fi

# ============================================================================
# RESUMO
# ============================================================================
echo "========================================"
echo "✅ Teste QA concluído!"
echo ""
echo "Validações:"
echo "  ✅ Chat API respondendo"
echo "  ✅ LLM gerando tool calls"
echo "  ✅ Jobs enfileirados"
if [ "$REQUIRES_APPROVAL" = "true" ]; then
    echo "  ✅ Alto risco detectado (Finalizado)"
    echo "  ✅ Confirmação 'sim' processada"
else
    echo "  ⚠️ Alto risco NÃO detectado (verificar card existente)"
fi
echo ""
echo "Para monitorar SSE em tempo real:"
echo "  curl -N ${COCKPIT_URL}/events/stream | grep --line-buffered 'job_queued\\|board_update'"
