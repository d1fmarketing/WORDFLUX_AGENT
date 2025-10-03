#!/bin/bash
# qa_bedrock_e2e.sh - Bedrock End-to-End QA Test
#
# Pré-requisitos:
# 1. WF_LLM_PROVIDER=bedrock em wordflux.env
# 2. IAM permissions configuradas (bedrock:InvokeModel)
# 3. Serviço wordflux-api rodando (porta 8080)
# 4. Cockpit rodando (porta 8081)
# 5. jq instalado

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuração
HOST="${HOST:-wordflux.3-228-174-188.nip.io}"
SESSION_ID="bed-qa-$(date +%s)"

echo -e "${BLUE}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   Bedrock E2E QA Test                            ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo "Session ID: $SESSION_ID"
echo "Host: $HOST"
echo ""

# ═══════════════════════════════════════════════════════════════
# Passo 1: Criar card
# ═══════════════════════════════════════════════════════════════
echo -e "${BLUE}═══ Passo 1: Criar Card ═══${NC}"
echo ""
echo "Comando:"
echo -e "${YELLOW}curl -s -X POST http://$HOST:8080/chat/ \\${NC}"
echo -e "${YELLOW}  -H 'Content-Type: application/json' \\${NC}"
echo -e "${YELLOW}  -d '{\"message\":\"Crie card \\\"Landing page Q4\\\" no Backlog\",\"session_id\":\"$SESSION_ID\"}'${NC}"
echo ""

RESPONSE_1=$(curl -s -X POST http://$HOST:8080/chat/ \
  -H 'Content-Type: application/json' \
  -d "{\"message\":\"Crie card \\\"Landing page Q4\\\" no Backlog\",\"session_id\":\"$SESSION_ID\"}")

echo "Resposta:"
echo "$RESPONSE_1" | jq '.'
echo ""

# Validações Passo 1
echo "✓ Validações:"
TOOL_CALL=$(echo "$RESPONSE_1" | jq -r '.tool_calls[0].name // empty')
REQUIRES_APPROVAL=$(echo "$RESPONSE_1" | jq -r '.requires_approval // empty')
MESSAGE=$(echo "$RESPONSE_1" | jq -r '.message // empty')

if [ "$TOOL_CALL" == "create_card" ]; then
  echo -e "  ${GREEN}✅ Tool call: create_card${NC}"
else
  echo -e "  ${RED}❌ Tool call esperado: create_card, recebido: $TOOL_CALL${NC}"
  exit 1
fi

if [ "$REQUIRES_APPROVAL" == "false" ]; then
  echo -e "  ${GREEN}✅ requires_approval: false (low-risk)${NC}"
else
  echo -e "  ${RED}❌ requires_approval esperado: false, recebido: $REQUIRES_APPROVAL${NC}"
  exit 1
fi

if echo "$MESSAGE" | grep -q "Job(s) enfileirado"; then
  echo -e "  ${GREEN}✅ Mensagem contém 'Job(s) enfileirado'${NC}"
else
  echo -e "  ${YELLOW}⚠️ Mensagem: $MESSAGE${NC}"
fi

echo ""
read -p "Pressione ENTER para continuar..." -t 5 || echo ""
echo ""

# ═══════════════════════════════════════════════════════════════
# Passo 2: Ver SSE stream (informativo)
# ═══════════════════════════════════════════════════════════════
echo -e "${BLUE}═══ Passo 2: Verificar SSE Stream ═══${NC}"
echo ""
echo "Comando (executar em outro terminal):"
echo -e "${YELLOW}curl -N http://$HOST:8081/events/stream | grep --line-buffered 'job_queued'${NC}"
echo ""
echo "Validação esperada:"
echo "  ✅ Evento 'job_queued' aparece no stream"
echo "  ✅ Evento contém session_id: $SESSION_ID"
echo ""
echo -e "${YELLOW}⚠️ Execute o comando acima em outro terminal e verifique os eventos.${NC}"
echo ""

# Tentar capturar SSE por 3 segundos (não bloqueante)
echo "Tentando capturar evento SSE automaticamente (3s timeout)..."
timeout 3s curl -N -s http://$HOST:8081/events/stream 2>/dev/null | grep --line-buffered "job_queued" | head -1 || echo "  ⏱️ Timeout (SSE pode estar funcionando, mas não recebemos eventos em 3s)"

echo ""
read -p "Eventos SSE verificados manualmente? (s/n/skip): " SSE_OK

if [ "$SSE_OK" == "s" ]; then
  echo -e "  ${GREEN}✅ SSE stream validado${NC}"
elif [ "$SSE_OK" == "skip" ]; then
  echo -e "  ${YELLOW}⏭️ SSE stream pulado${NC}"
else
  echo -e "  ${YELLOW}⚠️ SSE stream não validado (continuando...)${NC}"
fi

echo ""

# Aguardar processamento do job
echo "Aguardando 5s para job ser processado..."
sleep 5

