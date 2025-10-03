# AWS Bedrock Setup - Guia Passo-a-Passo

**Objetivo:** Habilitar AWS Bedrock como provider LLM do WordFlux com segurança e validação completa

**Tempo Estimado:** 15-20 minutos

**Última Atualização:** 2025-09-30

---

## Pré-requisitos

Antes de começar, verifique:

- [ ] Acesso SSH à instância EC2 WordFlux
- [ ] AWS CLI v2 instalado e configurado
- [ ] Permissões IAM para criar policies, roles, instance profiles
- [ ] Python 3.10+ com boto3>=1.28.0 (já instalado no WordFlux)
- [ ] Redis rodando (porta 6379)

**Verificar pré-requisitos:**

```bash
# SSH na instância
ssh ubuntu@wordflux.3-228-174-188.nip.io

# Verificar AWS CLI
aws --version
# Esperado: aws-cli/2.x.x

# Verificar boto3
python3 -c "import boto3; print(f'boto3 {boto3.__version__}')"
# Esperado: boto3 1.40.x ou superior

# Verificar permissões AWS
aws sts get-caller-identity
# Esperado: mostrar ARN do user/role atual

# Verificar Redis
redis-cli ping
# Esperado: PONG
```

---

## Opção A: Setup Automatizado (Recomendado)

### Passo 1: Executar Script de Setup

```bash
cd /home/ubuntu

# Opção 1: IAM Instance Profile (PRODUÇÃO)
sudo ./scripts/setup_bedrock_iam.sh --instance-profile

# Opção 2: IAM User Policy (DEV/TEST)
sudo ./scripts/setup_bedrock_iam.sh --user-policy
```

**O que o script faz:**
1. ✅ Cria IAM policy minimal (`WordFluxBedrockMinimalPolicy`)
2. ✅ Cria IAM role (`WordFluxBedrockRole`) - se instance profile
3. ✅ Cria instance profile e anexa à EC2 - se instance profile
4. ✅ Anexa policy ao user - se user policy
5. ✅ Aguarda propagação de permissões (60s)
6. ✅ Testa `aws bedrock-runtime invoke-model`
7. ✅ Mostra próximos passos

**Output esperado:**

```
╔══════════════════════════════════════════════════╗
║   WordFlux - Bedrock IAM Setup Automation       ║
╚══════════════════════════════════════════════════╝

[INFO] Verificando pré-requisitos...
[SUCCESS] Pré-requisitos OK
[INFO] =========================================
[INFO] Configurando IAM Instance Profile (Produção)
[INFO] =========================================
[INFO] Criando IAM policy: WordFluxBedrockMinimalPolicy...
[SUCCESS] Policy criada: arn:aws:iam::330140023537:policy/WordFluxBedrockMinimalPolicy
[INFO] Criando IAM role: WordFluxBedrockRole...
[SUCCESS] Role criada: WordFluxBedrockRole
...
[SUCCESS] ✅ Instance profile verificado: WordFluxBedrockRole
[INFO] =========================================
[INFO] Testando Acesso ao Bedrock
[INFO] =========================================
[SUCCESS] ✅ Invoke model OK
[INFO] Resposta do modelo: Pong
[SUCCESS] =========================================
[SUCCESS] Teste de Bedrock Completo!
[SUCCESS] =========================================
```

### Passo 2: Habilitar Bedrock no WordFlux

```bash
# Editar wordflux.env
sed -i 's/WF_LLM_PROVIDER=anthropic/WF_LLM_PROVIDER=bedrock/' /home/ubuntu/wordflux.env

# Verificar mudança
grep WF_LLM_PROVIDER /home/ubuntu/wordflux.env
# Esperado: WF_LLM_PROVIDER=bedrock
```

### Passo 3: Reiniciar Serviço API

```bash
sudo systemctl restart wordflux-api

# Aguardar 5 segundos
sleep 5

# Verificar status
sudo systemctl status wordflux-api --no-pager

# Verificar logs
sudo journalctl -u wordflux-api -n 30 | grep -i bedrock
```

**Logs esperados:**

```
🤖 Usando LLM provider: bedrock
🤖 BedrockClient inicializado (provider=bedrock, modelo: us.anthropic.claude-sonnet-4-5-20250929-v1:0, region: us-east-1)
```

### Passo 4: Validação End-to-End

```bash
# Test 1: Chat endpoint
curl -s -X POST http://localhost:8080/chat/ \
  -H 'Content-Type: application/json' \
  -d '{"message":"Crie card \"Bedrock Test\"","session_id":"bedrock-validation"}' \
  | python3 -m json.tool
```

**Resposta esperada:**

