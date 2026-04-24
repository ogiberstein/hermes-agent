from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.repo_freshness import analyze_repo


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return result.stdout.strip()


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")


def _commit_file(repo: Path, name: str, content: str, message: str) -> str:
    (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _make_remote_pair(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True, timeout=30)
    subprocess.run(["git", "clone", str(remote), str(work)], check=True, capture_output=True, text=True, timeout=30)
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test User")
    _commit_file(work, "README.md", "hello\n", "initial")
    _git(work, "push", "-u", "origin", "master")
    return remote, work


def test_analyze_repo_reports_fresh_for_synced_clean_repo(tmp_path: Path):
    _remote, work = _make_remote_pair(tmp_path)

    report = analyze_repo(work)

    assert report["freshness_state"] == "fresh"
    assert report["safe_to_trust_docs"] is True
    assert report["is_clean"] is True
    assert report["ahead"] == 0
    assert report["behind"] == 0


def test_analyze_repo_reports_dirty_for_local_modifications(tmp_path: Path):
    _remote, work = _make_remote_pair(tmp_path)
    (work / "README.md").write_text("dirty\n", encoding="utf-8")

    report = analyze_repo(work)

    assert report["freshness_state"] == "dirty"
    assert report["safe_to_trust_docs"] is False
    assert report["is_clean"] is False


def test_analyze_repo_reports_stale_when_behind_upstream(tmp_path: Path):
    remote, work = _make_remote_pair(tmp_path)
    upstream_clone = tmp_path / "upstream"
    subprocess.run(["git", "clone", str(remote), str(upstream_clone)], check=True, capture_output=True, text=True, timeout=30)
    _git(upstream_clone, "config", "user.email", "test@example.com")
    _git(upstream_clone, "config", "user.name", "Test User")
    _commit_file(upstream_clone, "CHANGELOG.md", "new\n", "remote update")
    _git(upstream_clone, "push", "origin", "master")

    report = analyze_repo(work)

    assert report["freshness_state"] == "stale"
    assert report["safe_to_trust_docs"] is False
    assert report["behind"] == 1