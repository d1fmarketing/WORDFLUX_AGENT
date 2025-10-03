# AWS Bedrock Security Runbook - WordFlux

**Objetivo:** Configurar acesso seguro ao AWS Bedrock seguindo o princípio do menor privilégio (least privilege)

**Público:** DevSecOps, Platform Engineers

**Última Atualização:** 2025-09-30

---

## 1. Visão Geral de Segurança

### Arquitetura Atual

```
┌─────────────────┐
│  EC2 Instance   │
│  (WordFlux API) │
│                 │
│  - No IAM Role  │  ⚠️ VULNERÁVEL
│  - User creds   │
│    in ~/.aws    │
└────────┬────────┘
         │
         │ HTTPS (public internet)
         ↓
┌─────────────────┐
│  AWS Bedrock    │
│  Runtime API    │
│  (us-east-1)    │
└─────────────────┘
```

### Riscos Identificados

| Risco | Severidade | Mitigação |
|-------|------------|-----------|
| **Credenciais em ~/.aws/credentials** | 🔴 Alta | Migrar para IAM Instance Profile |
| **Tráfego pela internet pública** | 🟡 Média | VPC Interface Endpoints (opcional) |
| **Permissões excessivas** | 🟡 Média | IAM Policy minimal (implementada) |
| **Sem auditoria de chamadas Bedrock** | 🟢 Baixa | CloudWatch Logs (opcional) |

---

## 2. IAM Configuration (Least Privilege)

### 2.1 Opção Recomendada: IAM Instance Profile

**Por quê?**
- ✅ Credenciais temporárias rotacionadas automaticamente
- ✅ Sem armazenamento de chaves em disco
- ✅ Melhor auditoria (CloudTrail registra role assumido)
- ✅ Princípio de menor privilégio por recurso (EC2)

**Steps:**

```bash
# 1. Criar IAM Role para EC2
aws iam create-role \
  --role-name WordFluxBedrockRole \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {"Service": "ec2.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }]
  }'

# 2. Criar policy minimal
aws iam create-policy \
  --policy-name WordFluxBedrockMinimalPolicy \
  --policy-document file:///home/ubuntu/docs/bedrock-iam-policy-minimal.json \
  --description "Minimal Bedrock invoke permissions for WordFlux"

# 3. Anexar policy ao role
aws iam attach-role-policy \
  --role-name WordFluxBedrockRole \
  --policy-arn arn:aws:iam::330140023537:policy/WordFluxBedrockMinimalPolicy

# 4. Criar instance profile
aws iam create-instance-profile \
  --instance-profile-name WordFluxBedrockInstanceProfile

# 5. Adicionar role ao instance profile
aws iam add-role-to-instance-profile \
  --instance-profile-name WordFluxBedrockInstanceProfile \
  --role-name WordFluxBedrockRole

# 6. Anexar instance profile à EC2
aws ec2 associate-iam-instance-profile \
  --instance-id i-0956d01bff6dbf1e5 \
  --iam-instance-profile Name=WordFluxBedrockInstanceProfile

# 7. Aguardar propagação
echo "Aguardando 30s para propagação..."
sleep 30

# 8. Verificar no metadata
curl -s http://169.254.169.254/latest/meta-data/iam/security-credentials/

# Deve mostrar: WordFluxBedrockRole
```

**Cleanup de credenciais antigas:**

```bash
# IMPORTANTE: Após migração para instance profile
# Remover ~/.aws/credentials para evitar confusão
mv ~/.aws/credentials ~/.aws/credentials.backup
# boto3 vai usar instance profile automaticamente
```

### 2.2 Opção Alternativa: IAM User (Menos Seguro)

**Quando usar:** Ambiente de desenvolvimento/teste apenas

