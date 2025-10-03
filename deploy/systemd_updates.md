# Systemd Service Updates for Chat Agent

## Overview

The chat agent requires additional environment variables to be configured in the systemd service files. These variables control the LLM provider, API keys, and chat-specific settings.

## Affected Services

### 1. `wordflux-cockpit.service`

The cockpit service needs chat configuration as it hosts the chat API.

**Location:** `/etc/systemd/system/wordflux-cockpit.service`

**Environment Variables to Add:**

```ini
[Service]
# Existing environment variables...
Environment="PORT=8081"
Environment="QUEUE_MODE=redis"
Environment="REDIS_URL=redis://localhost:6379/0"

# ADD THESE FOR CHAT AGENT:
Environment="OPENAI_API_KEY=sk-proj-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
Environment="WF_LLM_PROVIDER=openai"
Environment="OPENAI_CHAT_MODEL=gpt-4o-mini"
Environment="WF_CHAT_TTL_SEC=86400"
Environment="WF_RATELIMIT_PER_MIN=20"
```

**For Local Development/Testing (Mock Provider):**

```ini
# Use mock provider (no OpenAI API calls, no cost)
Environment="WF_LLM_PROVIDER=mock"
# OPENAI_API_KEY not required with mock provider
```

**Apply Changes:**

```bash
# Reload systemd daemon
sudo systemctl daemon-reload

# Restart cockpit service
sudo systemctl restart wordflux-cockpit

# Check status
sudo systemctl status wordflux-cockpit

# View logs
sudo journalctl -u wordflux-cockpit -f
```

---

### 2. `wordflux-api.service` (Optional)

The API service doesn't directly use chat features, but if you want to consolidate configuration:

**Location:** `/etc/systemd/system/wordflux-api.service`

**Environment Variables (Optional):**

```ini
[Service]
# Same chat variables as cockpit if needed for consistency
# Generally not required unless API service also hosts chat endpoints
```

---

### 3. `wordflux-worker.service` (No Changes Required)

The worker service processes jobs and doesn't need chat-specific configuration.

---

## Configuration Options

### LLM Provider Options

| Variable | Value | Description |
|----------|-------|-------------|
| `WF_LLM_PROVIDER` | `openai` | Use OpenAI API (requires `OPENAI_API_KEY`) |
| `WF_LLM_PROVIDER` | `mock` | Use mock provider (no external calls, for testing) |

### OpenAI Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | None | OpenAI API key (required if `WF_LLM_PROVIDER=openai`) |
| `OPENAI_CHAT_MODEL` | `gpt-4o-mini` | OpenAI model to use (gpt-4o-mini, gpt-4, gpt-3.5-turbo) |

### Chat Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `WF_CHAT_TTL_SEC` | `86400` | Session history TTL (24 hours) |
| `WF_RATELIMIT_PER_MIN` | `20` | Max requests per minute per IP |

---

## Security Considerations

### API Key Management

**❌ Don't:** Store API keys directly in systemd service files in production

**✅ Do:** Use a secrets management solution:

#### Option 1: Environment File

```bash
# Create secrets file
sudo mkdir -p /etc/wordflux
sudo nano /etc/wordflux/secrets.env
```

```ini
# /etc/wordflux/secrets.env
OPENAI_API_KEY=sk-proj-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

```bash
# Set restrictive permissions
sudo chmod 600 /etc/wordflux/secrets.env
sudo chown root:root /etc/wordflux/secrets.env
```

**Update service file:**

```ini
[Service]
EnvironmentFile=/etc/wordflux/secrets.env
# Other environment variables...
```

#### Option 2: AWS Secrets Manager (Production)

```bash
# Fetch secret at runtime
ExecStartPre=/usr/local/bin/fetch-secrets.sh
EnvironmentFile=/tmp/wordflux-secrets.env
```

---

## Verification

### 1. Check Service Status

```bash
sudo systemctl status wordflux-cockpit
```

**Expected output:**
- Status: `active (running)`
- No errors in recent logs
- "Chat router registered at /chat" in logs

### 2. Test Chat Endpoint

```bash
# Using mock provider
curl -X POST http://localhost:8081/chat/ \
  -H "Content-Type: application/json" \
  -d '{"message":"Olá","session_id":"test"}'
