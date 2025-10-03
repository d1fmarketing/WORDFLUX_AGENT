# Chat Agent Setup Guide

## Overview

WordFlux Cockpit now includes a conversational AI interface (Chat Agent) that enables users to interact with the workflow system through natural language in Portuguese-BR. This guide covers setup, testing, and usage.

---

## Prerequisites

- Python 3.11+
- Redis running (for session storage and rate limiting)
- OpenAI API key (or use mock provider for testing)

---

## Quick Start

### 1. Install Dependencies

```bash
# Install chat agent dependencies
make dev

# Or manually:
pip install -r requirements.txt
```

**New Dependency:** `openai>=1.52.0` (added for LLM integration)

### 2. Configure Environment

```bash
# Copy example configuration
cp .env.example .env

# Edit .env with your settings
nano .env
```

**Required Configuration:**

```bash
# LLM Provider (choose one)
WF_LLM_PROVIDER=mock          # For testing (no API costs)
# WF_LLM_PROVIDER=openai      # For production

# OpenAI Configuration (only if using openai provider)
OPENAI_API_KEY=sk-proj-...
OPENAI_CHAT_MODEL=gpt-4o-mini

# Chat Configuration
WF_CHAT_TTL_SEC=86400          # Session history TTL (24 hours)
WF_RATELIMIT_PER_MIN=20        # Rate limit per IP
```

### 3. Start Cockpit

```bash
# Using mock provider (recommended for local testing)
export WF_LLM_PROVIDER=mock
.venv/bin/python playbooks/cockpit/wordflux_cockpit.py

# Cockpit available at: http://localhost:8081
```

### 4. Test Chat Interface

Open your browser to `http://localhost:8081` and you'll see:

- **Left Panel (40%):** Chat interface with WordFlux IA
- **Right Panel (60%):** Kanban workflow board

Try these quick actions:
- **"Mostrar minhas tarefas"** - Lists your assigned cards
- **"Planeje o amanhã"** - Triggers autonomous planner
- **"Limpar Concluídos"** - Archives completed cards

---

## Mock vs. OpenAI Provider

### Mock Provider (Recommended for Development)

**Advantages:**
- ✅ No API costs
- ✅ Deterministic responses (good for testing)
- ✅ Fast (<50ms latency)
- ✅ No external dependencies
- ✅ Works offline

**Pattern Recognition:**
- "Mostrar tarefas" → Calls `summarize` tool
- "Mover card c-xxx para Produção" → Calls `propose_move` tool
- "Planeje o amanhã" → Calls `queue_job` for planner_agent
- "Limpar Concluídos" → Calls cleanup action

**Setup:**
```bash
export WF_LLM_PROVIDER=mock
# No API key needed
```

### OpenAI Provider (Production)

**Advantages:**
- ✅ Natural language understanding
- ✅ Flexible conversation flow
- ✅ Contextual responses
- ✅ Handles edge cases gracefully

**Costs:** ~$0.0001 per message (gpt-4o-mini)

**Setup:**
```bash
export WF_LLM_PROVIDER=openai
export OPENAI_API_KEY=sk-proj-...
export OPENAI_CHAT_MODEL=gpt-4o-mini  # Or gpt-4, gpt-3.5-turbo
```

---

## Features

### 1. Chat Interface (PT-BR)

All user-facing text is in Portuguese-BR:
- Input placeholder: "Envie sua mensagem…"
- Quick actions: "Minhas tarefas", "Planeje amanhã", "Limpar"
- Approval button: "Aprovar"
- Agent responses in natural Portuguese

### 2. Approval Workflow

**High-Risk Actions** require explicit user approval:
- Moving cards to "Produção" or "Finalizado"
- Bulk card creation
- Content publishing

**Workflow:**
```
User: "Mover card c-123 para Produção"
  ↓
Agent: Proposes action
  ↓
UI: Shows "Aprovar" button
  ↓
User: Clicks "Aprovar"
  ↓
System: Enqueues job, executes action
```

