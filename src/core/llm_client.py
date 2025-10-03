"""Cliente LLM multi-provider - Anthropic Direct API e AWS Bedrock.

Este módulo fornece integração com APIs LLM (Claude) para o sistema de chat
do WordFlux, incluindo suporte a tool-use para execução de funções.

Providers Suportados:
- Anthropic Direct API (via get_anthropic_client)
- AWS Bedrock Converse API (via get_bedrock_client)

Características:
- Suporte a tools[] (function calling) em ambos providers
- Streaming opcional (Anthropic) e reservado para futuro (Bedrock)
- Logging em Português sem vazar tokens/secrets
- Tratamento robusto de erros (timeout, rate limit, network)
- Interface unificada: método chat() retorna {"text", "tool_calls", "raw"}

Uso (Anthropic):
    from src.core.llm_client import get_anthropic_client, TOOL_SCHEMAS

    client = get_anthropic_client()
    response = client.chat(
        messages=[{"role": "user", "content": "Olá!"}],
        tools=TOOL_SCHEMAS
    )

Uso (Bedrock):
    from src.core.llm_client import get_bedrock_client, TOOL_SCHEMAS

    client = get_bedrock_client(aws_region="us-east-1")
    response = client.chat(
        messages=[{"role": "user", "content": "Olá!"}],
        tools=TOOL_SCHEMAS
    )
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Generator

try:
    import anthropic
except ImportError:
    raise SystemExit(
        "Anthropic SDK não instalado. Instale com: pip install anthropic>=0.39.0"
    )

try:
    import boto3
    from botocore.exceptions import ClientError, BotoCoreError
except ImportError:
    boto3 = None  # Optional: Bedrock support only if boto3 installed

logger = logging.getLogger(__name__)

# ============================================================================
# TOOL SCHEMAS - Formato Anthropic (input_schema)
# ============================================================================

TOOL_SCHEMAS = [
    {
        "name": "create_card",
        "description": "Cria um card no board a partir de linguagem natural.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Título do card"
                },
                "column": {
                    "type": "string",
                    "enum": ["Espera", "Produção", "Aprovação", "Agendado", "Finalizado"],
                    "description": "Coluna de destino"
                },
                "assignees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Responsáveis (opcional)"
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags de categorização (opcional)"
                },
                "due_date": {
                    "type": "string",
                    "description": "Data de entrega no formato YYYY-MM-DD (opcional)"
                }
            },
            "required": ["title", "column"]
        }
    },
    {
        "name": "move_card",
        "description": "Propõe mover um card para outra coluna.",
        "input_schema": {
            "type": "object",
            "properties": {
                "card_id": {
                    "type": "string",
                    "description": "ID do card"
                },
                "to_column": {
                    "type": "string",
                    "enum": ["Espera", "Produção", "Aprovação", "Agendado", "Finalizado"],
                    "description": "Coluna de destino"
                }
            },
            "required": ["card_id", "to_column"]
        }
    },
    {
        "name": "update_card",
        "description": "Propõe atualização de campos do card.",
        "input_schema": {
            "type": "object",
            "properties": {
                "card_id": {
                    "type": "string",
                    "description": "ID do card"
                },
                "fields": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": "Campos a atualizar (title, assignees, tags, due_date, etc)"
                }
            },
            "required": ["card_id", "fields"]
        }
    },
    {
        "name": "summarize_board",
        "description": "Resumo do board por coluna, prazos e bloqueios.",
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "description": "Escopo do resumo (opcional, ex: 'hoje', 'esta semana')"
                }
            }
        }
    },
    {
        "name": "ingest_email",
        "description": "Extrai intenção de um e-mail e devolve proposta de card.",
        "input_schema": {
            "type": "object",
            "properties": {
                "raw_text": {
                    "type": "string",
                    "description": "Texto completo do e-mail"
                }
            },
            "required": ["raw_text"]
        }
    }
]


# ============================================================================
# BEDROCK CONVERSION UTILITIES
# ============================================================================

def convert_anthropic_schema_to_bedrock(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Converter schema de ferramentas do formato Anthropic para Bedrock Converse.

    Anthropic format:
        {"name": "...", "description": "...", "input_schema": {...}}

    Bedrock Converse format:
        {"toolSpec": {"name": "...", "description": "...", "inputSchema": {"json": {...}}}}

    Args:
        tools: Lista de tools no formato Anthropic

    Returns:
        Lista de tools no formato Bedrock Converse
    """
    bedrock_tools = []
    for tool in tools:
        bedrock_tool = {
            "toolSpec": {
                "name": tool["name"],
                "description": tool["description"],
                "inputSchema": {
                    "json": tool["input_schema"]
                }
            }
        }
        bedrock_tools.append(bedrock_tool)

    return bedrock_tools


