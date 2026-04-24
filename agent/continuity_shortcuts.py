"""Shared continuity shortcut definitions for CLI, gateway, and docs-backed prompts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContinuityShortcut:
    name: str
    description: str
    prompt_template: str
    plain_trigger: bool = True
    aliases: tuple[str, ...] = ()


_SHORTCUTS: tuple[ContinuityShortcut, ...] = (
    ContinuityShortcut(
        name="handoff",
        aliases=("handover",),
        description="Close the current thread with docs+memory handoff.",
        prompt_template=(
            "Close this thread with handoff:\n"
            "1) Update project docs (STATUS.md, BRIEF.md, DECISIONS.md if a decision changed)\n"
            "2) Update memory rolling snapshot for this project using:\n"
            "   Latest discussed: ...\n"
            "   Next step: ...\n"
            "3) Return the exact handoff summary in 5 bullets max."
        ),
    ),
    ContinuityShortcut(
        name="bootstrap",
        description="Bootstrap from the latest project context before answering.",
        prompt_template=(
            "Before answering, bootstrap from latest project context:\n"
            "- Read latest STATUS.md, BRIEF.md, DECISIONS.md\n"
            "- Load latest project memory snapshot (Latest discussed / Next step)\n"
            "Then answer my request."
        ),
    ),
    ContinuityShortcut(
        name="project-init",
        description="Initialize or refresh the project memory bootstrap snapshot.",
        prompt_template=(
            "Initialize or refresh the project memory bootstrap snapshot.\n"
            "- Read the latest project docs needed to determine the current state\n"
            "- Ensure the rolling project memory snapshot exists and uses only:\n"
            "  Latest discussed: ...\n"
            "  Next step: ...\n"
            "  optional Risk watch: ...\n"
            "- If the user included 'force', refresh/reset the snapshot template before writing\n"
            "- Return a short note describing what changed"
        ),
    ),
    ContinuityShortcut(
        name="retro",
        description="Generate the incident retro packet and escalation blocks.",
        prompt_template=(
            "Run an incident retro for this issue.\n"
            "Return:\n"
            "1) What happened\n"
            "2) Root causes\n"
            "3) Fixes / prevention steps\n"
            "4) A copy-ready CoS review request block\n"
            "5) A copy-ready BRAIN PM review request block"
        ),
    ),
    ContinuityShortcut(
        name="decision",
        description="Log a decision capsule and update the snapshot.",
        prompt_template=(
            "Log this as a decision capsule:\n"
            "- Decision\n"
            "- Why\n"
            "- Revisit trigger\n"
            "Update DECISIONS.md and memory snapshot accordingly."
        ),
    ),
    ContinuityShortcut(
        name="memory",
        description="Update only the rolling project memory snapshot.",
        prompt_template=(
            "Memory checkpoint only:\n"
            "Update project rolling snapshot now with\n"
            "Latest discussed: ...\n"
            "Next step: ...\n"
            "Do not do any other work."
        ),
    ),
    ContinuityShortcut(
        name="cos",
        description="Run the CoS bootstrap before answering.",
        prompt_template=(
            "CoS bootstrap first:\n"
            "- Load cross-project CoS index\n"
            "- Load this project's latest snapshot (Latest discussed / Next step)\n"
            "- Read only essential docs needed to answer\n"
            "Then give me the recommendation."
        ),
    ),
    ContinuityShortcut(
        name="review",
        description="Run the weekly review structure for this area.",
        prompt_template=(
            "Run weekly review for this area:\n"
            "- What went well\n"
            "- What didn't\n"
            "- Root causes\n"
            "- Top improvements next week (max 5)\n"
            "Use discussion history + docs + memory as evidence."
        ),
    ),
    ContinuityShortcut(
        name="status",
        description="Run the decision-grade status flow.",
        prompt_template=(
            "Status update (decision-grade):\n"
            "1) Pull live execution signal first,\n"
            "2) verify the deployed runtime/entrypoint if this is a live run or deployment-sensitive question,\n"
            "3) then reconcile with docs,\n"
            "4) then return only material changes, blockers, and next step.\n"
            "If runtime truth is not verified, say the status is non-decision-grade."
        ),
    ),
)

def _build_shortcuts_by_name() -> dict[str, ContinuityShortcut]:
    shortcuts: dict[str, ContinuityShortcut] = {}
    for shortcut in _SHORTCUTS:
        for key in (shortcut.name, *shortcut.aliases):
            if key in shortcuts:
                raise ValueError(
                    f"Continuity shortcut name/alias collision for {key!r}: "
                    f"{shortcuts[key].name!r} and {shortcut.name!r}"
                )
            shortcuts[key] = shortcut
    return shortcuts


SHORTCUTS_BY_NAME = _build_shortcuts_by_name()

# These shortcuts are intended to be real slash/plain-text continuity commands.
# ``status`` is intentionally excluded because /status is already a dedicated
# gateway/session command; only bare ``status`` expands to the decision-grade
# status prompt.
COMMAND_SHORTCUT_NAMES = tuple(
    shortcut.name for shortcut in _SHORTCUTS if shortcut.name != "status"
)
COMMAND_SHORTCUTS = tuple(
    shortcut for shortcut in _SHORTCUTS if shortcut.name in COMMAND_SHORTCUT_NAMES
)


def build_shortcut_prompt(name: str, args: str = "") -> str | None:
    shortcut = SHORTCUTS_BY_NAME.get((name or "").strip().lower())
    if shortcut is None:
        return None
    prompt = shortcut.prompt_template
    extra = (args or "").strip()
    if extra:
        prompt += f"\n\nContext: {extra}"
    return prompt


def expand_plain_shortcut(text: str) -> tuple[str, str] | None:
    stripped = (text or "").strip()
    if not stripped or stripped.startswith("/"):
        return None

    keyword, separator, remainder = stripped.partition(" ")
    shortcut = SHORTCUTS_BY_NAME.get(keyword.rstrip(":").lower())
    if shortcut is None or not shortcut.plain_trigger:
        return None

    # Avoid hijacking ordinary sentences like "review the PR" or
    # "memory is full". Bare keywords expand directly; keyword-plus-context
    # requires an explicit colon sentinel, e.g. "bootstrap: polymarket-bot".
    extra = ""
    if keyword.endswith(":"):
        extra = (separator + remainder).strip()
    elif remainder:
        # Natural-language invocations like "handover command" should still
        # behave as the bare shortcut, while ordinary sentences such as
        # "review the PR" remain untouched.
        if remainder.strip().lower() != "command":
            return None

    prompt = build_shortcut_prompt(shortcut.name, extra)
    if prompt is None:
        return None
    return shortcut.name, prompt