**Low-Risk Actions** execute immediately:
- Querying card status
- Viewing tasks
- Generating summaries

### 3. Rate Limiting

- **Limit:** 20 requests per minute per IP (configurable)
- **Response:** HTTP 429 when exceeded
- **Storage:** Redis-backed with sliding window

### 4. Session Management

- **Session ID:** Auto-generated (format: `sess-xxxxxxxx`)
- **Storage:** Redis LIST with message history
- **TTL:** 24 hours (configurable)
- **Max Messages:** 50 per session (auto-trimmed)

### 5. Audit Logging

All state-changing actions logged to `wf:chat:audit`:
- Proposals created
- Approvals granted
- Jobs enqueued
- Retention: 1000 most recent entries

---

## Testing

### Run Unit Tests

```bash
# Test LLM client and chat API
make chat-test

# Or manually:
WF_LLM_PROVIDER=mock pytest tests/unit/test_llm_bridge.py tests/unit/test_chat_api.py -xvs
```

**Expected Output:**
```
==================== test session starts ====================
tests/unit/test_llm_bridge.py::test_tool_schemas_structure PASSED
tests/unit/test_llm_bridge.py::test_mock_llm_returns_valid_response PASSED
tests/unit/test_chat_api.py::test_chat_endpoint_basic_message PASSED
...
==================== 25 passed in 3.21s ====================
```

### Run Smoke Test

```bash
# End-to-end integration test
make chat-smoke

# Or manually:
WF_LLM_PROVIDER=mock python scripts/chat_smoke.py
```

**Expected Output:**
```
🔥 WordFlux Chat Agent - Smoke Test Suite
============================================================
✅ Health check passed
✅ Low-risk action executed immediately
✅ Proposal created: prop-xyz789
✅ Approval succeeded, job queued: chat-abc123
✅ Invalid proposal correctly rejected (404)
✅ Rate limiting works (20/20 allowed)
✅ History retrieved: 2 messages
✅ Quick actions: 100% success rate
============================================================
📊 Test Summary
============================================================
  health               ✅ PASS
  low_risk             ✅ PASS
  high_risk            ✅ PASS
  approval             ✅ PASS
  invalid_approval     ✅ PASS
  rate_limiting        ✅ PASS
  history              ✅ PASS
  quick_actions        ✅ PASS
============================================================
  Total: 8/8 tests passed (100%)
============================================================
🎉 All smoke tests passed!
```

---

## Production Deployment

### 1. Configure Systemd Service

See `deploy/systemd_updates.md` for detailed instructions.

**Key Environment Variables:**
```ini
[Service]
Environment="OPENAI_API_KEY=sk-proj-..."
Environment="WF_LLM_PROVIDER=openai"
Environment="OPENAI_CHAT_MODEL=gpt-4o-mini"
Environment="WF_CHAT_TTL_SEC=86400"
Environment="WF_RATELIMIT_PER_MIN=20"
```

**Apply Changes:**
```bash
sudo systemctl daemon-reload
sudo systemctl restart wordflux-cockpit
```

### 2. Configure Nginx

See `deploy/nginx_chat_snippets.conf` for complete configuration.

**Key Points:**
- Disable buffering for `/chat` endpoint
- Set appropriate timeouts (30s)
- Optional: Add Nginx-level rate limiting

```nginx
location /chat {
    proxy_pass http://localhost:8081/chat;
    proxy_buffering off;  # Important
    proxy_http_version 1.1;
    proxy_set_header X-Real-IP $remote_addr;
}
```

**Apply Changes:**
```bash
sudo nginx -t
sudo systemctl reload nginx
```

### 3. Verify Deployment

```bash
# Check service status
sudo systemctl status wordflux-cockpit

# Test chat endpoint
curl -X POST http://localhost:8081/chat/ \
  -H "Content-Type: application/json" \
  -d '{"message":"Olá","session_id":"prod-test"}'

# Check logs
sudo journalctl -u wordflux-cockpit -f
```

---

## Monitoring

### Prometheus Metrics

