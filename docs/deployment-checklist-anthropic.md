# Checklist de Deployment - Anthropic Claude Sonnet 4.5

**Versão:** 1.0
**Data:** 2025-09-30
**Responsável:** _______________
**Revisado por:** _______________

---

## ☑️ PRÉ-DEPLOYMENT (BLOQUEANTE)

### Código & Testes
- [ ] Todas as 8 fases implementadas conforme `IMPLEMENTATION_STATUS_ANTHROPIC.md`
- [ ] Canary routing testado localmente (100 requests, ~10% para cada provider)
- [ ] Métricas Prometheus funcionando: `curl /metrics | grep wordflux_chat`
- [ ] Auditoria Redis validada: `redis-cli LRANGE wf:chat:audit 0 5`
- [ ] Cost tracking acumulando valores: `redis-cli GET wf:chat:cost:$(date +%Y-%m-%d)`
- [ ] Smoke test 4/4 passando: `python scripts/guardrail_smoke_test.py`
- [ ] Todos os testes unitários passando: `pytest tests/unit/test_llm_bridge.py -k "anthropic or fallback" -v`
- [ ] Cobertura ≥ 80% para código novo

### Configuração
- [ ] `.env.example` atualizado com 4 novas variáveis (`WF_CANARY_*`, `WF_CHAT_DAILY_BUDGET`)
- [ ] `wordflux.env` em produção configurado:
  ```bash
  WF_CANARY_PCT=0                 # Start disabled
  WF_CANARY_PROVIDER=anthropic
  WF_LLM_PROVIDER=openai          # Default
  WF_LLM_PROVIDER_FALLBACK=openai
  ANTHROPIC_API_KEY=sk-ant-...    # ROTATED key
  ANTHROPIC_MODEL=claude-sonnet-4-20250514
  WF_CHAT_DAILY_BUDGET=50.0
  WF_CHAT_MAX_INPUT_TOKENS=8000
  ```
- [ ] API key temporária **ROTACIONADA** (não usar a key do teste)
- [ ] Nginx config atualizado (se necessário)
- [ ] Backup de configs feito:
  ```bash
  sudo cp /etc/nginx/sites-available/wordflux \
         /etc/nginx/sites-available/wordflux.backup
  redis-cli SAVE
  ```

### Documentação
- [ ] `docs/runbooks/chat-rollback.md` criado com 4 procedimentos (R1-R4)
- [ ] `docs/IMPLEMENTATION_STATUS_ANTHROPIC.md` revisado
- [ ] Este checklist impresso/disponível durante deployment
- [ ] Equipe on-call notificada sobre deployment
- [ ] Post-mortem template criado: `docs/post-mortems/YYYY-MM-DD-template.md`

### Observabilidade
- [ ] Grafana dashboard criado (ou Prometheus queries prontos):
  ```promql
  # 1. Request rate por provider
  sum by (provider) (rate(wordflux_chat_requests_total[5m]))

  # 2. Latency p95 por provider
  histogram_quantile(0.95, sum by (provider, le) (rate(wordflux_chat_latency_seconds_bucket[5m])))

  # 3. Fallback rate
  rate(wordflux_llm_fallback_total[5m])

  # 4. Error rate
  sum by (status) (rate(wordflux_chat_requests_total{status="error"}[5m]))

  # 5. Daily cost
  wordflux_chat_cost_usd_daily

  # 6. Active sessions (proxy: requests/min)
  rate(wordflux_chat_requests_total[1m])
  ```
- [ ] Alertas configurados (3 critical):
  ```yaml
  - alert: ChatFallbackRateHigh
    expr: rate(wordflux_llm_fallback_total[10m]) > 0.15
    for: 5m
    severity: critical

  - alert: ChatErrorRateHigh
    expr: rate(wordflux_chat_requests_total{status="error"}[5m]) > 0.05
    for: 5m
    severity: critical

  - alert: ChatLatencyHigh
    expr: histogram_quantile(0.95, rate(wordflux_chat_latency_seconds_bucket[5m])) > 5
    for: 10m
    severity: critical
  ```
- [ ] Log aggregation validado: `sudo journalctl -u wordflux-cockpit -f`
- [ ] Runbook de rollback acessível (link ou impresso)

---

