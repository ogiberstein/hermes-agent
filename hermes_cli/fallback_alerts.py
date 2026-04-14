from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

from hermes_cli.config import get_hermes_home

logger = logging.getLogger(__name__)

_HERMES_HOME = get_hermes_home()
_ALERT_STATE_PATH = _HERMES_HOME / "state" / "fallback_alert_state.json"
_ALERT_LOCK = threading.Lock()
_ALERT_COOLDOWN_SECONDS = 15 * 60


def _load_env_values(*keys: str) -> dict[str, str]:
    values = {k: os.getenv(k, "") for k in keys}
    env_path = _HERMES_HOME / ".env"
    if not env_path.exists():
        return values
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in values and not values[key]:
                values[key] = value
    except Exception:
        logger.debug("Failed reading ~/.hermes/.env for fallback alerts", exc_info=True)
    return values


def _tests_enabled() -> bool:
    return bool(os.getenv("PYTEST_CURRENT_TEST")) and os.getenv("HERMES_TEST_ALLOW_FALLBACK_ALERTS") not in {"1", "true", "yes"}


def _read_state() -> dict[str, Any]:
    try:
        if _ALERT_STATE_PATH.exists():
            data = json.loads(_ALERT_STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                if "last_sent" not in data:
                    # Back-compat with the initial flat map format.
                    return {"last_sent": data, "active": None}
                return {"last_sent": data.get("last_sent") or {}, "active": data.get("active")}
    except Exception:
        logger.debug("Failed reading fallback alert state", exc_info=True)
    return {"last_sent": {}, "active": None}


def _write_state(state: dict[str, Any]) -> None:
    _ALERT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ALERT_STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _load_whatsapp_chat_id() -> str:
    env_values = _load_env_values("WHATSAPP_FALLBACK_ALERT_CHAT_ID", "WHATSAPP_HOME_CHANNEL")
    explicit = (env_values.get("WHATSAPP_FALLBACK_ALERT_CHAT_ID") or "").strip()
    if explicit:
        return explicit

    jobs_path = _HERMES_HOME / "cron" / "jobs.json"
    if jobs_path.exists():
        try:
            payload = json.loads(jobs_path.read_text(encoding="utf-8"))
            jobs = payload.get("jobs") if isinstance(payload, dict) else payload
            for job in jobs or []:
                deliver = str(job.get("deliver") or "")
                if deliver.startswith("whatsapp:"):
                    return deliver.split(":", 1)[1].strip()
                target = job.get("target") or {}
                if str(target.get("platform") or "") == "whatsapp" and target.get("chat_id"):
                    return str(target.get("chat_id")).strip()
        except Exception:
            logger.debug("Failed reading cron jobs for WhatsApp fallback alert target", exc_info=True)

    config_path = _HERMES_HOME / "config.yaml"
    if config_path.exists():
        try:
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            fallback = str(cfg.get("WHATSAPP_FALLBACK_ALERT_CHAT_ID") or cfg.get("WHATSAPP_HOME_CHANNEL") or "").strip()
            if fallback:
                return fallback
        except Exception:
            logger.debug("Failed reading config.yaml for WhatsApp fallback alert target", exc_info=True)

    return ""


def get_alert_targets() -> dict[str, str]:
    env_values = _load_env_values("SLACK_BOT_TOKEN", "SLACK_HOME_CHANNEL")
    return {
        "slack_channel": (env_values.get("SLACK_HOME_CHANNEL") or "").strip(),
        "slack_configured": "yes" if (env_values.get("SLACK_BOT_TOKEN") or "").strip() and (env_values.get("SLACK_HOME_CHANNEL") or "").strip() else "no",
        "whatsapp_chat_id": _load_whatsapp_chat_id(),
        "whatsapp_configured": "yes" if _load_whatsapp_chat_id() else "no",
    }


def get_fallback_alert_status() -> dict[str, Any]:
    state = _read_state()
    targets = get_alert_targets()
    return {
        "active": state.get("active"),
        "cooldown_seconds": _ALERT_COOLDOWN_SECONDS,
        **targets,
    }


def _state_key(*, primary_provider: str, primary_model: str, fallback_provider: str, fallback_model: str, reason: str) -> str:
    return "|".join([
        primary_provider or "auto",
        primary_model or "",
        fallback_provider or "",
        fallback_model or "",
        reason or "unknown",
    ])


def _should_send(last_sent: dict[str, Any], state_key: str) -> bool:
    now = time.time()
    last = float(last_sent.get(state_key, 0) or 0)
    if now - last < _ALERT_COOLDOWN_SECONDS:
        return False
    last_sent[state_key] = now
    return True


def _build_activation_message(*, primary_provider: str, primary_model: str, fallback_provider: str, fallback_model: str, reason: str, scope: str) -> str:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    return (
        "⚠️ Hermes fallback activated\n"
        f"Scope: {scope}\n"
        f"Reason: {reason}\n"
        f"Primary: {primary_provider or 'auto'} / {primary_model or '-'}\n"
        f"Fallback: {fallback_provider or '-'} / {fallback_model or '-'}\n"
        f"Time: {stamp}\n"
        "OpenRouter may now be consuming paid credits."
    )


def _build_restored_message(*, primary_provider: str, primary_model: str, fallback_provider: str, fallback_model: str, scope: str) -> str:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    return (
        "✅ Hermes primary restored\n"
        f"Scope: {scope}\n"
        f"Primary: {primary_provider or 'auto'} / {primary_model or '-'}\n"
        f"Previous fallback: {fallback_provider or '-'} / {fallback_model or '-'}\n"
        f"Time: {stamp}\n"
        "Hermes should no longer be burning fallback OpenRouter credits for this outage path."
    )


def _send_slack(message: str) -> None:
    env_values = _load_env_values("SLACK_BOT_TOKEN", "SLACK_HOME_CHANNEL")
    token = (env_values.get("SLACK_BOT_TOKEN") or "").strip()
    channel = (env_values.get("SLACK_HOME_CHANNEL") or "").strip()
    if not token or not channel:
        return
    with httpx.Client(timeout=15) as client:
        response = client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": channel, "text": message},
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Slack API error: {payload}")


def _send_whatsapp(message: str) -> None:
    chat_id = _load_whatsapp_chat_id()
    if not chat_id:
        return
    with httpx.Client(timeout=15) as client:
        response = client.post(
            "http://127.0.0.1:3000/send",
            json={"chatId": chat_id, "message": message},
        )
        response.raise_for_status()


def _dispatch_async(message: str, *, thread_name: str) -> None:
    def _worker() -> None:
        for sender in (_send_slack, _send_whatsapp):
            try:
                sender(message)
            except Exception:
                logger.warning("Fallback alert send failed via %s", sender.__name__, exc_info=True)

    threading.Thread(target=_worker, name=thread_name, daemon=True).start()


def notify_fallback_activation(*, primary_provider: str, primary_model: str, fallback_provider: str, fallback_model: str, reason: str, scope: str) -> None:
    if _tests_enabled():
        return
    state_key = _state_key(
        primary_provider=primary_provider,
        primary_model=primary_model,
        fallback_provider=fallback_provider,
        fallback_model=fallback_model,
        reason=reason,
    )
    with _ALERT_LOCK:
        state = _read_state()
        if not _should_send(state.setdefault("last_sent", {}), state_key):
            state["active"] = {
                "primary_provider": primary_provider,
                "primary_model": primary_model,
                "fallback_provider": fallback_provider,
                "fallback_model": fallback_model,
                "reason": reason,
                "scope": scope,
                "activated_at": int(time.time()),
            }
            _write_state(state)
            return
        state["active"] = {
            "primary_provider": primary_provider,
            "primary_model": primary_model,
            "fallback_provider": fallback_provider,
            "fallback_model": fallback_model,
            "reason": reason,
            "scope": scope,
            "activated_at": int(time.time()),
        }
        _write_state(state)

    message = _build_activation_message(
        primary_provider=primary_provider,
        primary_model=primary_model,
        fallback_provider=fallback_provider,
        fallback_model=fallback_model,
        reason=reason,
        scope=scope,
    )
    _dispatch_async(message, thread_name="fallback-alert-activation")


def notify_primary_restored(*, primary_provider: str, primary_model: str, scope: str) -> bool:
    if _tests_enabled():
        return False
    with _ALERT_LOCK:
        state = _read_state()
        active = state.get("active")
        if not active:
            return False
        active_provider = str(active.get("primary_provider") or "")
        active_model = str(active.get("primary_model") or "")
        provider_matches = primary_provider == active_provider
        model_matches = (not primary_model) or (not active_model) or primary_model == active_model
        if not (provider_matches and model_matches):
            return False
        state_key = _state_key(
            primary_provider=active_provider,
            primary_model=active_model,
            fallback_provider=str(active.get("fallback_provider") or ""),
            fallback_model=str(active.get("fallback_model") or ""),
            reason="restored",
        )
        should_send = _should_send(state.setdefault("last_sent", {}), state_key)
        state["active"] = None
        _write_state(state)

    if not should_send:
        return True

    message = _build_restored_message(
        primary_provider=active_provider,
        primary_model=active_model,
        fallback_provider=str(active.get("fallback_provider") or ""),
        fallback_model=str(active.get("fallback_model") or ""),
        scope=scope,
    )
    _dispatch_async(message, thread_name="fallback-alert-restored")
    return True