New chat-specific metrics available at `http://localhost:9300/metrics`:

```
# Total chat messages by role
wordflux_chat_messages_total{role="user"}
wordflux_chat_messages_total{role="assistant"}

# Tool invocations
wordflux_chat_tool_calls_total{tool="suggest_actions"}
wordflux_chat_tool_calls_total{tool="propose_move"}

# Pending approvals (gauge)
wordflux_chat_pending_approvals

# Rate limit hits
wordflux_chat_rate_limit_hits_total
```

### Example PromQL Queries

```promql
# Chat messages per minute
rate(wordflux_chat_messages_total[5m])

# Approval rate
sum(rate(wordflux_chat_tool_calls_total{tool=~".*approve.*"}[5m])) /
sum(rate(wordflux_chat_messages_total[5m]))

# Rate limit hit rate
rate(wordflux_chat_rate_limit_hits_total[5m])
```

### Grafana Dashboard

Create a dashboard with panels for:
1. **Chat Activity:** Messages per minute (user + assistant)
2. **Tool Usage:** Breakdown by tool type
3. **Approval Workflow:** Pending vs approved over time
4. **Rate Limiting:** Hits and rejections
5. **Session Stats:** Active sessions, avg messages per session

---

## Troubleshooting

### Issue: "OpenAI API key required"

**Symptom:** 500 error when sending chat message

**Cause:** `WF_LLM_PROVIDER=openai` but no API key set

**Solution:**
```bash
# Option 1: Use mock provider
export WF_LLM_PROVIDER=mock

# Option 2: Set API key
export OPENAI_API_KEY=sk-proj-...
```

### Issue: Rate Limiting Too Strict

**Symptom:** Users getting 429 errors frequently

**Solution:**
```bash
# Increase rate limit
export WF_RATELIMIT_PER_MIN=50

# Restart cockpit
sudo systemctl restart wordflux-cockpit
```

### Issue: Chat History Not Persisting

**Symptom:** Previous messages not showing after refresh

**Cause:** Redis not configured or TTL too short

**Solution:**
```bash
# Check Redis
redis-cli ping  # Should return PONG

# Check session exists
redis-cli KEYS "wf:chat:hist:*"

# Increase TTL if needed
export WF_CHAT_TTL_SEC=172800  # 48 hours
```

### Issue: Approval Button Not Appearing

**Symptom:** High-risk actions don't show "Aprovar" button

**Cause:** JavaScript error or SSE connection issue

**Solution:**
```bash
# Check browser console for errors
# Check SSE connection
curl -N http://localhost:8081/events/stream

# Verify proposal stored in Redis
redis-cli --scan --pattern "wf:chat:proposal:*"
```

---

## Security Considerations

1. **API Keys:** Never commit API keys to version control. Use environment files or secrets manager.

2. **Rate Limiting:** Default 20 req/min should prevent abuse. Adjust based on your user base.

3. **Approval Workflow:** High-risk actions ALWAYS require approval. Don't bypass this in production.

4. **Audit Logging:** Review `wf:chat:audit` regularly for suspicious activity.

5. **Session Isolation:** Sessions are isolated by session_id. Don't share session IDs across users.

---

## Additional Resources

- **Full Specification:** [docs/wordflux_cockpit_chat.md](docs/wordflux_cockpit_chat.md)
- **Security Policies:** [docs/security.md](docs/security.md)
- **Nginx Configuration:** [deploy/nginx_chat_snippets.conf](deploy/nginx_chat_snippets.conf)
- **Systemd Setup:** [deploy/systemd_updates.md](deploy/systemd_updates.md)

---

## Anthropic Claude Sonnet 4.5 Rollout Plan

### Overview

This section describes the strategy for deploying Claude Sonnet 4.5 as an alternative LLM provider to WordFlux Cockpit in a controlled, data-driven manner.

### Phase 1: Dev/Staging Testing (Week 1)

**Objective:** Verify Anthropic integration works correctly without impacting production.

