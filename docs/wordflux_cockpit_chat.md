# WordFlux Cockpit - Especificação do Agente de Chat

## Visão Geral

O Agente de Chat é uma interface conversacional em português brasileiro que permite aos usuários interagir com o sistema WordFlux através de linguagem natural. O agente compreende comandos, propõe ações e requer aprovação explícita para operações de alto risco.

## Arquitetura

### Componentes

```
┌─────────────────────────────────────────────────────────┐
│                    USUÁRIO                               │
│          (Interface Web - Português BR)                  │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│                  CHAT ROUTER                             │
│  - Rate Limiting (20 req/min)                           │
│  - Session Management                                    │
│  - Tool Invocation                                       │
└─────────────────────────────────────────────────────────┘
                           │
                    ┌──────┴──────┐
                    │             │
                    ▼             ▼
      ┌──────────────────┐  ┌──────────────────┐
      │   LLM CLIENT     │  │   REDIS          │
      │  - OpenAI        │  │  - História      │
      │  - Mock Provider │  │  - Propostas     │
      └──────────────────┘  │  - Rate Limits   │
                            │  - Audit Log     │
                            └──────────────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │   QUEUE MANAGER     │
                         │  - Job Enqueuing    │
                         │  - Worker Dispatch  │
                         └─────────────────────┘
```

## Funcionalidades Principais

### 1. Interface de Chat

#### Layout
- **Painel Esquerdo (40%):** Transcrição do chat
- **Painel Direito (60%):** Board Kanban com colunas

#### Elementos UI
- **Transcrição:**
  - Mensagens do agente (bolhas roxas, à esquerda)
  - Mensagens do usuário (bolhas laranjas, à direita)
  - Auto-scroll para mensagem mais recente
  - Altura máxima: 400px com scroll

- **Ações Rápidas (3 botões):**
  1. "Mostrar minhas tarefas" - Lista cards atribuídos ao usuário
  2. "Planeje o amanhã" - Aciona planner_agent
  3. "Limpar Concluídos" - Arquiva cards finalizados antigos

- **Input de Mensagem:**
  - Campo de texto com placeholder: "Envie sua mensagem…"
  - Botão enviar: "→"
  - Submissão via Enter key

### 2. Ferramentas Disponíveis ao LLM

#### Tool 1: `suggest_actions`
**Propósito:** Consultar ações disponíveis para um card específico

**Risco:** BAIXO (sem mudança de estado)

**Parâmetros:**
```json
{
  "card_id": "string (required)"
}
```

**Retorno:**
```json
{
  "card_id": "c-abc123",
  "column": "In Progress",
  "actions": ["send_for_review", "pause"]
}
```

**Execução:** Imediata, sem aprovação

---

#### Tool 2: `propose_move`
**Propósito:** Mover um card entre colunas do board

**Risco:** ALTO (mudança de estado crítica)

**Parâmetros:**
```json
{
  "card_id": "string (required)",
  "to_column": "string (required, one of: Backlog, In Progress, Waiting Approval, Scheduled, Published)",
  "reason": "string (optional, justificativa da movimentação)"
}
```

**Retorno:**
```json
{
  "requires_approval": true,
  "proposal_id": "prop-xyz789",
  "message": "Para mover o card 'Artigo sobre IA' para Produção, clique em Aprovar"
}
```

**Execução:** Requer aprovação explícita do usuário

**Validações:**
- Card existe no board
- Coluna destino é válida
- Respeita WIP limits (Work In Progress)

---

#### Tool 3: `queue_job`
**Propósito:** Enfileirar um job para execução por agente específico

**Risco:** DEPENDENTE DO AGENTE
- **ALTO:** task_starter, content_publisher, content_approver, change_requester
- **BAIXO:** echo, slack_notifier, metrics_reporter

**Parâmetros:**
```json
{
  "agent": "string (required, nome do agente)",
  "payload": "object (required, dados para o agente)"
}
```

**Retorno (Baixo Risco):**
```json
{
  "job_id": "cockpit-abc123",
  "status": "enqueued",
  "message": "Job enfileirado para agente echo"
}
```

