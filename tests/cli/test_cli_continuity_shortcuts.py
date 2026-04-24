"""Tests for CLI continuity shortcut dispatch."""

from unittest.mock import MagicMock

from cli import HermesCLI


def _make_cli():
    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj.config = {}
    cli_obj.console = MagicMock()
    cli_obj.agent = None
    cli_obj.conversation_history = []
    cli_obj.session_id = "sess-123"
    cli_obj._pending_input = MagicMock()
    return cli_obj


def test_cli_slash_handover_alias_queues_handoff_prompt():
    cli_obj = _make_cli()

    result = cli_obj.process_command("/handover polymarket-bot")

    assert result is True
    cli_obj._pending_input.put.assert_called_once()
    queued = cli_obj._pending_input.put.call_args[0][0]
    assert "Close this thread with handoff" in queued
    assert queued.endswith("Context: polymarket-bot")


def test_cli_slash_bootstrap_queues_bootstrap_prompt():
    cli_obj = _make_cli()

    result = cli_obj.process_command("/bootstrap growth-lab")

    assert result is True
    cli_obj._pending_input.put.assert_called_once()
    queued = cli_obj._pending_input.put.call_args[0][0]
    assert "bootstrap from latest project context" in queued.lower()
    assert queued.endswith("Context: growth-lab")


def test_cli_hyphenated_project_init_queues_project_init_prompt():
    cli_obj = _make_cli()

    result = cli_obj.process_command("/project-init force")

    assert result is True
    cli_obj._pending_input.put.assert_called_once()
    queued = cli_obj._pending_input.put.call_args[0][0]
    assert "Initialize or refresh the project memory bootstrap snapshot" in queued
    assert queued.endswith("Context: force")