**Steps:**
1. Deploy to staging environment with `WF_LLM_PROVIDER=anthropic`
2. Configure fallback: `WF_LLM_PROVIDER_FALLBACK=openai`
3. Run full smoke test suite: `WF_LLM_PROVIDER=anthropic make chat-smoke`
4. Manual QA: Test all 5 tools (suggest_actions, propose_move, queue_job, bulk_from_email, summarize)
5. Verify fallback behavior: Trigger ASL-3 block with test case containing "química" keyword

**Success Criteria:**
- All smoke tests pass (8/8)
- Fallback triggers correctly on ASL-3 blocks
- No regressions compared to OpenAI baseline
- Latency p95 < 2000ms

### Phase 2: A/B Testing Pilot (Week 2)

**Objective:** Compare Anthropic vs OpenAI performance with real traffic.

**Implementation:**
Add feature flag logic to `src/api/chat.py`:

```python
def get_provider_for_session(session_id: str) -> str:
    """Assign provider based on session hash (10% to anthropic)."""
    import hashlib
    hash_val = int(hashlib.md5(session_id.encode()).hexdigest(), 16)
    if hash_val % 10 == 0:  # 10% of traffic
        return "anthropic"
    return "openai"
```

**Monitoring:**
Set up Prometheus alerts:

```yaml
- alert: AnthropicHighCost
  expr: rate(wordflux_chat_messages_total{provider="anthropic"}[1h]) * 15 > 100
  for: 5m
  annotations:
    summary: "Anthropic costs exceeding budget"

- alert: AnthropicHighLatency
  expr: histogram_quantile(0.95, rate(wordflux_chat_tool_calls_total{provider="anthropic"}[5m])) > 3
  for: 10m
  annotations:
    summary: "Anthropic p95 latency > 3s"

- alert: HighFallbackRate
  expr: rate(wordflux_llm_fallback_total[5m]) > 0.1
  for: 5m
  annotations:
    summary: "Fallback rate exceeding 10%"
```

**Metrics to Compare:**

| Metric | OpenAI Target | Anthropic Target | Current |
|--------|---------------|------------------|---------|
| **Latency p50** | < 800ms | < 600ms | - |
| **Latency p95** | < 2000ms | < 1500ms | - |
| **Tool Call Accuracy** | 85-90% | > 90% | - |
| **Cost per 1K messages** | $0.08 | $0.40 | - |
| **Fallback Rate** | 0% | < 5% | - |
| **User Satisfaction (subjective)** | Baseline | +10% | - |

**Data Collection:**
Run pilot for **2 weeks** minimum, collecting:
- Latency percentiles (p50, p95, p99) per provider
- Tool call success rate (jobs enqueued vs errors)
- Fallback reasons breakdown (asl3_block vs runtime_error)
- Cost per message (input + output tokens)
- User feedback via optional survey link in chat

### Phase 3: Decision Point (End of Week 2)

**Decision Matrix:**

| Outcome | Action |
|---------|--------|
| **Anthropic wins on latency + accuracy, cost acceptable** | Proceed to Phase 4 (gradual rollout) |
| **Anthropic equal to OpenAI, cost 5x higher** | Keep at 10%, use for premium features only |
| **Anthropic underperforms OpenAI** | Rollback to 0%, revisit after model improvements |
| **High ASL-3 false positive rate (> 5%)** | Escalate to Anthropic support, request filter tuning |

### Phase 4: Gradual Rollout (Weeks 3-4)

**If Phase 3 decision is GO:**

- **Week 3:** Increase to 25% traffic
- **Week 4:** Increase to 50% traffic
- **Week 5:** Increase to 75% traffic
- **Week 6:** Full migration to 100% (OpenAI as fallback)

**Rollback Plan:**
If any critical issues detected:
1. Immediately revert to previous percentage via env var update
2. Restart cockpit service: `sudo systemctl restart wordflux-cockpit`
3. Verify rollback via `/health` endpoint checking provider counts
4. Post-mortem analysis within 24 hours

