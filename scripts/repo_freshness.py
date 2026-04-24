#!/usr/bin/env python3
"""Minimal repo freshness preflight for status/review jobs.

Fail closed: local repo docs are canonical only when the checkout is synced with
its upstream and the working tree is clean.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


GIT_TIMEOUT = 30


def _run_git(repo_path: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
        check=check,
    )


def _git_output(repo_path: Path, args: list[str]) -> str:
    return _run_git(repo_path, args).stdout.strip()


def analyze_repo(repo_path: str | Path, *, fetch_remote: bool = True) -> dict[str, Any]:
    repo = Path(repo_path).expanduser().resolve()
    report: dict[str, Any] = {
        "repo_path": str(repo),
        "exists": repo.exists(),
        "is_git_repo": False,
        "branch": None,
        "upstream": None,
        "local_head": None,
        "remote_head": None,
        "ahead": None,
        "behind": None,
        "is_clean": None,
        "freshness_state": "missing",
        "safe_to_trust_docs": False,
        "reason": None,
    }

    if not repo.exists():
        report["reason"] = "repo path does not exist"
        return report

    try:
        report["is_git_repo"] = _git_output(repo, ["rev-parse", "--is-inside-work-tree"]) == "true"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        report["reason"] = f"git repo check failed: {exc}"
        return report

    if not report["is_git_repo"]:
        report["reason"] = "not a git repo"
        return report

    try:
        branch = _git_output(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
        local_head = _git_output(repo, ["rev-parse", "HEAD"])
        status_porcelain = _git_output(repo, ["status", "--porcelain"])
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        report["freshness_state"] = "unknown"
        report["reason"] = f"git status check failed: {exc}"
        return report

    report["branch"] = branch
    report["local_head"] = local_head
    report["is_clean"] = status_porcelain == ""

    if branch == "HEAD":
        report["freshness_state"] = "detached"
        report["reason"] = "detached HEAD"
        return report

    try:
        upstream = _git_output(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        report["freshness_state"] = "no_upstream"
        report["reason"] = "no upstream configured"
        return report

    report["upstream"] = upstream

    if fetch_remote:
        try:
            _run_git(repo, ["fetch", "--quiet", "--prune"], check=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            report["freshness_state"] = "fetch_failed"
            report["reason"] = f"git fetch failed: {exc}"
            return report

    try:
        remote_head = _git_output(repo, ["rev-parse", upstream])
        ahead_behind = _git_output(repo, ["rev-list", "--left-right", "--count", f"HEAD...{upstream}"])
        ahead, behind = (int(part) for part in ahead_behind.split())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as exc:
        report["freshness_state"] = "unknown"
        report["reason"] = f"upstream comparison failed: {exc}"
        return report

    report["remote_head"] = remote_head
    report["ahead"] = ahead
    report["behind"] = behind

    if ahead == 0 and behind == 0 and report["is_clean"]:
        report["freshness_state"] = "fresh"
        report["safe_to_trust_docs"] = True
        report["reason"] = "synced with upstream and clean"
        return report

    if ahead > 0 and behind > 0:
        report["freshness_state"] = "diverged"
        report["reason"] = f"diverged from upstream (ahead {ahead}, behind {behind})"
        return report

    if behind > 0:
        report["freshness_state"] = "stale"
        report["reason"] = f"behind upstream by {behind} commit(s)"
        return report

    if ahead > 0:
        report["freshness_state"] = "ahead"
        report["reason"] = f"ahead of upstream by {ahead} commit(s)"
        return report

    if not report["is_clean"]:
        report["freshness_state"] = "dirty"
        report["reason"] = "working tree has local modifications"
        return report

    report["freshness_state"] = "unknown"
    report["reason"] = "repo freshness could not be classified"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether a local git checkout is safe to trust as canonical repo truth.")
    parser.add_argument("repo_path", help="Path to the local git repository")
    parser.add_argument("--no-fetch", action="store_true", help="Skip git fetch before comparing with upstream")
    args = parser.parse_args()

    report = analyze_repo(args.repo_path, fetch_remote=not args.no_fetch)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("safe_to_trust_docs") else 2


if __name__ == "__main__":
    raise SystemExit(main())