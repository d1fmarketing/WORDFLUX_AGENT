"""Unit tests for Anthropic LLM client."""
from __future__ import annotations

import os
import pytest
from unittest.mock import MagicMock, patch, Mock

# Test the client
from src.core.llm_client import (
    AnthropicClient,
    get_anthropic_client,
    TOOL_SCHEMAS
)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def mock_anthropic_response():
    """Mock Anthropic API response with text only."""
    response = MagicMock()
    response.content = [
        MagicMock(type="text", text="Olá! Como posso ajudar?")
    ]
    response.model_dump.return_value = {"id": "msg_123", "model": "claude-sonnet-4-5"}
    return response


@pytest.fixture
def mock_anthropic_response_with_tool():
    """Mock Anthropic API response with text + tool_use."""
    response = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Vou criar o card agora."

    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "toolu_abc123"
    tool_block.name = "create_card"
    tool_block.input = {"title": "Landing page Q4", "intent": "Criar landing page"}

    response.content = [text_block, tool_block]
    response.model_dump.return_value = {"id": "msg_456", "model": "claude-sonnet-4-5"}
    return response


@pytest.fixture
def mock_anthropic_response_multiple_tools():
    """Mock Anthropic API response with multiple tool calls."""
    response = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Vou listar os cards e depois criar um novo."

    tool1 = MagicMock()
    tool1.type = "tool_use"
    tool1.id = "toolu_001"
    tool1.name = "list_cards"
    tool1.input = {"column": "Backlog"}

    tool2 = MagicMock()
    tool2.type = "tool_use"
    tool2.id = "toolu_002"
    tool2.name = "create_card"
    tool2.input = {"title": "New card", "intent": "Test"}

    response.content = [text_block, tool1, tool2]
    response.model_dump.return_value = {"id": "msg_789", "model": "claude-sonnet-4-5"}
    return response


# ============================================================================
# INITIALIZATION TESTS
# ============================================================================

def test_anthropic_client_requires_api_key():
    """Test that AnthropicClient requires API key."""
    # Remove env var if exists
    old_key = os.environ.pop("ANTHROPIC_API_KEY", None)

    try:
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY é obrigatória"):
            AnthropicClient()
    finally:
        if old_key:
            os.environ["ANTHROPIC_API_KEY"] = old_key


def test_anthropic_client_accepts_api_key_parameter():
    """Test that AnthropicClient accepts api_key parameter."""
    client = AnthropicClient(api_key="sk-ant-test-key")
    assert client.api_key == "sk-ant-test-key"


def test_anthropic_client_uses_env_var():
    """Test that AnthropicClient uses ANTHROPIC_API_KEY from environment."""
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-env-key"}):
        client = AnthropicClient()
        assert client.api_key == "sk-ant-env-key"


def test_anthropic_client_default_model():
    """Test that AnthropicClient uses default model."""
    client = AnthropicClient(api_key="sk-ant-test")
    assert client.model == "claude-sonnet-4-5-20250929"


def test_anthropic_client_custom_model():
    """Test that AnthropicClient accepts custom model."""
    client = AnthropicClient(api_key="sk-ant-test", model="claude-opus-4")
    assert client.model == "claude-opus-4"


def test_anthropic_client_model_from_env():
    """Test that AnthropicClient uses ANTHROPIC_MODEL from environment."""
    with patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "ANTHROPIC_MODEL": "claude-haiku-4"
    }):
        client = AnthropicClient()
        assert client.model == "claude-haiku-4"


def test_anthropic_client_default_config_values():
    """Test that AnthropicClient has correct default values."""
    client = AnthropicClient(api_key="sk-ant-test")
    assert client.max_tokens == 4096
    assert client.temperature == 1.0
    assert client.timeout == 30


def test_anthropic_client_custom_config_values():
    """Test that AnthropicClient accepts custom config values."""
    client = AnthropicClient(
        api_key="sk-ant-test",
        max_tokens=8000,
        temperature=0.5,
        timeout=60
    )
    assert client.max_tokens == 8000
    assert client.temperature == 0.5
    assert client.timeout == 60