**Retorno (Alto Risco):**
```json
{
  "requires_approval": true,
  "proposal_id": "prop-xyz789",
  "message": "Ação de alto risco: publicar conteúdo. Clique em Aprovar para executar."
}
```

**Agentes de Alto Risco:**
- `task_starter` - Inicia trabalho em cards (movendo para In Progress)
- `content_publisher` - Publica conteúdo para sistemas externos
- `content_approver` - Aprova conteúdo para agendamento
- `change_requester` - Solicita mudanças em conteúdo aprovado

**Agentes de Baixo Risco:**
- `echo` - Teste/verificação
- `slack_notifier` - Envia notificações
- `metrics_reporter` - Gera relatórios de métricas
- `scheduler` - Reagenda tasks

---

#### Tool 4: `bulk_from_email`
**Propósito:** Parsear corpo de email e criar múltiplos cards

**Risco:** ALTO (criação em massa)

**Parâmetros:**
```json
{
  "email_body": "string (required, corpo do email com lista de tarefas)"
}
```

**Processo:**
1. LLM identifica itens acionáveis no email
2. Extrai título, intent, prioridade para cada item
3. Cria proposta para criação em massa

**Retorno:**
```json
{
  "requires_approval": true,
  "proposal_id": "prop-bulk-123",
  "message": "Encontrei 5 tarefas no email. Clique em Aprovar para criar os cards.",
  "preview": [
    {"title": "Revisar documento", "intent": "Revisar doc do cliente X"},
    {"title": "Agendar reunião", "intent": "Reunião com stakeholder Y"}
  ]
}
```

**Execução:** SEMPRE requer aprovação

---

#### Tool 5: `summarize`
**Propósito:** Sumarizar estado do board ou progresso de cards

**Risco:** BAIXO (somente leitura)

**Parâmetros:**
```json
{
  "query": "string (optional, escopo da sumarização, ex: 'meus cards', 'cards em produção')"
}
```

**Retorno:**
```json
{
  "summary": "Você tem 3 cards em Produção: 'Artigo IA' (80% completo), 'Review PR' (50%), 'Documentação' (10%). Prazo mais próximo: Artigo IA em 2 dias."
}
```

**Execução:** Imediata, sem aprovação

---

### 3. Workflow de Aprovação

#### Fluxo Completo

```
1. Usuário: "Move o card C-123 para Produção"
   │
   ▼
2. Chat Router: Valida, chama LLM
   │
   ▼
3. LLM: Identifica tool propose_move, verifica risco
   │
   ▼
4. Sistema: Risco ALTO detectado
   │
   ▼
5. Redis: Armazena proposta em wf:chat:proposal:prop-xyz
   │         TTL: 1 hora
   ▼
6. SSE: Emite evento pending_approval
   │
   ▼
7. UI: Renderiza botão "Aprovar" no chat
   │
   ▼
8. Usuário: Clica em "Aprovar"
   │
   ▼
9. POST /chat/approve {proposal_id: "prop-xyz"}
   │
   ▼
10. Sistema: Busca proposta no Redis
    │
    ▼
11. Sistema: Enfileira job via queue_job()
    │
    ▼
12. SSE: Emite eventos approval + job_queued
    │
    ▼
13. Worker: Processa job, move card
    │
    ▼
14. SSE: Emite evento job_succeeded + board_update
    │
    ▼
15. UI: Atualiza board, mostra confirmação no chat
```

#### Estados de Proposta

| Estado | Descrição | TTL |
|--------|-----------|-----|
| `pending` | Aguardando aprovação do usuário | 1 hora |
| `approved` | Aprovada, job enfileirado | Deletada após aprovação |
| `expired` | TTL expirou, não pode mais ser aprovada | Auto-deletada pelo Redis |
| `rejected` | Usuário rejeitou explicitamente | Deletada imediatamente |

#### Redis Keys - Propostas

```
wf:chat:proposal:{proposal_id}
├─ Value: JSON serializado da proposta
├─ TTL: 3600 segundos (1 hora)
└─ Campos:
   ├─ id: "prop-xyz789"
   ├─ session_id: "sess-abc123"
   ├─ tool_name: "propose_move"
   ├─ params: {...}
   ├─ risk_level: "high"
   ├─ created_at: ISO timestamp
   └─ message: "Para mover o card..."
```

