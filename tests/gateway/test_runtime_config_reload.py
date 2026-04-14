import yaml


def test_reload_runtime_config_if_needed_hot_reloads_fallback(monkeypatch, tmp_path):
    import gateway.run as gateway_run
    from gateway.run import GatewayRunner

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "fallback_model": {
                    "provider": "openrouter",
                    "model": "anthropic/claude-sonnet-4.6",
                }
            }
        ),
        encoding="utf-8",
    )

    runner = GatewayRunner.__new__(GatewayRunner)
    runner._runtime_config_state = None
    runner._prefill_messages = []
    runner._ephemeral_system_prompt = ""
    runner._reasoning_config = None
    runner._show_reasoning = False
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._smart_model_routing = {}

    assert runner._reload_runtime_config_if_needed(force=True) is True
    assert runner._fallback_model == {
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4.6",
    }

    assert runner._reload_runtime_config_if_needed() is False

    config_path.write_text(
        yaml.safe_dump(
            {
                "fallback_model": {
                    "provider": "openrouter",
                    "model": "google/gemini-2.5-pro-preview",
                }
            }
        ),
        encoding="utf-8",
    )

    assert runner._reload_runtime_config_if_needed() is True
    assert runner._fallback_model == {
        "provider": "openrouter",
        "model": "google/gemini-2.5-pro-preview",
    }