# ============================================================================
# CHAT METHOD TESTS
# ============================================================================

@patch("anthropic.Anthropic")
def test_chat_without_tools(mock_anthropic_class, mock_anthropic_response):
    """Test chat method without tools."""
    # Setup mock
    mock_client_instance = MagicMock()
    mock_client_instance.messages.create.return_value = mock_anthropic_response
    mock_anthropic_class.return_value = mock_client_instance

    # Create client and call chat
    client = AnthropicClient(api_key="sk-ant-test")
    result = client.chat(messages=[{"role": "user", "content": "Olá"}])

    # Assertions
    assert result["text"] == "Olá! Como posso ajudar?"
    assert result["tool_calls"] == []
    assert "raw" in result

    # Verify API called correctly
    mock_client_instance.messages.create.assert_called_once()
    call_args = mock_client_instance.messages.create.call_args[1]
    assert call_args["model"] == "claude-sonnet-4-5-20250929"
    assert call_args["messages"] == [{"role": "user", "content": "Olá"}]
    assert "tools" not in call_args  # No tools should be passed


@patch("anthropic.Anthropic")
def test_chat_with_tools(mock_anthropic_class, mock_anthropic_response_with_tool):
    """Test chat method with tools."""
    # Setup mock
    mock_client_instance = MagicMock()
    mock_client_instance.messages.create.return_value = mock_anthropic_response_with_tool
    mock_anthropic_class.return_value = mock_client_instance

    # Create client and call chat
    client = AnthropicClient(api_key="sk-ant-test")
    result = client.chat(
        messages=[{"role": "user", "content": "Crie uma tarefa 'Landing page Q4'"}],
        tools=TOOL_SCHEMAS
    )

    # Assertions
    assert result["text"] == "Vou criar o card agora."
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["id"] == "toolu_abc123"
    assert result["tool_calls"][0]["name"] == "create_card"
    assert result["tool_calls"][0]["input"]["title"] == "Landing page Q4"

    # Verify tools were passed
    call_args = mock_client_instance.messages.create.call_args[1]
    assert call_args["tools"] == TOOL_SCHEMAS


@patch("anthropic.Anthropic")
def test_chat_with_system_message(mock_anthropic_class, mock_anthropic_response):
    """Test chat method extracts system message."""
    # Setup mock
    mock_client_instance = MagicMock()
    mock_client_instance.messages.create.return_value = mock_anthropic_response
    mock_anthropic_class.return_value = mock_client_instance

    # Create client and call chat
    client = AnthropicClient(api_key="sk-ant-test")
    result = client.chat(messages=[
        {"role": "system", "content": "Você é um assistente útil"},
        {"role": "user", "content": "Olá"}
    ])

    # Verify system message extracted
    call_args = mock_client_instance.messages.create.call_args[1]
    assert call_args["system"] == "Você é um assistente útil"
    assert call_args["messages"] == [{"role": "user", "content": "Olá"}]


# ============================================================================
# RESPONSE PARSING TESTS
# ============================================================================

def test_parse_text_only_response(mock_anthropic_response):
    """Test parsing response with text only."""
    client = AnthropicClient(api_key="sk-ant-test")
    result = client._parse_response(mock_anthropic_response)

    assert result["text"] == "Olá! Como posso ajudar?"
    assert result["tool_calls"] == []
    assert "raw" in result


def test_parse_response_with_tool_calls(mock_anthropic_response_with_tool):
    """Test parsing response with tool calls."""
    client = AnthropicClient(api_key="sk-ant-test")
    result = client._parse_response(mock_anthropic_response_with_tool)

    assert result["text"] == "Vou criar o card agora."
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["name"] == "create_card"