## ☑️ STAGING VALIDATION (BLOQUEANTE PARA PROD)

### Deploy em Staging
- [ ] Branch deployed: `git checkout feat/chat-agent-cockpit`
- [ ] Services started: `wordflux-{api,cockpit,worker}`
- [ ] Health checks green: `curl http://staging/health`
- [ ] Metrics endpoint acessível: `curl http://staging/metrics | wc -l` > 50 linhas

### Testes Funcionais
- [ ] Guardrail smoke test **PASSOU** (4/4):
  ```bash
  python scripts/guardrail_smoke_test.py --base-url http://staging:8081
  ```
- [ ] Teste manual de cada cenário:
  - [ ] Low-risk: "listar minhas tasks" → sem approval
  - [ ] Medium-risk: "mover c-123 para Aprovação" → pending_approval
  - [ ] High-risk: "publicar tudo" → pending_approval + risk=high
  - [ ] Approval flow: aprovar proposta → job_id retornado

### Canary Testing (Staging)
- [ ] Canary 10% testado:
  ```bash
  export WF_CANARY_PCT=10
  for i in {1..100}; do
    curl -X POST http://staging:8081/chat \
      -d "{\"session_id\":\"test-$i\",\"message\":\"hello\"}"
  done
  # Verificar distribuição:
  curl http://staging:8081/metrics | grep wordflux_chat_requests_total
  # Deve mostrar ~10 anthropic, ~90 openai
  ```

### Fallback Testing
- [ ] Fallback forçado funciona:
  ```bash
  # Bloquear Anthropic temporariamente (firewall ou key inválida)
  export ANTHROPIC_API_KEY=invalid
  # Enviar request
  curl -X POST http://staging:8081/chat \
    -d '{"session_id":"fallback-test","message":"hello"}'
  # Verificar logs: deve usar OpenAI como fallback
  sudo journalctl -u wordflux-cockpit -n 20 | grep fallback
  ```

### Métricas & Auditoria
- [ ] Métricas coletadas corretamente:
  ```bash
  curl http://staging:8081/metrics | grep -E "wordflux_chat_(requests|latency|cost|fallback)"
  # Todos os contadores devem estar presentes
  ```
- [ ] Auditoria Redis populada:
  ```bash
  redis-cli LRANGE wf:chat:audit 0 10 | jq
  # Deve ter entries com: ts, session_id, tools, outcome, provider
  ```
- [ ] Cost tracking funcionando:
  ```bash
  redis-cli GET wf:chat:cost:$(date +%Y-%m-%d)
  # Deve retornar valor > 0 após alguns requests
  ```

### Rollback Testing (CRÍTICO)
- [ ] Rollback R1 testado (desabilitar canary):
  ```bash
  sudo systemctl set-environment WF_CANARY_PCT=0
  sudo systemctl restart wordflux-cockpit
  # Validar: canary traffic deve ir para 0
  ```
- [ ] Tempo de rollback medido: _____ minutos (target < 2 min)
- [ ] Serviço volta ao normal após rollback

---

## ☑️ PRODUCTION DEPLOYMENT - PHASE 0 (BASELINE)

**Objetivo:** Deploy código novo com canary DESABILITADO para validar baseline

### Pre-Flight Checks
- [ ] Todos os items de PRÉ-DEPLOYMENT completos
- [ ] Todos os items de STAGING VALIDATION completos
- [ ] Equipe on-call alertada: deployment iniciando às ___:___ UTC
- [ ] Runbook de rollback aberto e pronto
- [ ] Dashboard de métricas aberto e monitorando

### Backup & Deploy
- [ ] Backup completo:
  ```bash
  ssh ubuntu@wordflux.3-228-174-188.nip.io
  sudo systemctl stop wordflux-{api,cockpit,worker}
  redis-cli SAVE
  sudo cp -r /home/ubuntu /home/ubuntu.backup.$(date +%Y%m%d-%H%M%S)
  ```
- [ ] Git pull para produção:
  ```bash
  cd /home/ubuntu
  git fetch origin
  git checkout feat/chat-agent-cockpit
  git pull origin feat/chat-agent-cockpit
  ```
- [ ] Verificar env vars:
  ```bash
  cat wordflux.env | grep -E "CANARY|ANTHROPIC|BUDGET"
  # Confirmar: WF_CANARY_PCT=0
  ```
