#!/bin/bash
set -euo pipefail

# setup_bedrock_iam.sh - Automated Bedrock IAM Setup Script
# Usage: sudo ./scripts/setup_bedrock_iam.sh [--instance-profile | --user-policy]
#
# Descrição:
#   Configura permissões mínimas de IAM para AWS Bedrock seguindo o princípio
#   de least privilege. Suporta tanto IAM Instance Profile (recomendado para
#   produção) quanto IAM User Policy (dev/test).
#
# Requisitos:
#   - AWS CLI v2
#   - jq (JSON processor)
#   - Credenciais AWS com permissões IAM
#
# Autor: WordFlux DevSecOps
# Versão: 1.0.0

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
POLICY_FILE="/home/ubuntu/docs/bedrock-iam-policy-minimal.json"
POLICY_NAME="WordFluxBedrockMinimalPolicy"
ROLE_NAME="WordFluxBedrockRole"
INSTANCE_PROFILE_NAME="WordFluxBedrockInstanceProfile"
INSTANCE_ID="i-0956d01bff6dbf1e5"
IAM_USER="renan"
AWS_ACCOUNT_ID="330140023537"
AWS_REGION="us-east-1"

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check prerequisites
check_prerequisites() {
    log_info "Verificando pré-requisitos..."

    # Check AWS CLI
    if ! command -v aws &> /dev/null; then
        log_error "AWS CLI não encontrado. Instale: https://aws.amazon.com/cli/"
        exit 1
    fi

    # Check jq
    if ! command -v jq &> /dev/null; then
        log_error "jq não encontrado. Instale: sudo apt install jq"
        exit 1
    fi

    # Check AWS credentials
    if ! aws sts get-caller-identity &> /dev/null; then
        log_error "Credenciais AWS inválidas ou expiradas"
        exit 1
    fi

    # Check policy file exists
    if [ ! -f "$POLICY_FILE" ]; then
        log_error "Policy file não encontrado: $POLICY_FILE"
        exit 1
    fi

    log_success "Pré-requisitos OK"
}

# Create IAM policy
create_iam_policy() {
    log_info "Criando IAM policy: $POLICY_NAME..."

    # Check if policy already exists
    POLICY_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:policy/${POLICY_NAME}"
    if aws iam get-policy --policy-arn "$POLICY_ARN" &> /dev/null; then
        log_warning "Policy $POLICY_NAME já existe. Pulando criação."
        echo "$POLICY_ARN"
        return 0
    fi

    # Create policy
    POLICY_ARN=$(aws iam create-policy \
        --policy-name "$POLICY_NAME" \
        --policy-document file://"$POLICY_FILE" \
        --description "Minimal Bedrock invoke permissions for WordFlux (auto-created)" \
        --query 'Policy.Arn' \
        --output text)

    if [ -z "$POLICY_ARN" ]; then
        log_error "Falha ao criar policy"
        exit 1
    fi

    log_success "Policy criada: $POLICY_ARN"
    echo "$POLICY_ARN"
}

