#!/usr/bin/env python3
"""Audit Hermes local memory shards for schema drift, contamination, and bloat."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.memory_tool import (
    ENTRY_DELIMITER,
    _COS_INDEX_REQUIRED_FIELDS,
    _SNAPSHOT_LEGACY_FIELDS,
    _SNAPSHOT_OPTIONAL_FIELDS,
    _SNAPSHOT_REQUIRED_FIELDS,
    _extract_labeled_fields,
    _is_cos_index,
    _is_snapshot_like,
    _known_project_keys,
)

MEM_DIR = Path.home() / ".hermes" / "memories"
USER_FILE = MEM_DIR / "USER.md"
GLOBAL_FILE = MEM_DIR / "MEMORY.md"
WARN_MEMORY_CHARS = 1800


def read_entries(path: Path) -> list[str]:
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    return [e.strip() for e in raw.split(ENTRY_DELIMITER) if e.strip()]


def audit_project_file(path: Path, project_key: str, all_keys: set[str]) -> dict:
    entries = read_entries(path)
    findings: list[str] = []
    snapshot_count = 0
    for entry in entries:
        fields = _extract_labeled_fields(entry)
        if _is_snapshot_like(fields):
            snapshot_count += 1
            legacy = [f for f in _SNAPSHOT_LEGACY_FIELDS if f in fields]
            if legacy:
                findings.append(f"legacy snapshot fields present: {', '.join(legacy)}")
            missing = [f for f in _SNAPSHOT_REQUIRED_FIELDS if not fields.get(f)]
            if missing:
                findings.append(f"snapshot missing required fields: {', '.join(missing)}")
            extras = [
                f for f in fields
                if f not in set(_SNAPSHOT_REQUIRED_FIELDS) | set(_SNAPSHOT_OPTIONAL_FIELDS)
            ]
            if extras:
                findings.append(f"snapshot has unexpected fields: {', '.join(extras)}")
        normalized_entry = re.sub(r"[^a-z0-9_-]+", " ", entry.lower())
        conflicts = [k for k in sorted(all_keys) if k != project_key and re.search(rf"\b{re.escape(k)}\b", normalized_entry)]
        if conflicts:
            findings.append(f"mentions other project keys: {', '.join(conflicts)}")
    if snapshot_count == 0:
        findings.append("missing rolling snapshot")
    elif snapshot_count > 1:
        findings.append(f"multiple rolling snapshots found: {snapshot_count}")
    return {
        "file": path.name,
        "project_key": project_key,
        "chars": path.stat().st_size if path.exists() else 0,
        "entries": len(entries),
        "findings": findings,
    }


def audit_user_file(path: Path) -> dict:
    entries = read_entries(path)
    findings: list[str] = []
    cos_entries = [e for e in entries if _is_cos_index(e, _extract_labeled_fields(e))]
    if not cos_entries:
        findings.append("CoS index missing")
    elif len(cos_entries) > 1:
        findings.append(f"multiple CoS index entries found: {len(cos_entries)}")
    else:
        fields = _extract_labeled_fields(cos_entries[0])
        missing = [f for f in _COS_INDEX_REQUIRED_FIELDS if not fields.get(f)]
        if missing:
            findings.append(f"CoS index missing required fields: {', '.join(missing)}")
    return {
        "file": path.name,
        "chars": path.stat().st_size if path.exists() else 0,
        "entries": len(entries),
        "findings": findings,
    }


def audit_global_file(path: Path) -> dict:
    entries = read_entries(path)
    findings: list[str] = []
    chars = path.stat().st_size if path.exists() else 0
    if chars > WARN_MEMORY_CHARS:
        findings.append(f"global memory high-water mark exceeded: {chars} chars")
    return {
        "file": path.name,
        "chars": chars,
        "entries": len(entries),
        "findings": findings,
    }


def main() -> None:
    project_keys = _known_project_keys(MEM_DIR)
    project_reports = []
    for key in sorted(project_keys):
        project_reports.append(audit_project_file(MEM_DIR / f"MEMORY.{key}.md", key, project_keys))

    report = {
        "memory_dir": str(MEM_DIR),
        "global": audit_global_file(GLOBAL_FILE),
        "user": audit_user_file(USER_FILE),
        "projects": project_reports,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