- [ ] Restart services:
  ```bash
  sudo systemctl daemon-reload
  sudo systemctl start wordflux-{api,cockpit,worker}
  ```

### Validação Baseline
- [ ] Health checks OK:
  ```bash
  curl http://wordflux.3-228-174-188.nip.io/health | jq '.status'
  # Deve retornar "ok"
  ```
- [ ] Métricas endpoint acessível:
  ```bash
  curl http://wordflux.3-228-174-188.nip.io/metrics | grep wordflux_chat | wc -l
  # Deve retornar > 10 linhas
  ```
- [ ] Nenhum erro nos logs:
  ```bash
  sudo journalctl -u wordflux-cockpit --since "5 minutes ago" | grep -i error
  # Não deve ter ERROR crítico
  ```
- [ ] Smoke test básico:
  ```bash
  curl -X POST http://wordflux.3-228-174-188.nip.io/chat \
    -d '{"session_id":"baseline-test","message":"hello"}'
  # Deve retornar 200 OK
  ```

### Monitoramento Baseline (30 min)
- [ ] Métricas estáveis por 30 minutos
- [ ] Fallback rate = 0% (canary desabilitado)
- [ ] Error rate < 1%
- [ ] Latency p95 < 3s (baseline OpenAI)
- [ ] Nenhum alerta disparado

**GATE:** Se baseline não está estável, **NÃO PROSSEGUIR** com canary. Investigar e resolver.

---

## ☑️ PRODUCTION DEPLOYMENT - PHASE 1 (CANARY 5%)

**Objetivo:** Ativar canary 5% e monitorar por 60 minutos

### Ativação
- [ ] Timestamp início: ___:___ UTC
- [ ] Ativar canary 5%:
  ```bash
  ssh ubuntu@wordflux.3-228-174-188.nip.io
  sudo systemctl set-environment WF_CANARY_PCT=5
  sudo systemctl restart wordflux-cockpit
  ```
- [ ] Validar routing:
  ```bash
  curl http://wordflux.3-228-174-188.nip.io/metrics | grep 'wordflux_chat_requests_total{provider="anthropic"'
  # Contador deve existir e começar a incrementar
  ```

### Monitoramento Ativo (60 min)
Verificar a cada 10 minutos e anotar valores:

| Tempo | Fallback % | Error % | Latency p95 Anthropic | Latency p95 OpenAI | Cost/Request | Alertas |
|-------|------------|---------|----------------------|-------------------|--------------|---------|
| +10min | ___% | ___% | ___s | ___s | $____ | ☐ |
| +20min | ___% | ___% | ___s | ___s | $____ | ☐ |
| +30min | ___% | ___% | ___s | ___s | $____ | ☐ |
| +40min | ___% | ___% | ___s | ___s | $____ | ☐ |
| +50min | ___% | ___% | ___s | ___s | $____ | ☐ |
| +60min | ___% | ___% | ___s | ___s | $____ | ☐ |

### Critérios de Sucesso
- [ ] Fallback rate < 5% (média 60 min)
- [ ] Error rate < 1%
- [ ] Latency p95 Anthropic < 3s
- [ ] Latency p95 não degradou vs baseline (< +20%)
- [ ] Nenhum alerta crítico
- [ ] Nenhum guardrail bypass detectado (audit log)

**GATE:** Se qualquer critério falhar, **EXECUTAR ROLLBACK R1** imediatamente.

### Smoke Tests em Produção
- [ ] Executar `guardrail_smoke_test.py` contra produção
- [ ] Todos os 4 cenários passando
- [ ] Nenhuma regressão vs staging

---

## ☑️ PRODUCTION DEPLOYMENT - PHASE 2 (CANARY 10%)

**Objetivo:** Incrementar para 10% e monitorar por 60 minutos

### Pré-Requisitos
- [ ] Phase 1 completada com sucesso
- [ ] Métricas da Phase 1 validadas e dentro dos thresholds

### Ativação
- [ ] Timestamp início: ___:___ UTC
- [ ] Incrementar para 10%:
  ```bash
  sudo systemctl set-environment WF_CANARY_PCT=10
  sudo systemctl restart wordflux-cockpit
  ```