# Obter card ID do board
echo "Buscando cards no Backlog..."
CARD_ID=$(curl -s http://$HOST:8081/board/state 2>/dev/null | jq -r '.columns[]? | select(.title=="Backlog") | .cards[0]?.id // empty' | head -1)

if [ -z "$CARD_ID" ] || [ "$CARD_ID" == "null" ]; then
  echo -e "${YELLOW}⚠️ Card não encontrado no Backlog automaticamente.${NC}"
  read -p "Digite o card_id manualmente (ou 'skip' para pular testes de movimentação): " MANUAL_CARD_ID

  if [ "$MANUAL_CARD_ID" == "skip" ]; then
    echo -e "${YELLOW}⏭️ Testes de movimentação pulados.${NC}"
    exit 0
  else
    CARD_ID="$MANUAL_CARD_ID"
  fi
fi

echo -e "${GREEN}✅ Card encontrado: $CARD_ID${NC}"

echo ""
read -p "Pressione ENTER para continuar..." -t 5 || echo ""
echo ""

# ═══════════════════════════════════════════════════════════════
# Passo 3: Mover com confirmação
# ═══════════════════════════════════════════════════════════════
echo -e "${BLUE}═══ Passo 3: Mover para Finalizado (High-Risk) ═══${NC}"
echo ""
echo "Comando:"
echo -e "${YELLOW}curl -s -X POST http://$HOST:8080/chat/ \\${NC}"
echo -e "${YELLOW}  -H 'Content-Type: application/json' \\${NC}"
echo -e "${YELLOW}  -d '{\"message\":\"Mova $CARD_ID para Finalizado\",\"session_id\":\"$SESSION_ID\"}'${NC}"
echo ""

RESPONSE_3=$(curl -s -X POST http://$HOST:8080/chat/ \
  -H 'Content-Type: application/json' \
  -d "{\"message\":\"Mova $CARD_ID para Finalizado\",\"session_id\":\"$SESSION_ID\"}")

echo "Resposta:"
echo "$RESPONSE_3" | jq '.'
echo ""

# Validações Passo 3
echo "✓ Validações:"
TOOL_CALL_3=$(echo "$RESPONSE_3" | jq -r '.tool_calls[0].name // empty')
REQUIRES_APPROVAL_3=$(echo "$RESPONSE_3" | jq -r '.requires_approval // empty')
MESSAGE_3=$(echo "$RESPONSE_3" | jq -r '.message // empty')

if [ "$TOOL_CALL_3" == "move_card" ]; then
  echo -e "  ${GREEN}✅ Tool call: move_card${NC}"
else
  echo -e "  ${RED}❌ Tool call esperado: move_card, recebido: $TOOL_CALL_3${NC}"
  exit 1
fi

if [ "$REQUIRES_APPROVAL_3" == "true" ]; then
  echo -e "  ${GREEN}✅ requires_approval: true (high-risk)${NC}"
else
  echo -e "  ${RED}❌ requires_approval esperado: true, recebido: $REQUIRES_APPROVAL_3${NC}"
  exit 1
fi

if echo "$MESSAGE_3" | grep -qiE "(confirma|confirmo|confirmação)"; then
  echo -e "  ${GREEN}✅ Mensagem pede confirmação${NC}"
else
  echo -e "  ${YELLOW}⚠️ Mensagem: $MESSAGE_3${NC}"
fi

echo ""
read -p "Pressione ENTER para continuar..." -t 5 || echo ""
echo ""

# ═══════════════════════════════════════════════════════════════
# Passo 4: Confirmar
# ═══════════════════════════════════════════════════════════════
echo -e "${BLUE}═══ Passo 4: Confirmar Ação ═══${NC}"
echo ""
echo "Comando:"
echo -e "${YELLOW}curl -s -X POST http://$HOST:8080/chat/ \\${NC}"
echo -e "${YELLOW}  -H 'Content-Type: application/json' \\${NC}"
echo -e "${YELLOW}  -d '{\"message\":\"sim\",\"session_id\":\"$SESSION_ID\"}'${NC}"
echo ""

RESPONSE_4=$(curl -s -X POST http://$HOST:8080/chat/ \
  -H 'Content-Type: application/json' \
  -d "{\"message\":\"sim\",\"session_id\":\"$SESSION_ID\"}")

echo "Resposta:"
echo "$RESPONSE_4" | jq '.'
echo ""

# Validações Passo 4
echo "✓ Validações:"
MESSAGE_4=$(echo "$RESPONSE_4" | jq -r '.message // empty')

if echo "$MESSAGE_4" | grep -q "Confirmado"; then
  echo -e "  ${GREEN}✅ Mensagem contém 'Confirmado'${NC}"
else
  echo -e "  ${YELLOW}⚠️ Mensagem: $MESSAGE_4${NC}"
fi

if echo "$MESSAGE_4" | grep -q "Job"; then
  echo -e "  ${GREEN}✅ Job enfileirado após confirmação${NC}"
else
  echo -e "  ${YELLOW}⚠️ Job não mencionado na resposta${NC}"
fi

echo ""
echo "Aguardando 5s para job ser processado..."
sleep 5
echo ""

# ═══════════════════════════════════════════════════════════════
# Verificação Final do Board
# ═══════════════════════════════════════════════════════════════
echo -e "${BLUE}═══ Verificação Final do Board ═══${NC}"
echo ""
echo "Verificando se card $CARD_ID está em Finalizado..."

FINAL_STATE=$(curl -s http://$HOST:8081/board/state 2>/dev/null | jq -r ".columns[]? | select(.title==\"Finalizado\") | .cards[]? | select(.id==\"$CARD_ID\") | .id // empty")

if [ "$FINAL_STATE" == "$CARD_ID" ]; then
  echo -e "  ${GREEN}✅ Card $CARD_ID encontrado em Finalizado${NC}"
else
  echo -e "  ${YELLOW}⚠️ Card $CARD_ID NÃO encontrado em Finalizado${NC}"
  echo "  Estado atual do board:"
  curl -s http://$HOST:8081/board/state 2>/dev/null | jq '.columns[]? | {title: .title, cards: [.cards[]?.id]}'
fi

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   QA Test Completo                               ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
