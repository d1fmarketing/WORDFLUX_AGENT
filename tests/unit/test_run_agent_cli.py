from __future__ import annotations

import json

import src.agents  # noqa: F401
from scripts import run_agent


def test_run_agent_cli_uses_message_payload(capsys) -> None:
    exit_code = run_agent.main(["--agent", "echo", "--message", "Hi there"])

    captured = capsys.readouterr()
    assert exit_code == 0
    data = json.loads(captured.out)
    assert data["message"] == "Hi there"
    assert data["agent"] == "echo"


def test_run_agent_cli_rejects_unknown_agent(capsys) -> None:
    exit_code = run_agent.main(["--agent", "missing", "--message", "noop"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Unknown agent" in captured.err