```bash
# 1. Criar policy minimal
aws iam create-policy \
  --policy-name WordFluxBedrockMinimalPolicy \
  --policy-document file:///home/ubuntu/docs/bedrock-iam-policy-minimal.json

# 2. Anexar ao user existente
aws iam attach-user-policy \
  --user-name renan \
  --policy-arn arn:aws:iam::330140023537:policy/WordFluxBedrockMinimalPolicy

# 3. Aguardar propagação
sleep 30

# 4. Verificar
aws iam list-attached-user-policies --user-name renan
```

### 2.3 Conteúdo da Policy Minimal

**Arquivo:** `docs/bedrock-iam-policy-minimal.json`

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BedrockInvokeModelMinimal",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:us-east-1::foundation-model/us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0"
      ],
      "Condition": {
        "StringEquals": {
          "aws:RequestedRegion": "us-east-1"
        }
      }
    }
  ]
}
```

**Diferenças da policy anterior:**

| Aspecto | Policy Antiga | Policy Minimal | Benefício |
|---------|---------------|----------------|-----------|
| **Resource** | `bedrock:*::foundation-model/anthropic.claude-*` | ARNs específicos do modelo configurado | Previne uso de modelos não autorizados |
| **Region** | Wildcard (`*`) | `us-east-1` via Condition | Previne chamadas de outras regiões |
| **Actions** | Mesmas | Mesmas | Apenas invoke, sem admin/config |

---

## 3. VPC Interface Endpoints (Opcional - Produção)

### 3.1 Benefícios

- ✅ Tráfego privado (não sai da AWS)
- ✅ Melhor latência (roteamento interno)
- ✅ Compliance (dados não trafegam internet pública)
- ✅ Sem NAT Gateway cost (se EC2 em subnet privada)

### 3.2 Custos

**VPC Endpoint:**
- $0.01 USD/hora (~$7.30/mês por endpoint)
- $0.01 USD/GB transferência

**Para WordFlux:**
- 1 endpoint: `com.amazonaws.us-east-1.bedrock-runtime`
- Estimativa: ~$10/mês (incluindo transferência)

### 3.3 Setup de VPC Endpoint

```bash
# 1. Obter VPC e subnet da instância
VPC_ID=$(aws ec2 describe-instances \
  --instance-ids i-0956d01bff6dbf1e5 \
  --query 'Reservations[0].Instances[0].VpcId' \
  --output text)

SUBNET_ID=$(aws ec2 describe-instances \
  --instance-ids i-0956d01bff6dbf1e5 \
  --query 'Reservations[0].Instances[0].SubnetId' \
  --output text)

echo "VPC: $VPC_ID, Subnet: $SUBNET_ID"

# 2. Criar Security Group para endpoint
SG_ID=$(aws ec2 create-security-group \
  --group-name wordflux-bedrock-endpoint-sg \
  --description "Security group for Bedrock VPC endpoint" \
  --vpc-id $VPC_ID \
  --query 'GroupId' \
  --output text)

# 3. Adicionar regra ingress HTTPS (443)
aws ec2 authorize-security-group-ingress \
  --group-id $SG_ID \
  --protocol tcp \
  --port 443 \
  --source-group $(aws ec2 describe-instances \
      --instance-ids i-0956d01bff6dbf1e5 \
      --query 'Reservations[0].Instances[0].SecurityGroups[0].GroupId' \
      --output text)

# 4. Criar VPC Endpoint
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

# 5. Aguardar disponibilidade
aws ec2 wait vpc-endpoint-available --vpc-endpoint-ids $ENDPOINT_ID

# 6. Verificar DNS privado
aws ec2 describe-vpc-endpoints \
  --vpc-endpoint-ids $ENDPOINT_ID \
  --query 'VpcEndpoints[0].DnsEntries'
```

### 3.4 Validação de VPC Endpoint

```bash
# 1. Dentro da EC2, testar resolução DNS
nslookup bedrock-runtime.us-east-1.amazonaws.com

# Deve resolver para IP privado (172.x.x.x) se endpoint ativo
# Caso contrário, resolve para IP público

