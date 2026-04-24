"""Tests for gateway continuity shortcut handling."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionEntry, SessionSource, build_session_key


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
    )


def _make_event(text: str) -> MessageEvent:
    return MessageEvent(text=text, source=_make_source(), message_id="m1")


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    adapter = MagicMock()
    adapter.send = AsyncMock()
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)

    session_entry = SessionEntry(
        session_key=build_session_key(_make_source()),
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store.load_transcript.return_value = []
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.append_to_transcript = MagicMock()
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.update_session = MagicMock()
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._show_reasoning = False
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._should_send_voice_reply = lambda *_args, **_kwargs: False
    runner._send_voice_reply = AsyncMock()
    runner._capture_gateway_honcho_if_configured = lambda *args, **kwargs: None
    runner._emit_gateway_run_progress = AsyncMock()
    runner._handle_message_with_agent = AsyncMock(return_value="ok")
    return runner


@pytest.mark.asyncio
async def test_slash_handoff_routes_to_agent_not_unknown():
    runner = _make_runner()

    result = await runner._handle_message(_make_event("/handoff polymarket-bot"))

    assert result == "ok"
    forwarded_event = runner._handle_message_with_agent.await_args.args[0]
    assert "Close this thread with handoff" in forwarded_event.text
    assert forwarded_event.text.endswith("Context: polymarket-bot")


@pytest.mark.asyncio
async def test_plain_bootstrap_expands_before_agent_run_with_explicit_context_sentinel():
    runner = _make_runner()

    result = await runner._handle_message(_make_event("bootstrap: polymarket-bot"))

    assert result == "ok"
    forwarded_event = runner._handle_message_with_agent.await_args.args[0]
    assert "bootstrap from latest project context" in forwarded_event.text.lower()
    assert forwarded_event.text.endswith("Context: polymarket-bot")


@pytest.mark.asyncio
async def test_plain_handover_command_phrase_expands_before_agent_run():
    runner = _make_runner()

    result = await runner._handle_message(_make_event("handover command"))

    assert result == "ok"
    forwarded_event = runner._handle_message_with_agent.await_args.args[0]
    assert "Close this thread with handoff" in forwarded_event.text


@pytest.mark.asyncio
async def test_plain_review_sentence_does_not_expand():
    runner = _make_runner()

    result = await runner._handle_message(_make_event("review the PR tomorrow"))

    assert result == "ok"
    forwarded_event = runner._handle_message_with_agent.await_args.args[0]
    assert forwarded_event.text == "review the PR tomorrow"
