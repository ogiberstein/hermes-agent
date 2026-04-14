import json
from pathlib import Path

import hermes_cli.fallback_alerts as fallback_alerts


def test_activation_then_restore_updates_state_and_dispatches(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_TEST_ALLOW_FALLBACK_ALERTS", "1")
    monkeypatch.setattr(fallback_alerts, "_HERMES_HOME", tmp_path)
    monkeypatch.setattr(fallback_alerts, "_ALERT_STATE_PATH", tmp_path / "state" / "fallback_alert_state.json")

    sent = []
    monkeypatch.setattr(fallback_alerts, "_dispatch_async", lambda message, *, thread_name: sent.append((thread_name, message)))

    fallback_alerts.notify_fallback_activation(
        primary_provider="openai-codex",
        primary_model="gpt-5.4",
        fallback_provider="openrouter",
        fallback_model="anthropic/claude-sonnet-4.6",
        reason="auth_failure",
        scope="runtime_resolution",
    )

    state = json.loads((tmp_path / "state" / "fallback_alert_state.json").read_text())
    assert state["active"]["primary_provider"] == "openai-codex"
    assert state["active"]["fallback_provider"] == "openrouter"
    assert len(sent) == 1
    assert "fallback activated" in sent[0][1].lower()

    restored = fallback_alerts.notify_primary_restored(
        primary_provider="openai-codex",
        primary_model="gpt-5.4",
        scope="gateway_runtime_resolution",
    )

    assert restored is True
    state = json.loads((tmp_path / "state" / "fallback_alert_state.json").read_text())
    assert state["active"] is None
    assert len(sent) == 2
    assert "primary restored" in sent[1][1].lower()


def test_get_alert_targets_prefers_cron_whatsapp_target(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_TEST_ALLOW_FALLBACK_ALERTS", "1")
    monkeypatch.setattr(fallback_alerts, "_HERMES_HOME", tmp_path)
    monkeypatch.setattr(fallback_alerts, "_ALERT_STATE_PATH", tmp_path / "state" / "fallback_alert_state.json")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_HOME_CHANNEL", raising=False)

    (tmp_path / ".env").write_text(
        "SLACK_BOT_TOKEN=xoxb-test\nSLACK_HOME_CHANNEL=C123\n",
        encoding="utf-8",
    )
    (tmp_path / "cron").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cron" / "jobs.json").write_text(
        json.dumps(
            {
                "jobs": [
                    {"id": "job-1", "deliver": "whatsapp:29012621545538@lid"}
                ]
            }
        ),
        encoding="utf-8",
    )

    targets = fallback_alerts.get_alert_targets()

    assert targets["slack_configured"] == "yes"
    assert targets["slack_channel"] == "C123"
    assert targets["whatsapp_configured"] == "yes"
    assert targets["whatsapp_chat_id"] == "29012621545538@lid"