### Phase 5: Optimization (Ongoing)

**Cost Optimization:**
- Negotiate enterprise pricing with Anthropic (volume > 10M tokens/month)
- Implement prompt caching for repeated system messages
- Use shorter models (claude-sonnet-4 instead of 4-5) for simple queries

**Performance Tuning:**
- Optimize max_tokens parameter (current: 1024, test: 512)
- Implement response streaming for better perceived latency
- Cache frequent tool responses (suggest_actions per card)

### Rollback Procedure

**Emergency Rollback (< 5 minutes):**

```bash
# 1. Update env var
echo "WF_LLM_PROVIDER=openai" | sudo tee -a /etc/systemd/system/wordflux-cockpit.service.d/override.conf

# 2. Restart service
sudo systemctl daemon-reload
sudo systemctl restart wordflux-cockpit

# 3. Verify
curl http://localhost:8081/health | jq '.llm_provider'
# Should return: "openai"
```

**Planned Rollback (with testing):**

1. Update `.env` or systemd override with `WF_LLM_PROVIDER=openai`
2. Test in staging first
3. Deploy to production during maintenance window
4. Monitor for 1 hour post-rollback

### Success Metrics Dashboard

Create Grafana dashboard with panels:

```promql
# Provider Distribution
sum by (provider) (rate(wordflux_chat_messages_total[5m]))

# Latency Comparison
histogram_quantile(0.95, sum by (provider, le) (rate(wordflux_chat_tool_calls_duration_bucket[5m])))

# Fallback Rate
rate(wordflux_llm_fallback_total[5m])

# Cost Estimate (messages/hour * cost per 1K)
sum(rate(wordflux_chat_messages_total{provider="anthropic"}[1h])) * 60 * 0.40
```

### Communication Plan

**Internal Stakeholders:**
- **Week 0:** Notify engineering team of pilot start date
- **Week 2:** Share A/B test results + decision
- **Week 6:** Announce completion of migration (if successful)

**Users:**
- No user-facing changes required (transparent fallback)
- Add banner to UI if fallback triggered: "🔄 Usando modelo alternativo"
- Optional: Add provider indicator in chat UI for transparency

---

## FAQ

**Q: Can I use the chat agent without OpenAI?**
A: Yes! Use `WF_LLM_PROVIDER=mock` for a fully functional chat without external dependencies. You can also use `WF_LLM_PROVIDER=anthropic` with an Anthropic API key.

**Q: How much does OpenAI cost?**
A: With gpt-4o-mini, approximately $0.0001 per message. Anthropic Sonnet 4.5 costs ~$0.0004 per message (4x more, but potentially higher quality).

**Q: What's the difference between Anthropic and OpenAI?**
A: Anthropic Claude Sonnet 4.5 offers better performance on coding tasks (77.2% SWE-bench vs 60.5% GPT-4o), faster responses (2-3x), and longer context (200K tokens). However, it costs 4-5x more and has ASL-3 safety filters that may block certain content.

**Q: What happens if Anthropic blocks my request?**
A: The system automatically falls back to OpenAI (if configured via `WF_LLM_PROVIDER_FALLBACK`). You'll see a banner: "🔄 Usando modelo alternativo". The fallback is logged for analysis.

**Q: Can I add custom tools/actions?**
A: Yes! Extend `src/core/llm_client.py` with new tool schemas and handle them in `src/api/chat.py`.

**Q: Is chat history encrypted?**
A: Chat history is stored in Redis in plaintext. Use Redis TLS and/or encryption-at-rest for sensitive environments.

**Q: Can multiple users share a session?**
A: Sessions are isolated by session_id. Each browser tab gets a unique session by default.

**Q: How do I export chat history?**
A: Use `GET /chat/history?session_id=xxx` to retrieve history as JSON.

---

## Support

For issues or questions:
- GitHub Issues: [wordflux/issues](https://github.com/yourusername/wordflux/issues)
- Documentation: [docs/](docs/)
- Logs: `sudo journalctl -u wordflux-cockpit -f`