def test_parse_multiple_tool_calls(mock_anthropic_response_multiple_tools):
    """Test parsing response with multiple tool calls."""
    client = AnthropicClient(api_key="sk-ant-test")
    result = client._parse_response(mock_anthropic_response_multiple_tools)

    assert result["text"] == "Vou listar os cards e depois criar um novo."
    assert len(result["tool_calls"]) == 2
    assert result["tool_calls"][0]["name"] == "list_cards"
    assert result["tool_calls"][1]["name"] == "create_card"


def test_tool_call_format_matches_spec():
    """Test that tool call format matches Anthropic spec."""
    client = AnthropicClient(api_key="sk-ant-test")

    # Create mock response
    response = MagicMock()
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "toolu_test"
    tool_block.name = "propose_move"
    tool_block.input = {"card_id": "c-123", "to_column": "In Progress"}
    response.content = [tool_block]
    response.model_dump.return_value = {}

    result = client._parse_response(response)
    tool_call = result["tool_calls"][0]

    # Verify format: {"id": "...", "name": "...", "input": {...}}
    assert "id" in tool_call
    assert "name" in tool_call
    assert "input" in tool_call
    assert tool_call["id"] == "toolu_test"
    assert tool_call["name"] == "propose_move"
    assert isinstance(tool_call["input"], dict)


# ============================================================================
# ERROR HANDLING TESTS
# ============================================================================

@patch("anthropic.Anthropic")
def test_api_error_handling(mock_anthropic_class):
    """Test handling of Anthropic API errors."""
    import anthropic

    # Setup mock to raise APIError
    mock_client_instance = MagicMock()
    mock_anthropic_class.return_value = mock_client_instance

    error = anthropic.APIStatusError(
        message="Invalid API key",
        response=MagicMock(status_code=401),
        body=None
    )
    mock_client_instance.messages.create.side_effect = error

    # Create client and expect RuntimeError
    client = AnthropicClient(api_key="sk-ant-invalid")
    with pytest.raises(RuntimeError, match="Erro na API Anthropic"):
        client.chat(messages=[{"role": "user", "content": "test"}])


@patch("anthropic.Anthropic")
def test_timeout_error_handling(mock_anthropic_class):
    """Test handling of timeout errors."""
    import anthropic

    # Setup mock to raise APITimeoutError
    mock_client_instance = MagicMock()
    mock_anthropic_class.return_value = mock_client_instance
    mock_client_instance.messages.create.side_effect = anthropic.APITimeoutError(
        request=MagicMock()
    )

    # Create client and expect TimeoutError
    client = AnthropicClient(api_key="sk-ant-test")
    with pytest.raises(TimeoutError, match="Timeout na API Anthropic"):
        client.chat(messages=[{"role": "user", "content": "test"}])


@patch("anthropic.Anthropic")
def test_rate_limit_error_handling(mock_anthropic_class):
    """Test handling of rate limit errors."""
    import anthropic

    # Setup mock to raise RateLimitError
    mock_client_instance = MagicMock()
    mock_anthropic_class.return_value = mock_client_instance

    # Create a proper RateLimitError (inherits from APIStatusError)
    error = anthropic.RateLimitError(
        message="Rate limit exceeded",
        response=MagicMock(status_code=429),
        body=None
    )
    mock_client_instance.messages.create.side_effect = error

    # Create client and expect RuntimeError
    client = AnthropicClient(api_key="sk-ant-test")
    with pytest.raises(RuntimeError, match="Rate limit excedido"):
        client.chat(messages=[{"role": "user", "content": "test"}])


# ============================================================================
# TOOL SCHEMA TESTS
# ============================================================================

def test_tool_schemas_count():
    """Test that we have all 5 required tool schemas."""
    assert len(TOOL_SCHEMAS) == 5


def test_tool_schemas_have_required_fields():
    """Test that all tool schemas have required fields."""
    for tool in TOOL_SCHEMAS:
        assert "name" in tool
        assert "description" in tool
        assert "input_schema" in tool
        assert isinstance(tool["input_schema"], dict)
        assert tool["input_schema"]["type"] == "object"
        assert "properties" in tool["input_schema"]