def convert_messages_to_converse(messages: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Converter mensagens do formato simples para Bedrock Converse format.

    Entrada:
        [{"role": "user", "content": "text"}, {"role": "assistant", "content": "text"}]

    Saída:
        [{"role": "user", "content": [{"text": "text"}]}, ...]

    Nota: Mensagens "system" são extraídas e retornadas separadamente.

    Args:
        messages: Lista de mensagens no formato simples

    Returns:
        Tupla (converse_messages, system_prompt)
    """
    converse_messages = []
    system_prompt = None

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")

        # Extrair mensagem de sistema separadamente
        if role == "system":
            system_prompt = content
            continue

        # Converter content para formato array
        if isinstance(content, str):
            content_blocks = [{"text": content}]
        else:
            # Já está no formato array (raro, mas possível)
            content_blocks = content

        converse_messages.append({
            "role": role,
            "content": content_blocks
        })

    return converse_messages, system_prompt


# ============================================================================
# ANTHROPIC CLIENT
# ============================================================================

class AnthropicClient:
    """
    Cliente simplificado para API Anthropic (Claude).

    Características:
    - Messages API com suporte a tools[]
    - Streaming opcional
    - Logging em PT-BR sem vazar tokens
    - Tratamento de erros robusto

    Exemplo:
        client = AnthropicClient(api_key="sk-ant-...")
        response = client.chat(
            messages=[{"role": "user", "content": "Olá!"}],
            tools=TOOL_SCHEMAS
        )
        # response: {"text": "...", "tool_calls": [...], "raw": {...}}
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        timeout: int = 30
    ):
        """
        Inicializar cliente Anthropic.

        Args:
            api_key: Chave API Anthropic (usa ANTHROPIC_API_KEY se None)
            model: Modelo a usar (usa ANTHROPIC_MODEL ou padrão se None)
            max_tokens: Máximo de tokens na resposta (padrão: 4096)
            temperature: Temperatura de geração 0.0-1.0 (padrão: 1.0)
            timeout: Timeout em segundos (padrão: 30)

        Raises:
            ValueError: Se api_key não fornecida e ANTHROPIC_API_KEY não definida
        """
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY é obrigatória. "
                "Defina via variável de ambiente ou parâmetro api_key."
            )

        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout

        self.client = anthropic.Anthropic(api_key=self.api_key, timeout=timeout)

        logger.info(
            f"🤖 AnthropicClient inicializado (modelo: {self.model}, "
            f"max_tokens: {max_tokens}, temperature: {temperature})"
        )

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False
    ) -> Dict[str, Any]:
        """
        Gerar resposta de chat com suporte a ferramentas.

        Args:
            messages: Lista de mensagens no formato:
                [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
            tools: Lista de ferramentas disponíveis (formato Anthropic)
            stream: Se True, retorna generator de chunks (opcional)

        Returns:
            Dict com:
            {
                "text": "resposta do assistente (string)",
                "tool_calls": [
                    {"id": "toolu_xxx", "name": "create_card", "input": {...}},
                    ...
                ],
                "raw": {...}  # resposta completa da API
            }

        Raises:
            RuntimeError: Erros da API Anthropic (rate limit, invalid key, etc.)
            TimeoutError: Timeout excedido
        """
        # Extrair mensagem de sistema (se houver)
        system_message = None
        filtered_messages: List[Dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")

            if not isinstance(content, str):
                content = str(content)

            if role == "system":
                system_message = content
                continue

            if role not in {"user", "assistant"}:
                logger.debug("Ignorando mensagem com role desconhecido: %s", role)
                continue

            filtered_messages.append({
                "role": role,
                "content": content,
            })

        # Log da requisição (sem vazar conteúdo completo)
        tool_count = len(tools) if tools else 0
        logger.info(
            f"🤖 Anthropic: enviando {len(filtered_messages)} mensagem(ns), "
            f"{tool_count} ferramenta(s) disponível(is)"
        )

        try:
            # Chamar API
            kwargs = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "messages": filtered_messages
            }

            if system_message:
                kwargs["system"] = system_message

            if tools:
                kwargs["tools"] = tools

            response = self.client.messages.create(**kwargs)

            # Parse resposta
            result = self._parse_response(response)

            # Log da resposta
            text_len = len(result["text"])
            tool_call_count = len(result["tool_calls"])
            logger.info(
                f"🤖 Anthropic: resposta recebida ({text_len} caracteres, "
                f"{tool_call_count} ferramenta(s) detectada(s))"
            )

            if tool_call_count > 0:
                tool_names = [tc["name"] for tc in result["tool_calls"]]
                logger.info(
                    f"🤖 Anthropic: ferramenta(s) detectada(s): {', '.join(tool_names)}"
                )

            return result

        except anthropic.APITimeoutError as e:
            logger.warning(f"⚠️ Anthropic: timeout após {self.timeout}s")
            raise TimeoutError(f"Timeout na API Anthropic após {self.timeout}s")

        except anthropic.RateLimitError as e:
            logger.error("❌ Anthropic: rate limit excedido")
            raise RuntimeError("Rate limit excedido na API Anthropic")

        except anthropic.APIError as e:
            status = getattr(e, 'status_code', 'unknown')
            message = getattr(e, 'message', str(e))
            logger.error(f"❌ Anthropic API erro: {status} - {message}")
            raise RuntimeError(f"Erro na API Anthropic: {message}")

        except Exception as e:
            logger.error(f"❌ Anthropic: erro inesperado - {type(e).__name__}: {e}")
            raise

    def _parse_response(self, response: Any) -> Dict[str, Any]:
        """
        Parse resposta da API Anthropic em formato normalizado.

        Args:
            response: Objeto de resposta da API Anthropic

        Returns:
            Dict com: {"text": str, "tool_calls": list, "raw": dict}
        """
        result = {
            "text": "",
            "tool_calls": [],
            "raw": response.model_dump() if hasattr(response, "model_dump") else {}
        }

        # Iterar pelos blocos de conteúdo
        for block in response.content:
            if block.type == "text":
                result["text"] += block.text

            elif block.type == "tool_use":
                result["tool_calls"].append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input
                })

        return result