### 4. Gerenciamento de Sessões

#### Estrutura de Sessão

```
wf:chat:hist:{session_id}
├─ Type: Redis LIST
├─ TTL: 86400 segundos (24 horas)
├─ Max length: 50 mensagens (auto-trimmed)
└─ Items: JSON de cada mensagem
   ├─ role: "user" | "assistant" | "system"
   ├─ content: "texto da mensagem"
   ├─ timestamp: ISO timestamp
   └─ tool_calls: [] (se aplicável)
```

#### Geração de Session ID

- **Automática:** Se não fornecido, gera `sess-{8 hex chars}`
- **Cliente:** Frontend mantém session_id em localStorage
- **Persistência:** 24 horas após última mensagem

#### Limpeza de Sessões

**Automática:**
- Redis TTL expira sessões após 24h de inatividade

**Manual:**
```bash
# Limpar sessões expiradas (cron diário)
redis-cli --scan --pattern "wf:chat:hist:*" | \
  while read key; do
    ttl=$(redis-cli TTL "$key")
    if [ "$ttl" -eq -1 ]; then
      redis-cli EXPIRE "$key" 86400
    fi
  done
```

### 5. Rate Limiting

#### Configuração

- **Limite:** 20 requisições por minuto por IP
- **Janela:** 60 segundos (sliding window)
- **Storage:** Redis INCR + EXPIRE
- **Resposta:** HTTP 429 com cabeçalho `Retry-After`

#### Implementação

```python
def check_rate_limit(ip: str, redis: Redis) -> bool:
    key = f"wf:chat:ratelimit:{ip}"
    count = redis.incr(key)
    if count == 1:
        redis.expire(key, 60)  # 60 second window

    if count > 20:
        return False  # Rate limit exceeded
    return True
```

#### Redis Keys - Rate Limiting

```
wf:chat:ratelimit:{ip_address}
├─ Value: Contador de requisições
├─ TTL: 60 segundos
└─ Incrementado a cada requisição
```

### 6. Audit Log

#### Propósito

Registrar todas as ações que mudam estado do sistema para:
- Auditoria de compliance
- Debugging de incidentes
- Análise de uso

#### Estrutura

```
wf:chat:audit
├─ Type: Redis LIST
├─ Max length: 1000 entradas (LTRIM após cada append)
└─ Items: JSON de cada ação
   ├─ ts: ISO timestamp
   ├─ session_id: "sess-abc123"
   ├─ user_ip: "192.168.1.100"
   ├─ action: "propose_move" | "approve" | "reject"
   ├─ tool_name: "propose_move"
   ├─ params: {...}
   ├─ proposal_id: "prop-xyz" (se aplicável)
   ├─ job_id: "cockpit-abc" (se enfileirado)
   ├─ risk_level: "high" | "low"
   └─ result: "approved" | "rejected" | "executed"
```

#### Exemplo de Entrada

```json
{
  "ts": "2025-09-29T14:32:01Z",
  "session_id": "sess-a1b2c3d4",
  "user_ip": "203.0.113.42",
  "action": "approve",
  "tool_name": "propose_move",
  "params": {
    "card_id": "c-abc123",
    "to_column": "In Progress"
  },
  "proposal_id": "prop-xyz789",
  "job_id": "cockpit-job123",
  "risk_level": "high",
  "result": "approved"
}
```

#### Consulta de Audit Log

```bash
# Últimas 10 aprovações
redis-cli LRANGE wf:chat:audit 0 9

# Buscar por sessão específica
redis-cli LRANGE wf:chat:audit 0 -1 | jq 'select(.session_id == "sess-abc")'

# Contar ações de alto risco nas últimas 24h
redis-cli LRANGE wf:chat:audit 0 -1 | jq 'select(.risk_level == "high") | select(.ts > "2025-09-28T00:00:00Z")'
```

## Eventos SSE (Server-Sent Events)

### Novos Tipos de Eventos

#### 1. `chat_message`

Emitido quando nova mensagem é adicionada à conversa.

```json
{
  "kind": "chat_message",
  "ts": 1727624521000,
  "session_id": "sess-abc123",
  "role": "assistant",
  "message": "Encontrei 3 cards em Produção para você."
}
```