def test_tool_schemas_anthropic_format():
    """Test that tool schemas are in Anthropic format (not OpenAI)."""
    for tool in TOOL_SCHEMAS:
        # Anthropic format should have "input_schema", not "parameters"
        assert "input_schema" in tool
        assert "parameters" not in tool

        # Should not have "type": "function" wrapper (that's OpenAI format)
        assert tool.get("type") != "function"

        # Should have direct "name" field (not nested in "function")
        assert "name" in tool
        assert "function" not in tool


def test_tool_schema_names():
    """Test that all required tools are present."""
    tool_names = {tool["name"] for tool in TOOL_SCHEMAS}
    expected_names = {
        "create_card",
        "move_card",
        "update_card",
        "summarize_board",  # Reads board state inline
        "ingest_email"      # Extracts intent from email
    }
    assert tool_names == expected_names


def test_create_card_has_portuguese_columns():
    """Test create_card tool has Portuguese column enum (5-column structure)."""
    create_card = next(t for t in TOOL_SCHEMAS if t["name"] == "create_card")
    column_prop = create_card["input_schema"]["properties"]["column"]

    assert "enum" in column_prop
    expected_columns = ["Espera", "Produção", "Aprovação", "Agendado", "Finalizado"]
    assert column_prop["enum"] == expected_columns


def test_move_card_has_portuguese_columns():
    """Test move_card tool has Portuguese column enum (5-column structure)."""
    move_card = next(t for t in TOOL_SCHEMAS if t["name"] == "move_card")
    to_column_prop = move_card["input_schema"]["properties"]["to_column"]

    assert "enum" in to_column_prop
    expected_columns = ["Espera", "Produção", "Aprovação", "Agendado", "Finalizado"]
    assert to_column_prop["enum"] == expected_columns


def test_high_risk_warning_in_move_card():
    """Test move_card description is present (high-risk logic is in backend)."""
    move_card = next(t for t in TOOL_SCHEMAS if t["name"] == "move_card")
    # High-risk warning is now handled in backend (is_high_risk_action)
    # Tool description just describes what it does
    assert "mover" in move_card["description"].lower() or "propõe" in move_card["description"].lower()


def test_all_tools_have_portuguese_descriptions():
    """Test all tool descriptions are in Portuguese."""
    portuguese_indicators = [
        "criar", "cria", "mover", "propõe", "card", "quadro", "tarefa",
        "adicionar", "executar", "enfileirar", "execução", "agente",
        "resumo", "board", "coluna", "atualização", "extrai", "email"
    ]

    for tool in TOOL_SCHEMAS:
        desc = tool["description"].lower()
        # At least one Portuguese word should appear
        assert any(word in desc for word in portuguese_indicators), \
            f"Tool {tool['name']} description may not be in Portuguese: {desc}"


def test_tool_required_fields():
    """Test all tools have proper required fields."""
    required_checks = {
        "create_card": ["title", "column"],
        "move_card": ["card_id", "to_column"],
        "update_card": ["card_id", "fields"],
        "summarize_board": [],  # No required fields
        "ingest_email": ["raw_text"]
    }

    for tool in TOOL_SCHEMAS:
        expected_required = required_checks.get(tool["name"], [])
        actual_required = tool["input_schema"].get("required", [])
        assert set(actual_required) == set(expected_required), \
            f"Tool {tool['name']} has wrong required fields. Expected: {expected_required}, Got: {actual_required}"


# ============================================================================
# FACTORY FUNCTION TESTS
# ============================================================================

def test_get_anthropic_client_returns_client():
    """Test that factory function returns AnthropicClient instance."""
    client = get_anthropic_client(api_key="sk-ant-test")
    assert isinstance(client, AnthropicClient)


def test_get_anthropic_client_accepts_kwargs():
    """Test that factory function passes kwargs to constructor."""
    client = get_anthropic_client(
        api_key="sk-ant-test",
        model="claude-opus-4",
        max_tokens=8000,
        temperature=0.7
    )
    assert client.model == "claude-opus-4"
    assert client.max_tokens == 8000
    assert client.temperature == 0.7