```json
{
  "message": "Vou criar o card \"Bedrock Test\".\n\n✅ Job(s) enfileirado(s): `chat-xxxxx`",
  "tool_calls": [
    {
      "name": "create_card",
      "input": {
        "title": "Bedrock Test"
      }
    }
  ],
  "requires_approval": false
}
```

```bash
# Test 2: SSE stream
curl -N http://localhost:8081/events/stream &
CURL_PID=$!
sleep 10  # Aguardar eventos
kill $CURL_PID
```

**Eventos esperados:**

```json
data: {"kind":"heartbeat","ts":"2025-09-30T15:30:00.123Z"}
data: {"kind":"job_queued","job_id":"chat-xxxxx","session_id":"bedrock-validation"}
```

```bash
# Test 3: Health check
curl -s http://localhost:8081/health | python3 -m json.tool
```

**Health esperado:**

```json
{
  "status": "ok",
  "queue_mode": "redis",
  "redis_ok": true,
  "queue_depth": 0,
  "autopilot": false,
  "provider": "bedrock",
  "model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
}
```

### ✅ Setup Completo!

Se todos os testes passaram, o Bedrock está funcionando corretamente.

**Próximos passos:**
- Monitorar logs por 24h: `sudo journalctl -u wordflux-api -f`
- Configurar alertas CloudWatch (ver `docs/bedrock-runbook.md`)
- (Opcional) Setup VPC endpoints para tráfego privado

---

## Opção B: Setup Manual (Passo-a-Passo)

### Passo 1: Criar IAM Policy

```bash
cd /home/ubuntu

# Criar policy
POLICY_ARN=$(aws iam create-policy \
  --policy-name WordFluxBedrockMinimalPolicy \
  --policy-document file://docs/bedrock-iam-policy-minimal.json \
  --description "Minimal Bedrock invoke permissions for WordFlux" \
  --query 'Policy.Arn' \
  --output text)

echo "Policy criada: $POLICY_ARN"
```

### Passo 2A: Setup IAM Instance Profile (Produção)

```bash
# 1. Criar IAM role
TRUST_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ec2.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
EOF
)

aws iam create-role \
  --role-name WordFluxBedrockRole \
  --assume-role-policy-document "$TRUST_POLICY" \
  --description "WordFlux Bedrock access role"

# 2. Anexar policy ao role
aws iam attach-role-policy \
  --role-name WordFluxBedrockRole \
  --policy-arn $POLICY_ARN

# 3. Criar instance profile
aws iam create-instance-profile \
  --instance-profile-name WordFluxBedrockInstanceProfile

# 4. Adicionar role ao instance profile
aws iam add-role-to-instance-profile \
  --instance-profile-name WordFluxBedrockInstanceProfile \
  --role-name WordFluxBedrockRole

# 5. Anexar instance profile à EC2
INSTANCE_ID="i-0956d01bff6dbf1e5"
aws ec2 associate-iam-instance-profile \
  --instance-id $INSTANCE_ID \
  --iam-instance-profile Name=WordFluxBedrockInstanceProfile

# 6. Aguardar propagação
echo "Aguardando 60s para propagação de permissões..."
sleep 60

# 7. Verificar
curl -s http://169.254.169.254/latest/meta-data/iam/security-credentials/
# Esperado: WordFluxBedrockRole
```

### Passo 2B: Setup IAM User Policy (Dev/Test)

```bash
# Anexar policy ao user
aws iam attach-user-policy \
  --user-name renan \
  --policy-arn $POLICY_ARN

# Aguardar propagação
echo "Aguardando 30s para propagação de permissões..."
sleep 30

# Verificar
aws iam list-attached-user-policies --user-name renan
```

### Passo 3: Testar Permissões Bedrock

```bash
# Criar payload de teste
cat > /tmp/bedrock-test.json <<EOF
{
  "anthropic_version": "bedrock-2023-05-31",
  "max_tokens": 10,
  "messages": [{
    "role": "user",
    "content": [{"type": "text", "text": "ping"}]
  }]
}
EOF

# Test invoke-model
aws bedrock-runtime invoke-model \
  --region us-east-1 \
  --model-id us.anthropic.claude-sonnet-4-5-20250929-v1:0 \
  --body file:///tmp/bedrock-test.json \
  /tmp/bedrock-response.json

# Verificar resposta
cat /tmp/bedrock-response.json | jq
```

**Resposta esperada:**

```json
{
  "content": [
    {
      "text": "Pong!",
      "type": "text"
    }
  ],
  "id": "msg_xxxxx",
  "model": "claude-sonnet-4-5-20250929",
  "role": "assistant",
  ...
}
```

Se receber **AccessDeniedException**, aguarde mais 30s e tente novamente.

