"""Tests for safe gateway restart from messaging platforms."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionEntry, SessionSource, build_session_key


def _make_event(text="/restart", platform=Platform.SLACK, user_id="u1", chat_id="C123", thread_id="thread-1"):
    source = SessionSource(
        platform=platform,
        user_id=user_id,
        chat_id=chat_id,
        user_name="tester",
        chat_type="channel",
        thread_id=thread_id,
    )
    return MessageEvent(text=text, source=source, message_id="m1")


def _make_runner() -> object:
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._update_prompt_pending = {}
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._background_tasks = set()
    runner._shutdown_event = AsyncMock()
    runner._failed_platforms = {}
    runner._show_reasoning = False
    runner._provider_routing = {}
    runner._fallback_model = None
    runner.delivery_router = SimpleNamespace(adapters={})
    runner.session_store = MagicMock()
    session_entry = SessionEntry(
        session_key=build_session_key(_make_event().source),
        session_id="sess-1",
        created_at=__import__("datetime").datetime.now(),
        updated_at=__import__("datetime").datetime.now(),
        platform=Platform.SLACK,
        chat_type="channel",
    )
    runner.session_store.get_or_create_session.return_value = session_entry
    return runner


class TestHandleRestartCommand:
    @pytest.mark.asyncio
    async def test_writes_notification_and_requests_detached_restart(self, tmp_path):
        runner = _make_runner()
        event = _make_event()
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()

        with patch("gateway.run._hermes_home", hermes_home), \
             patch.dict("os.environ", {}, clear=True), \
             patch.object(runner, "request_restart", return_value=True) as mock_request_restart:
            result = await runner._handle_restart_command(event)

        assert "Restarting gateway" in result
        notify_path = hermes_home / ".restart_notify.json"
        assert notify_path.exists()
        payload = json.loads(notify_path.read_text())
        assert payload["platform"] == "slack"
        assert payload["chat_id"] == "C123"
        assert payload["thread_id"] == "thread-1"
        mock_request_restart.assert_called_once_with(detached=True, via_service=False)

    @pytest.mark.asyncio
    async def test_writes_restart_redelivery_dedup_marker(self, tmp_path):
        runner = _make_runner()
        event = _make_event(platform=Platform.TELEGRAM)
        event.platform_update_id = 12345
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()

        with patch("gateway.run._hermes_home", hermes_home), \
             patch.object(runner, "request_restart", return_value=True):
            result = await runner._handle_restart_command(event)

        assert "Restarting gateway" in result
        marker_path = hermes_home / ".restart_last_processed.json"
        assert marker_path.exists()
        payload = json.loads(marker_path.read_text())
        assert payload["platform"] == "telegram"
        assert payload["update_id"] == 12345
        assert isinstance(payload["requested_at"], float)


class TestSendRestartNotification:
    @pytest.mark.asyncio
    async def test_sends_restart_confirmation_back_to_thread(self, tmp_path):
        runner = _make_runner()
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        pending = {
            "platform": "slack",
            "chat_id": "C123",
            "thread_id": "1776523154.066209",
            "session_key": "agent:main:slack:group:C123:1776523154.066209",
            "timestamp": "2026-04-18T15:00:00",
        }
        (hermes_home / ".restart_notify.json").write_text(json.dumps(pending))
        adapter = MagicMock()
        adapter.send = AsyncMock(return_value=None)
        runner.adapters = {Platform.SLACK: adapter}

        with patch("gateway.run._hermes_home", hermes_home):
            result = await runner._send_restart_notification()

        assert result is None
        adapter.send.assert_awaited_once()
        args, kwargs = adapter.send.await_args
        assert args[0] == "C123"
        assert "gateway restarted" in args[1].lower()
        assert kwargs["metadata"]["thread_id"] == "1776523154.066209"
        assert not (hermes_home / ".restart_notify.json").exists()