# 2. Testar conectividade
curl -v https://bedrock-runtime.us-east-1.amazonaws.com 2>&1 | grep "Connected to"

# 3. Verificar rota (deve usar endpoint, não Internet Gateway)
aws ec2 describe-route-tables \
  --filters "Name=vpc-id,Values=$VPC_ID" \
  --query 'RouteTables[*].Routes[?DestinationPrefixListId]'
```

---

## 4. Validação de Segurança

### 4.1 Test IAM Permissions

```bash
# Test 1: Invoke model (deve funcionar)
aws bedrock-runtime invoke-model \
  --region us-east-1 \
  --model-id us.anthropic.claude-sonnet-4-5-20250929-v1:0 \
  --body '{
    "anthropic_version": "bedrock-2023-05-31",
    "max_tokens": 10,
    "messages": [{"role":"user","content":[{"type":"text","text":"ping"}]}]
  }' \
  /tmp/bedrock-test.json

# Esperado: HTTP 200, arquivo /tmp/bedrock-test.json criado
cat /tmp/bedrock-test.json

# Test 2: List models (NÃO deve funcionar - sem permissão)
aws bedrock list-foundation-models --region us-east-1

# Esperado: AccessDeniedException (proposital, least privilege)
```

### 4.2 Test Application Integration

```bash
# Test 1: SSE stream health
curl -N http://localhost:8081/events/stream &
CURL_PID=$!
sleep 5  # Aguardar heartbeat events
kill $CURL_PID

# Esperado: eventos {"kind":"heartbeat"} a cada 30s

# Test 2: Chat endpoint com Bedrock
# (Requer WF_LLM_PROVIDER=bedrock no wordflux.env)
curl -s -X POST http://localhost:8080/chat/ \
  -H 'Content-Type: application/json' \
  -d '{
    "message": "Crie card \"Security Test - Bedrock\"",
    "session_id": "security-validation"
  }' | python3 -m json.tool

# Esperado:
# {
#   "message": "Vou criar o card \"Security Test - Bedrock\".\n\n✅ Job(s) enfileirado(s): `chat-xxxxx`",
#   "tool_calls": [...],
#   "requires_approval": false
# }

# Test 3: Verificar logs do Bedrock
sudo journalctl -u wordflux-api -n 20 | grep -i bedrock

