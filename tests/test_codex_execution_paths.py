import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


sys.modules.setdefault("fire", types.SimpleNamespace(Fire=lambda *a, **k: None))
sys.modules.setdefault("firecrawl", types.SimpleNamespace(Firecrawl=object))
sys.modules.setdefault("fal_client", types.SimpleNamespace())

import cron.scheduler as cron_scheduler
import gateway.run as gateway_run
import run_agent
from gateway.config import Platform
from gateway.session import SessionSource
from hermes_cli.auth import AuthError
from hermes_cli.runtime_provider import resolve_runtime_provider_with_auth_fallback


def _write_runtime_config(config_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """
model:
  default: gpt-5.4
  provider: openai-codex
fallback_model:
  provider: openrouter
  model: anthropic/claude-sonnet-4.6
smart_model_routing:
  enabled: false
""".strip()
        + "\n",
        encoding="utf-8",
    )


class _RecordingAgent:
    last_init = {}

    def __init__(self, *args, **kwargs):
        type(self).last_init = dict(kwargs)

    def run_conversation(self, user_message, conversation_history=None, task_id=None):
        provider = type(self).last_init.get("provider")
        model = type(self).last_init.get("model")
        return {
            "completed": True,
            "final_response": f"{provider}:{model}",
            "messages": [],
            "tools": [],
        }


def _runtime_resolver_with_primary_auth_failure(*, requested=None, explicit_base_url=None):
    if requested in (None, "openai-codex"):
        raise AuthError(
            "Codex token refresh failed with status 401.",
            provider="openai-codex",
            code="invalid_grant",
            relogin_required=True,
        )
    if requested == "openrouter":
        return {
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "or-key",
        }
    raise AssertionError(f"unexpected provider request: {requested}")


def test_cron_run_job_auth_failure_uses_fallback_runtime(monkeypatch, tmp_path):
    hermes_home = tmp_path / "hermes"
    _write_runtime_config(hermes_home / "config.yaml")

    monkeypatch.setattr(cron_scheduler, "_hermes_home", hermes_home)
    monkeypatch.setattr(run_agent, "AIAgent", _RecordingAgent)
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        _runtime_resolver_with_primary_auth_failure,
    )
    monkeypatch.setattr("hermes_cli.runtime_provider.format_runtime_provider_error", lambda exc: str(exc))

    _RecordingAgent.last_init = {}

    success, output, final_response, error = cron_scheduler.run_job(
        {"id": "job-1", "name": "Codex Auth Failover", "prompt": "ping", "model": "gpt-5.4"}
    )

    assert success is True
    assert error is None
    assert final_response == "openrouter:anthropic/claude-sonnet-4.6"
    assert "openrouter:anthropic/claude-sonnet-4.6" in output
    assert _RecordingAgent.last_init["provider"] == "openrouter"
    assert _RecordingAgent.last_init["model"] == "anthropic/claude-sonnet-4.6"


def test_runtime_resolution_auth_failure_uses_fallback_runtime(monkeypatch, tmp_path):
    hermes_home = tmp_path / "hermes"
    _write_runtime_config(hermes_home / "config.yaml")

    monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        _runtime_resolver_with_primary_auth_failure,
    )
    monkeypatch.setattr("hermes_cli.runtime_provider.format_runtime_provider_error", lambda exc: str(exc))
    alert_calls = []
    monkeypatch.setattr(
        "hermes_cli.fallback_alerts.notify_fallback_activation",
        lambda **kwargs: alert_calls.append(kwargs),
    )
    monkeypatch.setenv("HERMES_TOOL_PROGRESS", "false")

    runtime, fallback = resolve_runtime_provider_with_auth_fallback(
        requested="openai-codex",
        fallback_model={"provider": "openrouter", "model": "anthropic/claude-sonnet-4.6"},
    )

    assert runtime["provider"] == "openrouter"
    assert runtime["api_key"] == "or-key"
    assert fallback == {"provider": "openrouter", "model": "anthropic/claude-sonnet-4.6"}
    assert len(alert_calls) == 1
    assert alert_calls[0]["fallback_provider"] == "openrouter"
    assert alert_calls[0]["reason"] == "auth_failure"


def test_gateway_run_agent_auth_failure_uses_fallback_runtime(monkeypatch, tmp_path):
    hermes_home = tmp_path / "hermes"
    _write_runtime_config(hermes_home / "config.yaml")

    monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)
    monkeypatch.setattr(run_agent, "AIAgent", _RecordingAgent)
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        _runtime_resolver_with_primary_auth_failure,
    )
    monkeypatch.setattr("hermes_cli.runtime_provider.format_runtime_provider_error", lambda exc: str(exc))
    monkeypatch.setenv("HERMES_TOOL_PROGRESS", "false")

    _RecordingAgent.last_init = {}

    runner = gateway_run.GatewayRunner.__new__(gateway_run.GatewayRunner)
    runner.adapters = {}
    runner._ephemeral_system_prompt = ""
    runner._prefill_messages = []
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = {"provider": "openrouter", "model": "anthropic/claude-sonnet-4.6"}
    runner._running_agents = {}
    runner.hooks = MagicMock()
    runner.hooks.emit = AsyncMock()
    runner.hooks.loaded_hooks = []
    runner._session_db = None

    source = SessionSource(
        platform=Platform.LOCAL,
        chat_id="cli",
        chat_name="CLI",
        chat_type="dm",
        user_id="test-user-1",
    )

    result = asyncio.run(
        runner._run_agent(
            message="hello",
            context_prompt="",
            history=[],
            source=source,
            session_id="session-401",
            session_key="agent:main:local:dm",
        )
    )

    assert result["final_response"] == "openrouter:anthropic/claude-sonnet-4.6"
    assert _RecordingAgent.last_init["provider"] == "openrouter"
    assert _RecordingAgent.last_init["model"] == "anthropic/claude-sonnet-4.6"