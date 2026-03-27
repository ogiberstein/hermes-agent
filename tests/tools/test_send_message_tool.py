"""Tests for tools/send_message_tool.py."""

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from gateway.config import Platform
from tools.send_message_tool import _send_slack, _send_to_platform, send_message_tool


def _run_async_immediately(coro):
    return asyncio.run(coro)


def _make_config():
    telegram_cfg = SimpleNamespace(enabled=True, token="fake-token", extra={})
    return SimpleNamespace(
        platforms={Platform.TELEGRAM: telegram_cfg},
        get_home_channel=lambda _platform: None,
    ), telegram_cfg


class TestSendMessageTool:
    def test_sends_to_explicit_telegram_topic_target(self):
        config, telegram_cfg = _make_config()

        with patch.dict("os.environ", {"HERMES_SESSION_PLATFORM": "cli"}, clear=False), \
             patch("gateway.config.load_gateway_config", return_value=config), \
             patch("tools.interrupt.is_interrupted", return_value=False), \
             patch("model_tools._run_async", side_effect=_run_async_immediately), \
             patch("tools.send_message_tool._send_to_platform", new=AsyncMock(return_value={"success": True})) as send_mock, \
             patch("gateway.mirror.mirror_to_session", return_value=True) as mirror_mock:
            result = json.loads(
                send_message_tool(
                    {
                        "action": "send",
                        "target": "telegram:-1001:17585",
                        "message": "hello",
                    }
                )
            )

        assert result["success"] is True
        send_mock.assert_awaited_once_with(Platform.TELEGRAM, telegram_cfg, "-1001", "hello", thread_id="17585")
        mirror_mock.assert_called_once_with("telegram", "-1001", "hello", source_label="cli", thread_id="17585")

    def test_resolved_telegram_topic_name_preserves_thread_id(self):
        config, telegram_cfg = _make_config()

        with patch("gateway.config.load_gateway_config", return_value=config), \
             patch("tools.interrupt.is_interrupted", return_value=False), \
             patch("gateway.channel_directory.resolve_channel_name", return_value="-1001:17585"), \
             patch("model_tools._run_async", side_effect=_run_async_immediately), \
             patch("tools.send_message_tool._send_to_platform", new=AsyncMock(return_value={"success": True})) as send_mock, \
             patch("gateway.mirror.mirror_to_session", return_value=True):
            result = json.loads(
                send_message_tool(
                    {
                        "action": "send",
                        "target": "telegram:Coaching Chat / topic 17585",
                        "message": "hello",
                    }
                )
            )

        assert result["success"] is True
        send_mock.assert_awaited_once_with(Platform.TELEGRAM, telegram_cfg, "-1001", "hello", thread_id="17585")

    def test_send_to_platform_passes_slack_thread_id(self):
        slack_cfg = SimpleNamespace(token="xoxb-test", extra={})

        with patch("tools.send_message_tool._send_slack", new=AsyncMock(return_value={"success": True})) as send_mock:
            result = asyncio.run(_send_to_platform(Platform.SLACK, slack_cfg, "C123", "hello", thread_id="1774555596.462009"))

        assert result == {"success": True}
        send_mock.assert_awaited_once_with("xoxb-test", "C123", "hello", thread_id="1774555596.462009")

    def test_send_slack_includes_thread_ts_when_present(self):
        class FakeResponse:
            async def json(self):
                return {"ok": True, "ts": "1774555600.000100"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeSession:
            def __init__(self):
                self.calls = []

            def post(self, url, headers=None, json=None):
                self.calls.append({"url": url, "headers": headers, "json": json})
                return FakeResponse()

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        fake_session = FakeSession()

        with patch("aiohttp.ClientSession", return_value=fake_session):
            result = asyncio.run(_send_slack("xoxb-test", "C123", "hello", thread_id="1774555596.462009"))

        assert result["success"] is True
        assert fake_session.calls[0]["json"] == {
            "channel": "C123",
            "text": "hello",
            "thread_ts": "1774555596.462009",
        }
