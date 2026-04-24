"""Regression contracts for commands that must not silently disappear.

These commands are operational muscle memory for long-running Hermes sessions.
They intentionally duplicate the expected command/alias list instead of deriving it
from COMMAND_SHORTCUTS, so removing a command from both implementation and shortcut
metadata still fails CI.
"""

from __future__ import annotations

from hermes_cli.commands import COMMANDS, GATEWAY_KNOWN_COMMANDS, resolve_command


CRITICAL_CONTINUITY_COMMANDS = {
    "handoff": ("handover",),
    "bootstrap": (),
    "project-init": (),
    "retro": (),
    "decision": (),
    "memory": (),
    "cos": (),
    "review": (),
}

CRITICAL_OPERATIONS_COMMANDS = {
    "restart": (),
    "stop": (),
    "agents": ("tasks",),
    "help": (),
    "commands": (),
}


def _assert_gateway_command_contract(command_name: str, aliases: tuple[str, ...]) -> None:
    resolved = resolve_command(command_name)
    assert resolved is not None, f"/{command_name} disappeared from command registry"
    assert resolved.name == command_name
    assert command_name in GATEWAY_KNOWN_COMMANDS, f"/{command_name} is not available in gateway"

    for alias in aliases:
        alias_resolved = resolve_command(alias)
        assert alias_resolved is not None, f"/{alias} alias disappeared from command registry"
        assert alias_resolved.name == command_name, f"/{alias} no longer resolves to /{command_name}"
        assert alias in GATEWAY_KNOWN_COMMANDS, f"/{alias} alias is not available in gateway"


def test_critical_continuity_commands_are_gateway_available() -> None:
    for command_name, aliases in CRITICAL_CONTINUITY_COMMANDS.items():
        _assert_gateway_command_contract(command_name, aliases)
        # Continuity commands must also work in CLI/plain slash form.
        assert f"/{command_name}" in COMMANDS
        for alias in aliases:
            assert f"/{alias}" in COMMANDS


def test_critical_operations_commands_are_gateway_available() -> None:
    for command_name, aliases in CRITICAL_OPERATIONS_COMMANDS.items():
        _assert_gateway_command_contract(command_name, aliases)