# Esperado: Logs mostrando BedrockClient inicializado
```

### 4.3 Security Audit Checklist

- [ ] **IAM Policy**: Apenas permissões mínimas (InvokeModel, InvokeModelWithResponseStream)
- [ ] **Resource Scope**: ARNs específicos do modelo, não wildcard
- [ ] **Region Scope**: Condition restrita a us-east-1
- [ ] **Credentials**: Usando IAM Instance Profile (não user credentials em disco)
- [ ] **VPC Endpoint** (opcional): Tráfego privado, Private DNS habilitado
- [ ] **Security Groups**: Apenas porta 443 ingress necessária
- [ ] **Logs**: CloudTrail habilitado para auditoria de chamadas Bedrock
- [ ] **Rotation**: Não aplicável (instance profile usa credenciais temporárias)

---

## 5. Troubleshooting

### 5.1 AccessDeniedException

**Sintoma:**
```
botocore.exceptions.ClientError: An error occurred (AccessDeniedException) when calling the InvokeModel operation: You don't have access to the model with the specified model ID.
```

**Causas Possíveis:**

1. **Policy não anexada ao user/role**
   ```bash
   # Verificar policies do user
   aws iam list-attached-user-policies --user-name renan

   # Verificar policies do role (se usando instance profile)
   aws iam list-attached-role-policies --role-name WordFluxBedrockRole
   ```

2. **Propagação de permissões pendente**
   ```bash
   # Aguardar 30-60 segundos após attach
   sleep 60
   ```

3. **Resource ARN incorreto na policy**
   ```bash
   # Verificar policy
   aws iam get-policy-version \
     --policy-arn arn:aws:iam::330140023537:policy/WordFluxBedrockMinimalPolicy \
     --version-id v1 \
     --query 'PolicyVersion.Document' | jq
   ```

4. **Modelo não disponível na região**
   ```bash
   # Listar modelos disponíveis
   aws bedrock list-foundation-models --region us-east-1 --by-provider anthropic
   ```

### 5.2 VPC Endpoint DNS Issues

**Sintoma:**
```
ConnectionError: Could not resolve bedrock-runtime.us-east-1.amazonaws.com
```

**Causas:**

1. **Private DNS não habilitado**
   ```bash
   aws ec2 describe-vpc-endpoints \
     --vpc-endpoint-ids $ENDPOINT_ID \
     --query 'VpcEndpoints[0].PrivateDnsEnabled'
   # Deve retornar: true
   ```

2. **VPC DNS settings incorretos**
   ```bash
   aws ec2 describe-vpc-attribute \
     --vpc-id $VPC_ID \
     --attribute enableDnsHostnames
   aws ec2 describe-vpc-attribute \
     --vpc-id $VPC_ID \
     --attribute enableDnsSupport
   # Ambos devem ser: true
   ```

3. **Security Group bloqueando 443**
   ```bash
   aws ec2 describe-security-groups --group-ids $SG_ID
   # Verificar regra ingress TCP 443
   ```

### 5.3 Bedrock Throttling

**Sintoma:**
```
ThrottlingException: Rate exceeded for operation InvokeModel
```

**Mitigação:**

1. **Verificar quotas da conta**
   ```bash
   aws service-quotas list-service-quotas \
     --service-code bedrock \
     --region us-east-1 \
     --query 'Quotas[?QuotaName==`InvokeModel requests per minute`]'
   ```

2. **Implementar exponential backoff**
   - Já implementado em `src/core/llm_client.py` (boto3 retries)

3. **Solicitar aumento de quota**
   ```bash
   aws service-quotas request-service-quota-increase \
     --service-code bedrock \
     --quota-code L-12345678 \
     --desired-value 1000
   ```

### 5.4 Logs e Diagnóstico

**CloudTrail - Auditoria de chamadas Bedrock:**

```bash
# Verificar chamadas InvokeModel nas últimas 24h
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=InvokeModel \
  --region us-east-1 \
  --max-items 10 \
  --query 'Events[*].[EventTime,Username,CloudTrailEvent]' \
  --output text
```

**Application Logs:**

```bash
# Logs da API (systemd journal)
sudo journalctl -u wordflux-api -f --since "10 minutes ago" | grep -i bedrock

# Filtrar apenas erros
sudo journalctl -u wordflux-api --since "1 hour ago" | grep -E "(ERROR|WARN|Exception)"
```

---

## 6. Rollback Plan

### Se Bedrock Falhar, Voltar para Anthropic Direct

```bash
# 1. Editar wordflux.env
sed -i 's/WF_LLM_PROVIDER=bedrock/WF_LLM_PROVIDER=anthropic/' /home/ubuntu/wordflux.env

# 2. Verificar que ANTHROPIC_API_KEY está presente
grep ANTHROPIC_API_KEY /home/ubuntu/wordflux.env

# 3. Reiniciar serviço
sudo systemctl restart wordflux-api

# 4. Verificar logs
sudo journalctl -u wordflux-api -n 20 | grep "LLM provider"
# Deve mostrar: "🤖 Usando LLM provider: anthropic"

# 5. Testar chat
curl -s -X POST http://localhost:8080/chat/ \
  -d '{"message":"ping","session_id":"rollback-test"}' | jq '.message'
```

---

## 7. Compliance e Auditoria

### 7.1 CloudTrail Logging

**Habilitar CloudTrail para Bedrock API calls:**

```bash
# Criar S3 bucket para CloudTrail (se não existir)
aws s3 mb s3://wordflux-cloudtrail-logs --region us-east-1