```

**Expected response:**
```json
{
  "message": "Olá! Sou o Assistente WordFlux...",
  "role": "assistant",
  "tool_calls": [],
  "requires_approval": false,
  "session_id": "test"
}
```

### 3. Check Logs for Errors

```bash
# View recent logs
sudo journalctl -u wordflux-cockpit -n 50

# Follow logs in real-time
sudo journalctl -u wordflux-cockpit -f

# Filter for chat-related logs
sudo journalctl -u wordflux-cockpit | grep -i chat
```

### 4. Verify Environment Variables

```bash
# Check environment of running process
sudo systemctl show wordflux-cockpit --property=Environment
```

---

## Troubleshooting

### Issue: "OpenAI API key required" Error

**Symptom:** Cockpit fails to start or chat endpoint returns 500 error

**Cause:** `WF_LLM_PROVIDER=openai` but no `OPENAI_API_KEY` set

**Solution:**
```bash
# Option 1: Use mock provider
sudo systemctl edit wordflux-cockpit
# Add: Environment="WF_LLM_PROVIDER=mock"

# Option 2: Add API key
sudo systemctl edit wordflux-cockpit
# Add: Environment="OPENAI_API_KEY=sk-proj-..."
```

### Issue: Rate Limiting Too Strict/Loose

**Symptom:** Users being blocked too frequently or not enough

**Solution:**
```bash
# Adjust rate limit
sudo systemctl edit wordflux-cockpit
# Modify: Environment="WF_RATELIMIT_PER_MIN=50"

# Restart
sudo systemctl restart wordflux-cockpit
```

### Issue: Chat History Not Persisting

**Symptom:** Chat history lost between sessions

**Cause:** Redis not configured or TTL too short

**Solution:**
```bash
# Check Redis
redis-cli ping  # Should return PONG

# Check TTL setting
sudo systemctl show wordflux-cockpit --property=Environment | grep CHAT_TTL

# Adjust if needed
sudo systemctl edit wordflux-cockpit
# Add: Environment="WF_CHAT_TTL_SEC=172800"  # 48 hours
```

---

## Rollback

If chat agent causes issues, disable it temporarily:

```bash
# Option 1: Use mock provider (safe mode)
sudo systemctl edit wordflux-cockpit
# Add: Environment="WF_LLM_PROVIDER=mock"
sudo systemctl restart wordflux-cockpit

# Option 2: Revert to previous version
sudo systemctl stop wordflux-cockpit
cd /home/ubuntu/wordflux
git checkout main  # Or previous commit
sudo systemctl start wordflux-cockpit
```

---

## Monitoring

### Key Metrics to Monitor

1. **Chat API Errors:**
   ```bash
   sudo journalctl -u wordflux-cockpit | grep -i "chat error"
   ```

2. **Rate Limit Hits:**
   ```bash
   # Via Prometheus
   curl http://localhost:9300/metrics | grep chat_rate_limit_hits
   ```

3. **OpenAI API Latency:**
   ```bash
   # Check response times in logs
   sudo journalctl -u wordflux-cockpit | grep -i "llm"
   ```

---

## Complete Service File Example

```ini
[Unit]
Description=WordFlux Cockpit with Chat Agent
After=network.target redis.service
Wants=redis.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu
EnvironmentFile=/etc/wordflux/secrets.env
Environment="PORT=8081"
Environment="QUEUE_MODE=redis"
Environment="REDIS_URL=redis://localhost:6379/0"
Environment="WF_LLM_PROVIDER=openai"
Environment="OPENAI_CHAT_MODEL=gpt-4o-mini"
Environment="WF_CHAT_TTL_SEC=86400"
Environment="WF_RATELIMIT_PER_MIN=20"
ExecStart=/home/ubuntu/.venv/bin/python playbooks/cockpit/wordflux_cockpit.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

---

## References

- [systemd Environment Variables](https://www.freedesktop.org/software/systemd/man/systemd.exec.html#Environment)
- [OpenAI API Keys](https://platform.openai.com/api-keys)
- [Redis Configuration](https://redis.io/docs/manual/config/)