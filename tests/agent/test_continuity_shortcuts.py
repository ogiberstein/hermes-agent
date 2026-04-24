"""Tests for docs-backed continuity shortcut expansion."""

from agent.continuity_shortcuts import (
    ContinuityShortcut,
    build_shortcut_prompt,
    expand_plain_shortcut,
    _build_shortcuts_by_name,
)


def test_build_handoff_prompt_includes_core_steps():
    prompt = build_shortcut_prompt("handoff")
    assert prompt is not None
    assert "Close this thread with handoff" in prompt
    assert "Update project docs" in prompt
    assert "Latest discussed" in prompt


def test_build_shortcut_prompt_appends_context():
    prompt = build_shortcut_prompt("bootstrap", "polymarket-bot")
    assert prompt is not None
    assert prompt.endswith("Context: polymarket-bot")


def test_alias_resolves_to_handoff_prompt():
    prompt = build_shortcut_prompt("handover", "urgent")
    assert prompt is not None
    assert "Close this thread with handoff" in prompt
    assert prompt.endswith("Context: urgent")


def test_project_init_prompt_mentions_force_refresh():
    prompt = build_shortcut_prompt("project-init", "force")
    assert prompt is not None
    assert "If the user included 'force'" in prompt


def test_expand_plain_shortcut_for_bootstrap_with_explicit_context_sentinel():
    expanded = expand_plain_shortcut("bootstrap: polymarket-bot")
    assert expanded is not None
    name, prompt = expanded
    assert name == "bootstrap"
    assert "bootstrap from latest project context" in prompt.lower()
    assert prompt.endswith("Context: polymarket-bot")


def test_expand_plain_shortcut_for_status():
    expanded = expand_plain_shortcut("status")
    assert expanded is not None
    name, prompt = expanded
    assert name == "status"
    assert "decision-grade" in prompt


def test_expand_plain_handover_command_phrase():
    expanded = expand_plain_shortcut("handover command")
    assert expanded is not None
    name, prompt = expanded
    assert name == "handoff"
    assert "Close this thread with handoff" in prompt


def test_expand_plain_shortcut_ignores_slash_commands_unknown_text_and_sentences():
    assert expand_plain_shortcut("/bootstrap") is None
    assert expand_plain_shortcut("hello there") is None
    assert expand_plain_shortcut("review the PR tomorrow") is None
    assert expand_plain_shortcut("memory is getting full") is None


def test_shortcut_alias_collisions_fail_fast(monkeypatch):
    import agent.continuity_shortcuts as shortcuts

    monkeypatch.setattr(
        shortcuts,
        "_SHORTCUTS",
        (
            ContinuityShortcut("alpha", "first", "A", aliases=("shared",)),
            ContinuityShortcut("beta", "second", "B", aliases=("shared",)),
        ),
    )

    try:
        _build_shortcuts_by_name()
    except ValueError as exc:
        assert "collision" in str(exc)
        assert "shared" in str(exc)
    else:
        raise AssertionError("Expected shortcut alias collision to fail fast")