### Monitoramento (60 min)
Mesma tabela de monitoramento da Phase 1. **CRITÉRIOS IDÊNTICOS.**

### Validação Final Phase 2
- [ ] Todas as métricas dentro dos thresholds
- [ ] Pelo menos 50 requests reais de usuários processados via Anthropic
- [ ] Nenhum feedback negativo de usuários (se houver canais de suporte)

**GATE:** Decidir se continuar para Phase 3 (25%) ou manter 10% por mais tempo.

---

## ☑️ GO/NO-GO DECISION POINT

**Critérios para GO (continuar incremento gradual):**
- [ ] Fallback rate < 5% em ambas as phases
- [ ] Error rate < 1% consistente
- [ ] Latency não degradou (< +20% vs baseline)
- [ ] Nenhum incidente crítico
- [ ] Custo por request < 4x OpenAI (esperado: ~4x)
- [ ] Feedback inicial neutro/positivo

**Critérios para NO-GO (rollback e replanejamento):**
- [ ] Qualquer critério GO não atendido
- [ ] Alertas frequentes
- [ ] Guardrail bypasses detectados
- [ ] Custo insustentável (> 5x OpenAI)

**DECISÃO:** ☐ GO  ☐ NO-GO  ☐ HOLD (manter 10% e monitorar mais)

**Justificativa:**
______________________________________________________________________
______________________________________________________________________

**Aprovado por:** _______________ Data: ___________

---

## ☑️ ROLLOUT INCREMENTAL (DAYS 4-7) - SE GO APROVADO

### Day 4: 25% Canary
- [ ] `WF_CANARY_PCT=25`
- [ ] Monitorar por 12 horas
- [ ] Métricas validadas

### Day 5: 50% Canary (A/B Test)
- [ ] `WF_CANARY_PCT=50`
- [ ] Monitorar por 24 horas
- [ ] Comparação detalhada OpenAI vs Anthropic:
  - [ ] Latency: ___s vs ___s
  - [ ] Error rate: ___% vs ___%
  - [ ] Tool call accuracy: ___% vs ___%
  - [ ] Cost per 1k requests: $___ vs $___

### Day 6: 75% Canary
- [ ] `WF_CANARY_PCT=75`
- [ ] Monitorar por 12 horas
- [ ] Preparar análise final

### Day 7: Decisão Final
- [ ] **OPÇÃO A:** 100% Anthropic (OpenAI como fallback)
  ```bash
  WF_LLM_PROVIDER=anthropic
  WF_LLM_PROVIDER_FALLBACK=openai
  WF_CANARY_PCT=0  # Desabilitar canary system
  ```
- [ ] **OPÇÃO B:** Split permanente (ex: 75/25)
  ```bash
  WF_CANARY_PCT=75
  ```
- [ ] **OPÇÃO C:** Rollback completo para 100% OpenAI

**DECISÃO FINAL:** ________

**Documentado em:** `docs/post-mortems/2025-09-30-anthropic-rollout.md`

---

## ☑️ POST-DEPLOYMENT

### Finalização
- [ ] Tag de release criada: `git tag v1.1.0-chat-anthropic`
- [ ] Documentação atualizada (README, CHAT_SETUP.md)
- [ ] Post-deployment review agendado (data: _________)
- [ ] Métricas exportadas para análise: `curl /metrics > metrics-final.txt`
- [ ] Auditoria de custos: total gasto em 7 dias = $______

### Lições Aprendidas
1. O que funcionou bem: ____________________________________
2. O que não funcionou: ____________________________________
3. O que faríamos diferente: ____________________________________
4. Action items: ____________________________________

### Próximos Passos
- [ ] UI de chat (se não implementada): issue #___
- [ ] Email consolidation: issue #___
- [ ] Card "pending approval" styling: issue #___
- [ ] Otimização de custos (caching, model selection): issue #___
- [ ] A/B testing framework formalizado: issue #___

---

## 📞 CONTATOS DE EMERGÊNCIA

**On-Call Engineer:** _____________ (phone: __________)
**Tech Lead:** _____________ (phone: __________)
**Slack Channel:** #wordflux-incidents
**Runbook Rollback:** `docs/runbooks/chat-rollback.md`

---

**Checklist Completo:** _____ / _____ items
**Data de Conclusão:** ___________
**Assinatura:** _______________
