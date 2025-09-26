from __future__ import annotations

import src.agents  # noqa: F401
from src.core.registry import create_agent


def test_echo_agent_returns_message_metadata() -> None:
    agent = create_agent("echo")
    payload = {"message": "Hello world"}

    result = agent.run(payload)

    assert result["agent"] == "echo"
    assert result["message"] == "Hello world"
    assert result["characters"] == len("Hello world")
    assert result["words"] == 2


def test_echo_agent_handles_missing_message() -> None:
    agent = create_agent("echo")

    result = agent.run({})

    assert result["message"] == ""
    assert result["words"] == 0