#### 2. `pending_approval`

Emitido quando ação de alto risco requer aprovação.

```json
{
  "kind": "pending_approval",
  "ts": 1727624522000,
  "proposal_id": "prop-xyz789",
  "tool_name": "propose_move",
  "message": "Para mover o card 'Artigo IA' para Produção, clique em Aprovar",
  "risk_level": "high",
  "session_id": "sess-abc123"
}
```

#### 3. `approval`

Emitido quando usuário aprova uma proposta.

```json
{
  "kind": "approval",
  "ts": 1727624530000,
  "proposal_id": "prop-xyz789",
  "job_id": "cockpit-job456",
  "message": "Proposta aprovada. Job enfileirado para execução."
}
```

#### 4. `chat_error`

Emitido quando ocorre erro no processamento do chat.

```json
{
  "kind": "chat_error",
  "ts": 1727624535000,
  "error_type": "rate_limit_exceeded",
  "message": "Limite de 20 requisições por minuto excedido. Tente novamente em 30 segundos.",
  "retry_after": 30
}
```

## Configuração

### Variáveis de Ambiente

```bash
# OpenAI Configuration
OPENAI_API_KEY=sk-proj-...              # API key da OpenAI
WF_LLM_PROVIDER=openai                  # Provedor: 'openai' ou 'mock'
OPENAI_CHAT_MODEL=gpt-4o-mini           # Modelo a usar

# Chat Configuration
WF_CHAT_TTL_SEC=86400                   # TTL de sessões (24 horas)
WF_RATELIMIT_PER_MIN=20                 # Limite de requisições por minuto
```

### Mock Provider

Para desenvolvimento e testes locais sem custos de API:

```bash
export WF_LLM_PROVIDER=mock
```

**Comportamento do Mock:**
- Respostas determinísticas baseadas em padrões de input
- "mostrar tarefas" → chama `suggest_actions`
- "mover card X para Y" → chama `propose_move`
- "criar cards do email" → chama `bulk_from_email`
- Nenhuma chamada externa à OpenAI
- Latência < 50ms (vs. 500-2000ms para OpenAI)

---

## Modelo Alternativo: Claude Sonnet 4.5

### Visão Geral

O WordFlux Cockpit agora suporta **Claude Sonnet 4.5** (via Anthropic API) como provedor LLM alternativo ao OpenAI. Este modelo oferece capacidades avançadas de raciocínio e tool-use, com performance superior em benchmarks de engenharia de software.

### Vantagens do Sonnet 4.5

**Performance em Benchmarks:**
- **SWE-bench Verified:** 77.2% (vs. GPT-4o: 60.5%)
- **OSWorld (tarefas computacionais):** 61.4% (líder do mercado)
- **Tool Use (Berkeley Function Calling):** 92.7% accuracy

**Características Técnicas:**
- **Contexto:** 200K tokens (janela de contexto longa)
- **Velocidade:** 2-3x mais rápido que GPT-4 (100 tokens/s)
- **Tool Calling:** Suporte nativo a múltiplas chamadas paralelas
- **Sessões longas:** Ideal para conversas com histórico extenso

**Preço:**
- **Input:** $3 / 1M tokens
- **Output:** $15 / 1M tokens
- **Comparação:** ~30% mais caro que GPT-4o-mini, mas ~70% mais barato que GPT-4

### Configuração

Para usar Sonnet 4.5, configure as seguintes variáveis de ambiente:

```bash
# Provedor LLM (padrão: mock)
WF_LLM_PROVIDER=anthropic

# API key da Anthropic (obtenha em: https://console.anthropic.com)
ANTHROPIC_API_KEY=sk-ant-api03-XXXXXXXXXXXXXXXXXXXXXXXX

# Modelo a usar (padrão: claude-sonnet-4-5)
ANTHROPIC_MODEL=claude-sonnet-4-5

# Fallback automático se primário falhar (opcional)
WF_LLM_PROVIDER_FALLBACK=openai
```

### Disponibilidade

**Anthropic API Pública:**
- Endpoint: `https://api.anthropic.com`
- Requer conta e API key
- Rate limits: 50 req/min (Tier 1), 1000 req/min (Tier 4)