# ============================================================================
# BEDROCK CLIENT
# ============================================================================

class BedrockClient:
    """
    Cliente para AWS Bedrock usando Converse API.

    Características:
    - Converse API (sem streaming por padrão)
    - Suporte a tools via ToolSpec
    - Logging em PT-BR sem vazar secrets
    - Tratamento de erros robusto (boto3)

    Exemplo:
        client = BedrockClient(aws_region="us-east-1")
        response = client.chat(
            messages=[{"role": "user", "content": "Olá!"}],
            tools=TOOL_SCHEMAS
        )
        # response: {"text": "...", "tool_calls": [...], "raw": {...}}
    """

    def __init__(
        self,
        aws_region: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        timeout: int = 30
    ):
        """
        Inicializar cliente Bedrock.

        Args:
            aws_region: Região AWS (usa AWS_REGION se None)
            model: Modelo Bedrock a usar (usa ANTHROPIC_BEDROCK_MODEL ou padrão se None)
            max_tokens: Máximo de tokens na resposta (padrão: 4096)
            temperature: Temperatura de geração 0.0-1.0 (padrão: 1.0)
            timeout: Timeout em segundos (padrão: 30)

        Raises:
            ValueError: Se aws_region não fornecida e AWS_REGION não definida
            RuntimeError: Se boto3 não instalado
        """
        if boto3 is None:
            raise RuntimeError(
                "boto3 não instalado. Instale com: pip install boto3>=1.28.0"
            )

        self.aws_region = aws_region or os.getenv("AWS_REGION")
        if not self.aws_region:
            raise ValueError(
                "AWS_REGION é obrigatória. "
                "Defina via variável de ambiente ou parâmetro aws_region."
            )

        # Default model - usar variável de ambiente ou fallback para Sonnet 3.5
        # Nota: Nome do Sonnet 4.5 no Bedrock ainda não confirmado, deixar ENV configurável
        self.model = model or os.getenv(
            "ANTHROPIC_BEDROCK_MODEL",
            "anthropic.claude-3-5-sonnet-20240620-v1:0"
        )
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout

        # Criar cliente bedrock-runtime
        try:
            self.client = boto3.client(
                "bedrock-runtime",
                region_name=self.aws_region,
                config=boto3.session.Config(
                    connect_timeout=timeout,
                    read_timeout=timeout
                )
            )
        except Exception as e:
            logger.error(f"❌ Bedrock: erro ao criar cliente boto3 - {e}")
            raise

        logger.info(
            f"🤖 BedrockClient inicializado (provider=bedrock, modelo: {self.model}, "
            f"region: {self.aws_region}, max_tokens: {max_tokens}, temperature: {temperature})"
        )

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False
    ) -> Dict[str, Any]:
        """
        Gerar resposta de chat com suporte a ferramentas.

        Args:
            messages: Lista de mensagens no formato:
                [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
            tools: Lista de ferramentas disponíveis (formato Anthropic, será convertido)
            stream: Não suportado no momento (reservado para futuro)

        Returns:
            Dict com:
            {
                "text": "resposta do assistente (string)",
                "tool_calls": [
                    {"id": "toolu_xxx", "name": "create_card", "input": {...}},
                    ...
                ],
                "raw": {...}  # resposta completa da API
            }

        Raises:
            RuntimeError: Erros da API Bedrock (throttling, invalid key, etc.)
            TimeoutError: Timeout excedido
        """
        # Converter mensagens para formato Converse
        converse_messages, system_prompt = convert_messages_to_converse(messages)

        # Converter tools para formato Bedrock (se fornecido)
        bedrock_tools = None
        if tools:
            bedrock_tools = convert_anthropic_schema_to_bedrock(tools)

        # Log da requisição (sem vazar conteúdo completo)
        tool_count = len(tools) if tools else 0
        logger.info(
            f"🤖 Bedrock: enviando {len(converse_messages)} mensagem(ns), "
            f"{tool_count} ferramenta(s) disponível(is)"
        )

        try:
            # Preparar kwargs para Converse API
            kwargs = {
                "modelId": self.model,
                "messages": converse_messages,
                "inferenceConfig": {
                    "maxTokens": self.max_tokens,
                    "temperature": self.temperature
                }
            }

            # Adicionar system prompt se presente
            if system_prompt:
                kwargs["system"] = [{"text": system_prompt}]

            # Adicionar tools se presente
            if bedrock_tools:
                kwargs["toolConfig"] = {"tools": bedrock_tools}

            # Chamar Converse API
            response = self.client.converse(**kwargs)

            # Parse resposta
            result = self._parse_response(response)

            # Log da resposta
            text_len = len(result["text"])
            tool_call_count = len(result["tool_calls"])
            logger.info(
                f"🤖 Bedrock: resposta recebida ({text_len} caracteres, "
                f"{tool_call_count} ferramenta(s) detectada(s))"
            )

            if tool_call_count > 0:
                tool_names = [tc["name"] for tc in result["tool_calls"]]
                logger.info(
                    f"🤖 Bedrock: ferramenta(s) detectada(s): {', '.join(tool_names)}"
                )

            return result

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_message = e.response.get("Error", {}).get("Message", str(e))
            logger.error(f"❌ Bedrock ClientError: {error_code} - {error_message}")

            if error_code == "ThrottlingException":
                raise RuntimeError("Rate limit excedido na API Bedrock")
            else:
                raise RuntimeError(f"Erro na API Bedrock: {error_message}")

        except BotoCoreError as e:
            logger.error(f"❌ Bedrock BotoCoreError: {e}")
            raise RuntimeError(f"Erro de rede/config Bedrock: {e}")

        except Exception as e:
            logger.error(f"❌ Bedrock: erro inesperado - {type(e).__name__}: {e}")
            raise

    def _parse_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse resposta da API Bedrock Converse em formato normalizado.

        Args:
            response: Dict de resposta da API Bedrock

        Returns:
            Dict com: {"text": str, "tool_calls": list, "raw": dict}
        """
        result = {
            "text": "",
            "tool_calls": [],
            "raw": response
        }

        # Extrair output message
        output_message = response.get("output", {}).get("message", {})
        content_blocks = output_message.get("content", [])

        # Iterar pelos blocos de conteúdo
        for block in content_blocks:
            # Block type pode ser: text ou toolUse
            if "text" in block:
                result["text"] += block["text"]

            elif "toolUse" in block:
                tool_use = block["toolUse"]
                result["tool_calls"].append({
                    "id": tool_use.get("toolUseId", ""),
                    "name": tool_use.get("name", ""),
                    "input": tool_use.get("input", {})
                })

        return result


# ============================================================================
# FACTORY FUNCTION
# ============================================================================

def get_anthropic_client(
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    **kwargs
) -> AnthropicClient:
    """
    Factory function para criar cliente Anthropic.

    Args:
        api_key: Chave API (opcional, usa ANTHROPIC_API_KEY)
        model: Modelo (opcional, usa ANTHROPIC_MODEL ou padrão)
        **kwargs: Argumentos adicionais para AnthropicClient

    Returns:
        Instância de AnthropicClient

    Exemplo:
        client = get_anthropic_client()
        response = client.chat([{"role": "user", "content": "Olá!"}])
    """
    return AnthropicClient(api_key=api_key, model=model, **kwargs)


def get_bedrock_client(
    aws_region: Optional[str] = None,
    model: Optional[str] = None,
    **kwargs
) -> BedrockClient:
    """
    Factory function para criar cliente Bedrock.

    Args:
        aws_region: Região AWS (opcional, usa AWS_REGION)
        model: Modelo Bedrock (opcional, usa ANTHROPIC_BEDROCK_MODEL ou padrão)
        **kwargs: Argumentos adicionais para BedrockClient

    Returns:
        Instância de BedrockClient

    Exemplo:
        client = get_bedrock_client(aws_region="us-east-1")
        response = client.chat(
            messages=[{"role": "user", "content": "Olá!"}],
            tools=TOOL_SCHEMAS
        )
    """
    return BedrockClient(aws_region=aws_region, model=model, **kwargs)
