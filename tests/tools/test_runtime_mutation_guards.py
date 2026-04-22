import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import tools.approval as approval_module
from tools.approval import check_all_command_guards
from tools.terminal_tool import terminal_tool


class _FakeEnv:
    env = {}

    def execute(self, command, **kwargs):  # pragma: no cover - should never run
        raise AssertionError(f"command unexpectedly executed: {command}")


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    approval_module._session_approved.clear()
    approval_module._pending.clear()
    approval_module._permanent_approved.clear()
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)
    monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
    monkeypatch.delenv("HERMES_EXEC_ASK", raising=False)
    monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setenv("HERMES_INTERACTIVE", "1")
    yield
    approval_module._session_approved.clear()
    approval_module._pending.clear()
    approval_module._permanent_approved.clear()


@patch("tools.tirith_security.check_command_security", return_value={"action": "allow", "findings": [], "summary": ""})
def test_active_uv_blocked_even_in_yolo_mode(_mock_tirith, monkeypatch):
    monkeypatch.setenv("HERMES_YOLO_MODE", "1")
    monkeypatch.setenv("VIRTUAL_ENV", "/root/.hermes/hermes-agent/venv")

    result = check_all_command_guards(
        "uv run --active python -m trading_strategies.cli replay-thalex-wheel",
        "local",
        workdir="/root/projects/trading-strategies",
        env_context={"VIRTUAL_ENV": "/root/.hermes/hermes-agent/venv", "PATH": os.environ.get("PATH", "")},
    )

    assert result["approved"] is False
    assert result.get("status") == "blocked"
    assert "--active" in result["message"]
    assert "uv run" in result["message"]


@patch("tools.tirith_security.check_command_security", return_value={"action": "allow", "findings": [], "summary": ""})
def test_hermes_venv_target_blocked_when_workdir_is_non_hermes(_mock_tirith, monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/root/.hermes/hermes-agent/venv")

    result = check_all_command_guards(
        "python -m venv /root/.hermes/hermes-agent/venv",
        "local",
        workdir="/root/projects/trading-strategies",
        env_context={"VIRTUAL_ENV": "/root/.hermes/hermes-agent/venv", "PATH": os.environ.get("PATH", "")},
    )

    assert result["approved"] is False
    assert result.get("status") == "blocked"
    assert "/root/.hermes/hermes-agent/venv" in result["message"]


@patch("tools.tirith_security.check_command_security", return_value={"action": "allow", "findings": [], "summary": ""})
def test_package_mutation_blocked_when_hermes_env_is_active(_mock_tirith, monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/root/.hermes/hermes-agent/venv")

    result = check_all_command_guards(
        "uv sync",
        "local",
        workdir="/root/projects/trading-strategies",
        env_context={"VIRTUAL_ENV": "/root/.hermes/hermes-agent/venv", "PATH": os.environ.get("PATH", "")},
    )

    assert result["approved"] is False
    assert result.get("status") == "blocked"
    assert "active virtualenv" in result["message"]


@patch("tools.tirith_security.check_command_security", return_value={"action": "allow", "findings": [], "summary": ""})
def test_plain_uv_run_allowed_in_project_repo(_mock_tirith, monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/root/.hermes/hermes-agent/venv")

    result = check_all_command_guards(
        "uv run python -m trading_strategies.cli replay-thalex-wheel",
        "local",
        workdir="/root/projects/trading-strategies",
        env_context={"VIRTUAL_ENV": "/root/.hermes/hermes-agent/venv", "PATH": os.environ.get("PATH", "")},
    )

    assert result["approved"] is True


@patch("tools.terminal_tool._create_environment", return_value=_FakeEnv())
def test_terminal_force_cannot_bypass_runtime_guard(_mock_env, monkeypatch, tmp_path):
    monkeypatch.setenv("VIRTUAL_ENV", "/root/.hermes/hermes-agent/venv")

    result = json.loads(
        terminal_tool(
            "uv run --active python -V",
            workdir="/root/projects/trading-strategies",
            task_id=f"test-{tmp_path.name}",
            force=True,
        )
    )

    assert result["status"] == "blocked"
    assert "--active" in result["error"]