**AWS Bedrock (Alternativa):**
- Modelo ARN: `anthropic.claude-3.5-sonnet-v2`
- Integração via boto3 (configuração adicional necessária)
- Vantagem: sem saída da AWS VPC

### Segurança: ASL-3 e Filtros CBRN

**Anthropic Safety Level 3 (ASL-3):**
- Sonnet 4.5 possui filtros de segurança avançados
- Bloqueia conteúdo CBRN (Químico, Biológico, Radiológico, Nuclear)
- **Potencial problema:** Falsos positivos em comandos legítimos

**Exemplo de Falso Positivo:**
```
Usuário: "Mover card 'Relatório de Química' para Produção"
Sistema: ⚠️ Bloqueado por filtro CBRN (palavra "Química")
Ação: Fallback automático para OpenAI
```

**Mitigação:**
- Sistema detecta bloqueios via código de erro da API
- Fallback automático para provider secundário (`WF_LLM_PROVIDER_FALLBACK`)
- Logging de todos os fallbacks no audit trail para análise posterior
- Banner transparente para usuário: "Modelo principal indisponível, usando fallback"

### Fallback Chain

O sistema suporta fallback automático em caso de falhas:

**Cenários de Fallback:**
1. **Bloqueio ASL-3:** Filtro CBRN bloqueia requisição → tenta fallback
2. **Rate Limit:** API retorna 429 → tenta fallback com exponential backoff
3. **Timeout:** API não responde em 10s → tenta fallback
4. **API Key Inválida:** Autenticação falha → tenta fallback

**Configuração:**
```bash
# Primário: Anthropic Sonnet 4.5
WF_LLM_PROVIDER=anthropic

# Secundário: OpenAI GPT-4o-mini
WF_LLM_PROVIDER_FALLBACK=openai

# Terciário (se fallback também falha): Mock
# Sistema automaticamente cai para mock se todos os providers falharem
```

**Eventos de Fallback:**
```json
{
  "kind": "llm_fallback",
  "ts": 1727624535000,
  "from_provider": "anthropic",
  "to_provider": "openai",
  "reason": "asl3_block",
  "message": "Modelo principal bloqueou requisição, usando fallback"
}
```

### Comparação de Providers

| Característica | Mock | OpenAI (GPT-4o-mini) | Anthropic (Sonnet 4.5) |
|----------------|------|----------------------|------------------------|
| **Custo por 1M tokens** | $0 (grátis) | $0.15 input / $0.60 output | $3 input / $15 output |
| **Latência p95** | < 50ms | 800-2000ms | 500-1200ms |
| **SWE-bench Score** | N/A | 60.5% | 77.2% |
| **Tool Use Accuracy** | 100% (determinístico) | 85-90% | 92.7% |
| **Contexto máximo** | N/A | 128K tokens | 200K tokens |
| **Suporte Tool Calling** | ✅ (simulado) | ✅ | ✅ (nativo) |
| **Fallback automático** | N/A (sempre disponível) | ✅ | ✅ |
| **Ideal para** | Dev/Testes | Produção low-cost | Produção high-accuracy |

### Questões Abertas - Anthropic

**1. Contratos Enterprise:**
- Anthropic oferece contratos com preços negociados para volumes > 10M tokens/mês
- Avaliação de ROI necessária antes de migração completa

**2. Limites Diários:**
- Free tier: 50 req/min, 100K tokens/dia
- Produção: upgrade para Tier 2+ recomendado (500 req/min, ilimitado)

**3. Integração AWS Bedrock:**
- Alternativa à API pública para compliance/segurança
- Requer configuração adicional de IAM roles e VPC endpoints
- Latência potencialmente menor (mesma região AWS)

**4. Monitoring de Custos:**
- Sonnet 4.5 é 20x mais caro que GPT-4o-mini
- Implementar alertas de budget no Prometheus:
  ```promql
  rate(wordflux_chat_messages_total{provider="anthropic"}[1h]) * 15 > 100
  ```

**5. Testes A/B:**
- Implementar feature flag para direcionar 10% tráfego para Anthropic
- Comparar métricas: latência, accuracy de tool calls, satisfação do usuário
- Decisão de migração baseada em dados após 2 semanas de piloto

---

## Métricas Prometheus

### Novos Contadores