# Setup IAM Instance Profile (recommended)
setup_instance_profile() {
    log_info "========================================="
    log_info "Configurando IAM Instance Profile (Produção)"
    log_info "========================================="

    # Step 1: Create IAM policy
    POLICY_ARN=$(create_iam_policy)

    # Step 2: Create IAM role
    log_info "Criando IAM role: $ROLE_NAME..."

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

    if aws iam get-role --role-name "$ROLE_NAME" &> /dev/null; then
        log_warning "Role $ROLE_NAME já existe. Pulando criação."
    else
        aws iam create-role \
            --role-name "$ROLE_NAME" \
            --assume-role-policy-document "$TRUST_POLICY" \
            --description "WordFlux Bedrock access role (auto-created)" \
            > /dev/null
        log_success "Role criada: $ROLE_NAME"
    fi

    # Step 3: Attach policy to role
    log_info "Anexando policy ao role..."
    aws iam attach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn "$POLICY_ARN"
    log_success "Policy anexada ao role"

    # Step 4: Create instance profile
    log_info "Criando instance profile: $INSTANCE_PROFILE_NAME..."

    if aws iam get-instance-profile --instance-profile-name "$INSTANCE_PROFILE_NAME" &> /dev/null; then
        log_warning "Instance profile $INSTANCE_PROFILE_NAME já existe. Pulando criação."
    else
        aws iam create-instance-profile \
            --instance-profile-name "$INSTANCE_PROFILE_NAME" \
            > /dev/null
        log_success "Instance profile criado"

        # Add role to instance profile
        log_info "Adicionando role ao instance profile..."
        aws iam add-role-to-instance-profile \
            --instance-profile-name "$INSTANCE_PROFILE_NAME" \
            --role-name "$ROLE_NAME"
        log_success "Role adicionado ao instance profile"
    fi

    # Step 5: Attach instance profile to EC2
    log_info "Anexando instance profile à EC2: $INSTANCE_ID..."

    # Check if already attached
    CURRENT_PROFILE=$(aws ec2 describe-iam-instance-profile-associations \
        --filters "Name=instance-id,Values=$INSTANCE_ID" \
        --query 'IamInstanceProfileAssociations[0].IamInstanceProfile.Arn' \
        --output text 2>/dev/null || echo "None")

    if [ "$CURRENT_PROFILE" != "None" ]; then
        log_warning "EC2 já tem instance profile: $CURRENT_PROFILE"
        log_info "Substituindo pelo novo instance profile..."

        ASSOCIATION_ID=$(aws ec2 describe-iam-instance-profile-associations \
            --filters "Name=instance-id,Values=$INSTANCE_ID" \
            --query 'IamInstanceProfileAssociations[0].AssociationId' \
            --output text)

        aws ec2 replace-iam-instance-profile-association \
            --association-id "$ASSOCIATION_ID" \
            --iam-instance-profile "Name=$INSTANCE_PROFILE_NAME" \
            > /dev/null
        log_success "Instance profile substituído"
    else
        aws ec2 associate-iam-instance-profile \
            --instance-id "$INSTANCE_ID" \
            --iam-instance-profile "Name=$INSTANCE_PROFILE_NAME" \
            > /dev/null
        log_success "Instance profile anexado à EC2"
    fi

    # Step 6: Wait for propagation
    log_info "Aguardando propagação de permissões (60s)..."
    sleep 60

    # Step 7: Verify
    log_info "Verificando instance profile na EC2..."
    METADATA_ROLE=$(curl -s http://169.254.169.254/latest/meta-data/iam/security-credentials/ 2>/dev/null || echo "")

    if [ "$METADATA_ROLE" == "$ROLE_NAME" ]; then
        log_success "✅ Instance profile verificado: $METADATA_ROLE"
    else
        log_warning "⚠️ Metadata mostra: '$METADATA_ROLE' (esperado: $ROLE_NAME)"
        log_warning "Aguarde mais 30s e tente novamente"
    fi

    log_success "========================================="
    log_success "Instance Profile Setup Completo!"
    log_success "========================================="
}

# Setup IAM User Policy (dev/test only)
setup_user_policy() {
    log_info "========================================="
    log_info "Configurando IAM User Policy (Dev/Test)"
    log_info "========================================="

    # Step 1: Create IAM policy
    POLICY_ARN=$(create_iam_policy)

    # Step 2: Attach policy to user
    log_info "Anexando policy ao user: $IAM_USER..."
    aws iam attach-user-policy \
        --user-name "$IAM_USER" \
        --policy-arn "$POLICY_ARN"
    log_success "Policy anexada ao user"

    # Step 3: Wait for propagation
    log_info "Aguardando propagação de permissões (30s)..."
    sleep 30

    # Step 4: Verify
    log_info "Verificando policies do user..."
    ATTACHED_POLICIES=$(aws iam list-attached-user-policies \
        --user-name "$IAM_USER" \
        --query 'AttachedPolicies[?PolicyName==`'"$POLICY_NAME"'`].PolicyName' \
        --output text)

    if [ "$ATTACHED_POLICIES" == "$POLICY_NAME" ]; then
        log_success "✅ Policy verificada: $POLICY_NAME"
    else
        log_error "❌ Policy não encontrada nas políticas do user"
        exit 1
    fi

    log_success "========================================="
    log_success "User Policy Setup Completo!"
    log_success "========================================="
}

# Test Bedrock access
test_bedrock_access() {
    log_info "========================================="
    log_info "Testando Acesso ao Bedrock"
    log_info "========================================="

    # Create minimal test payload
    TEST_PAYLOAD=$(cat <<EOF
{
  "anthropic_version": "bedrock-2023-05-31",
  "max_tokens": 10,
  "messages": [{
    "role": "user",
    "content": [{"type": "text", "text": "ping"}]
  }]
}
EOF
)

    echo "$TEST_PAYLOAD" > /tmp/bedrock-test-payload.json

    # Test invoke-model
    log_info "Testando aws bedrock-runtime invoke-model..."

    if aws bedrock-runtime invoke-model \
        --region "$AWS_REGION" \
        --model-id "us.anthropic.claude-sonnet-4-5-20250929-v1:0" \
        --body file:///tmp/bedrock-test-payload.json \
        /tmp/bedrock-test-response.json 2>&1; then

        log_success "✅ Invoke model OK"

        # Parse response
        RESPONSE_TEXT=$(jq -r '.content[0].text' /tmp/bedrock-test-response.json 2>/dev/null || echo "N/A")
        log_info "Resposta do modelo: $RESPONSE_TEXT"
    else
        log_error "❌ Invoke model FAILED"
        log_error "Verifique:"
        log_error "  1. Permissões IAM (bedrock:InvokeModel)"
        log_error "  2. Região correta (us-east-1)"
        log_error "  3. Model ID disponível"
        exit 1
    fi

    # Cleanup
    rm -f /tmp/bedrock-test-payload.json /tmp/bedrock-test-response.json

    log_success "========================================="
    log_success "Teste de Bedrock Completo!"
    log_success "========================================="
}

# Main function
main() {
    echo ""
    log_info "╔══════════════════════════════════════════════════╗"
    log_info "║   WordFlux - Bedrock IAM Setup Automation       ║"
    log_info "╚══════════════════════════════════════════════════╝"
    echo ""

    # Parse arguments
    SETUP_MODE="instance-profile"  # Default
    if [ $# -gt 0 ]; then
        case "$1" in
            --instance-profile)
                SETUP_MODE="instance-profile"
                ;;
            --user-policy)
                SETUP_MODE="user-policy"
                ;;
            --help|-h)
                echo "Usage: $0 [--instance-profile | --user-policy]"
                echo ""
                echo "Options:"
                echo "  --instance-profile   Setup IAM Instance Profile (recommended for prod)"
                echo "  --user-policy        Setup IAM User Policy (dev/test only)"
                echo "  --help, -h           Show this help message"
                echo ""
                exit 0
                ;;
            *)
                log_error "Opção inválida: $1"
                echo "Use --help para ver opções disponíveis"
                exit 1
                ;;
        esac
    fi

    # Check prerequisites
    check_prerequisites

    # Run setup based on mode
    if [ "$SETUP_MODE" == "instance-profile" ]; then
        setup_instance_profile
    else
        setup_user_policy
    fi

    # Test Bedrock access
    test_bedrock_access

    # Print next steps
    echo ""
    log_info "╔══════════════════════════════════════════════════╗"
    log_info "║   Next Steps                                     ║"
    log_info "╚══════════════════════════════════════════════════╝"
    echo ""
    log_info "1. Habilitar Bedrock no WordFlux:"
    echo "   sed -i 's/WF_LLM_PROVIDER=anthropic/WF_LLM_PROVIDER=bedrock/' /home/ubuntu/wordflux.env"
    echo ""
    log_info "2. Reiniciar serviço:"
    echo "   sudo systemctl restart wordflux-api"
    echo ""
    log_info "3. Verificar logs:"
    echo "   sudo journalctl -u wordflux-api -n 20 | grep Bedrock"
    echo ""
    log_info "4. Testar chat endpoint:"
    echo "   curl -X POST http://localhost:8080/chat/ \\"
    echo "     -d '{\"message\":\"ping\",\"session_id\":\"test\"}' | jq"
    echo ""
    log_success "Setup concluído com sucesso! 🎉"
    echo ""
}

# Run main
main "$@"