# Criar trail
aws cloudtrail create-trail \
  --name wordflux-bedrock-audit \
  --s3-bucket-name wordflux-cloudtrail-logs \
  --include-global-service-events \
  --is-multi-region-trail

# Iniciar logging
aws cloudtrail start-logging --name wordflux-bedrock-audit

# Verificar status
aws cloudtrail get-trail-status --name wordflux-bedrock-audit
```

### 7.2 Queries de Auditoria

```bash
# Quem invocou Bedrock nas últimas 24h?
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=InvokeModel \
  --region us-east-1 \
  --query 'Events[*].[EventTime,Username,SourceIPAddress]' \
  --output table

# Quantas chamadas por hora?
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=InvokeModel \
  --region us-east-1 \
  --start-time $(date -u -d '1 day ago' +%Y-%m-%dT%H:%M:%S) \
  --query 'Events[*].EventTime' \
  --output text | cut -dT -f2 | cut -d: -f1 | sort | uniq -c
```

---

## 8. Monitoring e Alertas

### 8.1 Métricas Recomendadas

**CloudWatch Metrics:**
- `bedrock:InvokeModel` count (via CloudTrail)
- `bedrock:InvokeModel` latency (custom metric)
- `bedrock:InvokeModel` errors (via CloudTrail error events)

**Application Metrics (Prometheus):**
- `wordflux_chat_llm_calls_total{provider="bedrock"}` - Total de chamadas
- `wordflux_chat_llm_errors_total{provider="bedrock",error_type="AccessDenied"}` - Erros
- `wordflux_chat_llm_latency_seconds{provider="bedrock"}` - Latência

### 8.2 Alertas Recomendados

```yaml
# CloudWatch Alarms
- AlarmName: WordFlux-Bedrock-HighErrorRate
  Metric: Errors
  Threshold: 10 errors in 5 minutes
  Action: SNS notification

- AlarmName: WordFlux-Bedrock-HighLatency
  Metric: Duration
  Threshold: P95 > 5 seconds
  Action: SNS notification

- AlarmName: WordFlux-Bedrock-AccessDenied
  Metric: AccessDenied count
  Threshold: > 0
  Action: PagerDuty alert (critical)
```

---

## 9. Checklist de Deployment

### Pré-Produção

- [ ] IAM policy minimal criada (`bedrock-iam-policy-minimal.json`)
- [ ] Policy anexada a IAM role/user
- [ ] Permissões propagadas (aguardar 60s)
- [ ] Teste CLI: `aws bedrock-runtime invoke-model` OK
- [ ] VPC endpoint criado (opcional)
- [ ] Security groups configurados
- [ ] `WF_LLM_PROVIDER=bedrock` em `wordflux.env`
- [ ] Serviço reiniciado: `sudo systemctl restart wordflux-api`
- [ ] Logs confirmam Bedrock: `journalctl | grep BedrockClient`
- [ ] Teste e2e: Chat endpoint funcional

### Pós-Produção

- [ ] CloudTrail habilitado para auditoria
- [ ] Métricas expostas em `/metrics`
- [ ] Alertas configurados (errors, latency)
- [ ] Dashboard Grafana atualizado
- [ ] Runbook documentado (este arquivo)
- [ ] Rollback plan testado

---

## 10. Referências

- [AWS Bedrock Documentation](https://docs.aws.amazon.com/bedrock/)
- [IAM Best Practices - Least Privilege](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html#grant-least-privilege)
- [VPC Endpoints for AWS Services](https://docs.aws.amazon.com/vpc/latest/privatelink/vpc-endpoints.html)
- [CloudTrail Logging for Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/logging-using-cloudtrail.html)

---

**Contato:** Para questões de segurança ou IAM, entrar em contato com AWS account admin.

**Versão:** 1.0.0
**Última Revisão:** 2025-09-30