### Passo 4-6: Mesmo que Opção A

Continue com os passos 2, 3 e 4 da Opção A (Habilitar Bedrock, Reiniciar, Validar).

---

## Troubleshooting

### Erro 1: AccessDeniedException

**Sintoma:**
```
botocore.exceptions.ClientError: An error occurred (AccessDeniedException)
```

**Soluções:**

1. **Aguardar propagação de permissões**
   ```bash
   echo "Aguardando 60s..."
   sleep 60
   # Tentar novamente
   ```

2. **Verificar policy anexada**
   ```bash
   # Para instance profile
   aws iam list-attached-role-policies --role-name WordFluxBedrockRole

   # Para user
   aws iam list-attached-user-policies --user-name renan
   ```

3. **Verificar conteúdo da policy**
   ```bash
   aws iam get-policy-version \
     --policy-arn arn:aws:iam::330140023537:policy/WordFluxBedrockMinimalPolicy \
     --version-id v1 \
     --query 'PolicyVersion.Document' | jq
   ```

4. **Testar com modelo diferente**
   ```bash
   # Tentar com Sonnet 3.5 (não requer inference profile)
   aws bedrock-runtime invoke-model \
     --model-id anthropic.claude-3-5-sonnet-20240620-v1:0 \
     --body file:///tmp/bedrock-test.json \
     /tmp/bedrock-response2.json
   ```

### Erro 2: ValidationException (Inference Profile Required)

**Sintoma:**
```
ValidationException: Invocation of model ID ... with on-demand throughput isn't supported. Retry with inference profile.
```

**Solução:**

```bash
# Atualizar wordflux.env com inference profile
sed -i 's/ANTHROPIC_BEDROCK_MODEL=anthropic.claude-sonnet-4-5-20250929-v1:0/ANTHROPIC_BEDROCK_MODEL=us.anthropic.claude-sonnet-4-5-20250929-v1:0/' /home/ubuntu/wordflux.env

# Reiniciar
sudo systemctl restart wordflux-api
```

### Erro 3: Chat Endpoint Retorna Timeout

**Sintoma:**
```
curl: (28) Operation timed out after 30000 milliseconds
```

**Soluções:**

1. **Verificar logs da API**
   ```bash
   sudo journalctl -u wordflux-api -n 50 | grep -E "(ERROR|WARN)"
   ```

2. **Verificar se API está rodando**
   ```bash
   curl http://localhost:8080/health
   ```

3. **Verificar Redis**
   ```bash
   redis-cli ping
   redis-cli info clients
   ```

4. **Aumentar timeout (temporariamente)**
   ```bash
   curl --max-time 120 -X POST http://localhost:8080/chat/ \
     -d '{"message":"ping","session_id":"timeout-test"}'
   ```

### Erro 4: SSE Stream Não Recebe Eventos

**Sintoma:** `curl -N /events/stream` conecta mas não mostra eventos

**Soluções:**

1. **Verificar Redis pub/sub**
   ```bash
   # Terminal 1
   redis-cli SUBSCRIBE wf:events

   # Terminal 2 (enviar mensagem de teste)
   redis-cli PUBLISH wf:events '{"kind":"test","ts":"2025-09-30T00:00:00Z"}'

   # Terminal 1 deve mostrar a mensagem
   ```

2. **Verificar nginx buffering**
   ```bash
   sudo nginx -t
   # Deve mostrar: X-Accel-Buffering: no para /events/stream
   ```

3. **Restart nginx**
   ```bash
   sudo systemctl restart nginx
   ```

### Erro 5: "Provider bedrock não reconhecido"

**Sintoma:**
```
ValueError: Provider LLM inválido: 'bedrock'
```

**Solução:**

1. **Verificar código llm_client.py**
   ```bash
   grep "def get_bedrock_client" /home/ubuntu/src/core/llm_client.py
   # Deve existir a função
   ```

2. **Verificar imports**
   ```bash
   python3 -c "from src.core.llm_client import get_bedrock_client; print('OK')"
   ```

3. **Reinstalar boto3**
   ```bash
   source /home/ubuntu/.venv/bin/activate
   pip install --upgrade boto3
   ```

---

## Rollback (Voltar para Anthropic Direct)

Se algo der errado com Bedrock:

```bash
# 1. Editar wordflux.env
sed -i 's/WF_LLM_PROVIDER=bedrock/WF_LLM_PROVIDER=anthropic/' /home/ubuntu/wordflux.env

# 2. Verificar API key presente
grep ANTHROPIC_API_KEY /home/ubuntu/wordflux.env

# 3. Reiniciar
sudo systemctl restart wordflux-api

# 4. Verificar logs
sudo journalctl -u wordflux-api -n 20 | grep "LLM provider"
# Esperado: "🤖 Usando LLM provider: anthropic"

# 5. Testar
curl -s -X POST http://localhost:8080/chat/ \
  -d '{"message":"ping","session_id":"rollback-test"}' | jq '.message'
```

