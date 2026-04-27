"""Smoke and import-contract tests for repo-local operational scripts."""

from __future__ import annotations

import ast
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
LOCAL_IMPORT_ROOTS = {
    "agent",
    "cli",
    "cron",
    "environments",
    "gateway",
    "hermes_cli",
    "hermes_constants",
    "model_tools",
    "run_agent",
    "tools",
    "toolsets",
    "tui_gateway",
}


def test_memory_audit_script_runs_with_isolated_memory_home(tmp_path):
    home = tmp_path / "home"
    mem_dir = home / ".hermes" / "memories"
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").write_text("VPS baseline: test fixture", encoding="utf-8")
    (mem_dir / "USER.md").write_text(
        "Top priorities: test\n"
        "Project one-liners: test-project=test\n"
        "Cross-project risks/conflicts: none",
        encoding="utf-8",
    )
    (mem_dir / "MEMORY.test-project.md").write_text(
        "Latest discussed: script smoke test\n"
        "Next step: keep audit JSON valid",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env.setdefault("HERMES_HOME", str(home / ".hermes"))

    result = subprocess.run(
        [sys.executable, "scripts/memory_audit.py"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["global"]["findings"] == []
    assert report["user"]["findings"] == []
    assert report["projects"] == [
        {
            "file": "MEMORY.test-project.md",
            "project_key": "test-project",
            "chars": len("Latest discussed: script smoke test\nNext step: keep audit JSON valid"),
            "entries": 1,
            "findings": [],
        }
    ]


def test_scripts_local_from_import_contracts(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))

    local_roots = LOCAL_IMPORT_ROOTS | {path.stem for path in SCRIPTS_DIR.glob("*.py")}
    failures: list[str] = []

    for script in sorted(SCRIPTS_DIR.glob("*.py")):
        tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 0 or not node.module:
                continue
            if node.module.split(".")[0] not in local_roots:
                continue
            imported_names = [alias.name for alias in node.names if alias.name != "*"]
            try:
                module = importlib.import_module(node.module)
            except Exception as exc:  # noqa: BLE001 - report all import-contract failures
                failures.append(f"{script.relative_to(REPO_ROOT)}:{node.lineno} imports {node.module}: {exc!r}")
                continue
            missing = [name for name in imported_names if not hasattr(module, name)]
            if missing:
                failures.append(
                    f"{script.relative_to(REPO_ROOT)}:{node.lineno} imports missing "
                    f"{node.module}.{', '.join(missing)}"
                )

    assert failures == []