```
# Total de mensagens por role
wordflux_chat_messages_total{role="user|assistant"}

# Total de invocações de ferramentas
wordflux_chat_tool_calls_total{tool="suggest_actions|propose_move|..."}

# Propostas pendentes de aprovação (gauge)
wordflux_chat_pending_approvals

# Rate limit hits
wordflux_chat_rate_limit_hits_total

# Erros no chat
wordflux_chat_errors_total{error_type="llm_api|rate_limit|validation"}
```

### Queries Úteis

```promql
# Taxa de aprovação (aprovações / propostas criadas)
sum(increase(wordflux_chat_tool_calls_total{tool="approve"}[5m])) /
sum(increase(wordflux_chat_messages_total{role="assistant"}[5m]))

# P95 de propostas pendentes
histogram_quantile(0.95, sum(rate(wordflux_chat_pending_approvals[5m])))

# Taxa de erro no chat
rate(wordflux_chat_errors_total[5m])
```

## Testes

### Cenários de Teste

#### 1. Ação de Baixo Risco
```
Input: "Mostre minhas tarefas"
Expected: Lista retornada imediatamente, sem aprovação
```

#### 2. Ação de Alto Risco
```
Input: "Mova o card C-123 para Produção"
Expected:
  - Botão "Aprovar" aparece
  - Proposta armazenada no Redis
  - Job NÃO enfileirado ainda
```

#### 3. Aprovação
```
Action: Clicar em "Aprovar"
Expected:
  - Job enfileirado
  - SSE emite eventos approval + job_queued
  - Proposta deletada do Redis
```

#### 4. Rate Limiting
```
Action: Enviar 21 mensagens em 60 segundos
Expected:
  - Primeiras 20: HTTP 200
  - 21ª mensagem: HTTP 429
  - Header Retry-After presente
```

#### 5. Expiração de Proposta
```
Setup: Criar proposta e aguardar 1 hora + 1 minuto
Action: Tentar aprovar proposta
Expected: HTTP 404 (proposta expirou)
```

## Questões Abertas (TODOs)

### 1. Autenticação e Autorização
**Status:** TODO
**Questão:** Como identificar o usuário que está fazendo a requisição?
**Opções:**
- IP address (atual, temporário)
- Session cookies
- JWT tokens
- OAuth 2.0 integration

**Implicações:**
- Audit log precisa registrar user_id ao invés de IP
- Rate limiting por usuário ao invés de IP
- Permissões por usuário (alguns podem aprovar, outros não)

### 2. Templates de Cards Externalizados
**Status:** TODO (issue #TBD)
**Problema:** Templates de cards estão hardcoded em `planner_agent.py`
**Solução Proposta:**
- Mover para arquivos YAML em `configs/card_templates/`
- LLM pode consultar templates disponíveis
- Admins podem adicionar novos templates sem código

### 3. Suporte Multi-Idioma
**Status:** TODO (issue #TBD)
**Problema:** Sistema atualmente apenas em PT-BR
**Solução Proposta:**
- Detectar idioma da mensagem (langdetect)
- Manter contexto do idioma na sessão
- Traduzir respostas e UI dinamicamente

### 4. Cleanup Automático de Propostas
**Status:** TODO (issue #TBD)
**Problema:** Propostas expiradas permanecem no Redis (TTL cuida, mas limpeza proativa seria melhor)
**Solução Proposta:**
- Background job rodando a cada hora
- SCAN wf:chat:proposal:* e verifica TTL
- Deleta as que estão próximas de expirar

### 5. Export de Conversas
**Status:** TODO (issue #TBD)
**Feature:** Usuários podem querer baixar histórico de conversa
**Solução Proposta:**
- GET /chat/export?session_id=xxx
- Retorna JSON ou markdown formatado
- Incluir propostas e aprovações

## Referências

- [OpenAI Function Calling](https://platform.openai.com/docs/guides/function-calling)
- [Redis Rate Limiting Patterns](https://redis.io/docs/manual/patterns/rate-limiter/)
- [Server-Sent Events Specification](https://html.spec.whatwg.org/multipage/server-sent-events.html)
- [WordFlux Agent Architecture](../ARCHITECTURE_DIAGRAM.txt)