---

## VPC Interface Endpoints (Opcional - Produção)

Para tráfego privado (sem sair da AWS via internet):

```bash
# 1. Obter VPC ID
VPC_ID=$(aws ec2 describe-instances \
  --instance-ids i-0956d01bff6dbf1e5 \
  --query 'Reservations[0].Instances[0].VpcId' \
  --output text)

# 2. Obter Subnet ID
SUBNET_ID=$(aws ec2 describe-instances \
  --instance-ids i-0956d01bff6dbf1e5 \
  --query 'Reservations[0].Instances[0].SubnetId' \
  --output text)

# 3. Criar Security Group
SG_ID=$(aws ec2 create-security-group \
  --group-name wordflux-bedrock-endpoint-sg \
  --description "Security group for Bedrock VPC endpoint" \
  --vpc-id $VPC_ID \
  --query 'GroupId' \
  --output text)

# 4. Adicionar regra ingress HTTPS (443)
INSTANCE_SG=$(aws ec2 describe-instances \
  --instance-ids i-0956d01bff6dbf1e5 \
  --query 'Reservations[0].Instances[0].SecurityGroups[0].GroupId' \
  --output text)

aws ec2 authorize-security-group-ingress \
  --group-id $SG_ID \
  --protocol tcp \
  --port 443 \
  --source-group $INSTANCE_SG

# 5. Criar VPC Endpoint
ENDPOINT_ID=$(aws ec2 create-vpc-endpoint \
  --vpc-id $VPC_ID \
  --service-name com.amazonaws.us-east-1.bedrock-runtime \
  --vpc-endpoint-type Interface \
  --subnet-ids $SUBNET_ID \
  --security-group-ids $SG_ID \
  --private-dns-enabled \
  --query 'VpcEndpoint.VpcEndpointId' \
  --output text)

echo "VPC Endpoint criado: $ENDPOINT_ID"

# 6. Aguardar disponibilidade
aws ec2 wait vpc-endpoint-available --vpc-endpoint-ids $ENDPOINT_ID

# 7. Verificar DNS privado
nslookup bedrock-runtime.us-east-1.amazonaws.com
# Deve resolver para IP privado (172.x.x.x)
```

**Custo:** ~$7.30/mês + $0.01/GB transferência

**Benefícios:**
- ✅ Tráfego privado (não sai da AWS)
- ✅ Melhor latência
- ✅ Compliance (dados não trafegam internet pública)

---

## Validação de Sucesso

Checklist final:

- [ ] `aws bedrock-runtime invoke-model` retorna 200 OK
- [ ] `WF_LLM_PROVIDER=bedrock` em wordflux.env
- [ ] Logs mostram "BedrockClient inicializado"
- [ ] Chat endpoint retorna resposta em PT-BR
- [ ] Tool calls são executados (job enfileirado)
- [ ] SSE stream mostra eventos `job_queued`
- [ ] Health endpoint mostra `"provider": "bedrock"`
- [ ] (Opcional) VPC endpoint funcionando

---

## Próximos Passos

1. **Monitoramento:**
   - Configurar alertas CloudWatch para errors, latency
   - Dashboard Grafana com métricas Bedrock

2. **Auditoria:**
   - Habilitar CloudTrail para Bedrock API calls
   - Revisar audit log semanal

3. **Otimização:**
   - Testar modelos alternativos (Sonnet 4, Opus 4.1)
   - Ajustar timeout se necessário

4. **Documentação:**
   - Atualizar runbook com learnings específicos
   - Documentar casos de uso específicos

---

## Documentação Relacionada

- **Security Runbook:** `docs/bedrock-runbook.md`
- **IAM Policy:** `docs/bedrock-iam-policy-minimal.json`
- **Deployment Summary:** `DEPLOYMENT_SUMMARY.md`
- **Bedrock Status:** `BEDROCK_STATUS.md`
- **Setup Script:** `scripts/setup_bedrock_iam.sh`

---

## Suporte

**Problemas de Permissões IAM:**
- Entrar em contato com AWS account admin

**Problemas de Aplicação:**
- Verificar logs: `sudo journalctl -u wordflux-api -f`
- Verificar health: `curl http://localhost:8080/health`

**Questões de Segurança:**
- Consultar `docs/bedrock-runbook.md`
- Consultar `docs/security.md`

---

**Última Revisão:** 2025-09-30
**Versão:** 1.0.0
