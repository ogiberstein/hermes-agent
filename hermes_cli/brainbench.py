"""BrainBench-lite: opt-in capture and replay for Hermes recall/search queries.

This intentionally starts small: capture the deterministic session FTS search
surface, redact obvious PII/secrets, export NDJSON, and replay candidates against
the current session DB to detect retrieval drift.
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from hermes_constants import get_hermes_home

SCHEMA_VERSION = 1
CAPTURE_ENV = "HERMES_BRAINBENCH_CAPTURE"
DIR_ENV = "HERMES_BRAINBENCH_DIR"
DEFAULT_EXCLUDE_SOURCES = ["tool"]

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_BEARER_RE = re.compile(r"\bBearer\s+([A-Za-z0-9._~+/=-]{8,})\b")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
# Conservative phone match: requires separators or leading + to avoid redacting
# ordinary short numbers in search queries.
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s.-])?(?:\(?\d{2,4}\)?[\s.-]){2,}\d{2,4}(?!\w)")
_API_KEY_RE = re.compile(
    r"\b((?:sk|pk|rk|ghp|github_pat|xox[baprs])-?[A-Za-z0-9_\-]{12,})\b",
    re.IGNORECASE,
)


def brainbench_dir() -> Path:
    override = os.getenv(DIR_ENV)
    if override:
        return Path(override).expanduser()
    return get_hermes_home() / "brainbench"


def candidates_path(path: Optional[Path | str] = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    return brainbench_dir() / "eval_candidates.ndjson"


def is_capture_enabled() -> bool:
    raw = os.getenv(CAPTURE_ENV, "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def scrub_pii(text: str) -> str:
    """Redact obvious PII/secrets from captured query text."""
    if not text:
        return ""
    redacted = _EMAIL_RE.sub("[REDACTED_EMAIL]", str(text))
    redacted = _JWT_RE.sub("[REDACTED_TOKEN]", redacted)
    redacted = _BEARER_RE.sub("Bearer [REDACTED_TOKEN]", redacted)
    redacted = _API_KEY_RE.sub("[REDACTED_TOKEN]", redacted)
    redacted = _PHONE_RE.sub("[REDACTED_PHONE]", redacted)
    return redacted


def _now_ms() -> int:
    return int(time.time() * 1000)


def _normalize_ids(ids: Iterable[Any], limit: int = 50) -> List[str]:
    out: List[str] = []
    for item in ids or []:
        if item is None:
            continue
        value = str(item)
        if value not in out:
            out.append(value)
        if len(out) >= limit:
            break
    return out


def _ensure_private_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass


def _append_private_jsonl(path: Path, row: Dict[str, Any]) -> None:
    _ensure_private_parent(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _open_private_for_write(path: Path):
    _ensure_private_parent(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return os.fdopen(fd, "w", encoding="utf-8")


def capture_search_event(
    tool_name: str,
    query: str,
    retrieved_ids: Iterable[Any],
    latency_ms: int | float,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    path: Optional[Path | str] = None,
) -> bool:
    """Append one captured retrieval candidate when capture is explicitly enabled."""
    if not is_capture_enabled():
        return False

    out_path = candidates_path(path)
    safe_metadata = dict(metadata or {})
    if tool_name == "session_search" and "exclude_sources" not in safe_metadata:
        safe_metadata["exclude_sources"] = list(DEFAULT_EXCLUDE_SOURCES)
    row = {
        "schema_version": SCHEMA_VERSION,
        "captured_at_ms": _now_ms(),
        "tool_name": tool_name,
        "query": scrub_pii(query or ""),
        "retrieved_ids": _normalize_ids(retrieved_ids),
        "latency_ms": int(latency_ms),
        "metadata": safe_metadata,
    }
    _append_private_jsonl(out_path, row)
    return True


def iter_candidates(path: Optional[Path | str] = None) -> Iterator[Dict[str, Any]]:
    in_path = candidates_path(path)
    if not in_path.exists():
        return
    with in_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("schema_version") == SCHEMA_VERSION:
                yield row


def export_candidates(
    *,
    input_path: Optional[Path | str] = None,
    output_path: Optional[Path | str] = None,
    limit: Optional[int] = None,
) -> int:
    """Write captured candidates to stdout or a file. Returns rows written."""
    rows = iter_candidates(input_path)
    count = 0
    close = False
    if output_path and str(output_path) != "-":
        out = _open_private_for_write(Path(output_path).expanduser())
        close = True
    else:
        import sys

        out = sys.stdout
    try:
        for row in rows:
            if limit is not None and count >= limit:
                break
            out.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    finally:
        if close:
            out.close()
    return count


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    left = set(a)
    right = set(b)
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _search_session_ids(
    db: Any,
    query: str,
    *,
    role_filter: Optional[List[str]] = None,
    exclude_sources: Optional[List[str]] = None,
    limit: int = 50,
) -> tuple[List[str], int]:
    start = time.perf_counter()
    kwargs = {"query": query, "limit": limit, "offset": 0}
    if role_filter:
        kwargs["role_filter"] = role_filter
    kwargs["exclude_sources"] = exclude_sources if exclude_sources is not None else list(DEFAULT_EXCLUDE_SOURCES)
    rows = db.search_messages(**kwargs)
    latency_ms = int((time.perf_counter() - start) * 1000)
    return _normalize_ids((row.get("session_id") for row in rows), limit=limit), latency_ms


def replay_candidates(
    *,
    db: Any,
    input_path: Optional[Path | str] = None,
    limit: Optional[int] = None,
    top_regressions: int = 5,
) -> Dict[str, Any]:
    """Replay captured session_search rows against the current DB."""
    rows = list(iter_candidates(input_path))
    if limit is not None:
        rows = rows[: max(0, int(limit))]

    results: List[Dict[str, Any]] = []
    errored = 0
    skipped = 0
    for row in rows:
        if row.get("tool_name") != "session_search":
            skipped += 1
            continue
        query = row.get("query") or ""
        captured_ids = _normalize_ids(row.get("retrieved_ids") or [])
        try:
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            role_filter_raw = metadata.get("role_filter")
            role_filter = None
            if isinstance(role_filter_raw, str) and role_filter_raw.strip():
                role_filter = [part.strip() for part in role_filter_raw.split(",") if part.strip()]
            exclude_sources = metadata.get("exclude_sources")
            if not isinstance(exclude_sources, list):
                exclude_sources = list(DEFAULT_EXCLUDE_SOURCES)
            current_ids, current_latency = _search_session_ids(
                db,
                query,
                role_filter=role_filter,
                exclude_sources=exclude_sources,
            )
        except Exception as exc:  # pragma: no cover - defensive summary path
            errored += 1
            results.append({
                "query": query,
                "error": str(exc),
                "captured_ids": captured_ids,
                "current_ids": [],
                "jaccard": 0.0,
                "top1_stable": False,
                "latency_delta_ms": None,
            })
            continue
        captured_latency = int(row.get("latency_ms") or 0)
        latency_delta = current_latency - captured_latency
        result = {
            "query": query,
            "captured_ids": captured_ids,
            "current_ids": current_ids,
            "jaccard": _jaccard(captured_ids, current_ids),
            "top1_stable": bool(captured_ids and current_ids and captured_ids[0] == current_ids[0]) or (not captured_ids and not current_ids),
            "latency_delta_ms": latency_delta,
            "captured_latency_ms": captured_latency,
            "current_latency_ms": current_latency,
        }
        results.append(result)

    replayed = [r for r in results if "error" not in r]
    mean_jaccard = sum(r["jaccard"] for r in replayed) / len(replayed) if replayed else 0.0
    top1_stability = sum(1 for r in replayed if r["top1_stable"]) / len(replayed) if replayed else 0.0
    latency_deltas = [r["latency_delta_ms"] for r in replayed if r.get("latency_delta_ms") is not None]
    mean_latency_delta = sum(latency_deltas) / len(latency_deltas) if latency_deltas else 0.0
    rows_over_2x_latency = sum(
        1
        for r in replayed
        if r.get("captured_latency_ms", 0) > 0
        and r.get("current_latency_ms", 0) > 2 * r.get("captured_latency_ms", 0)
    )
    regressions = sorted(replayed, key=lambda r: (r["jaccard"], r["top1_stable"]))[:top_regressions]

    return {
        "schema_version": SCHEMA_VERSION,
        "summary": {
            "rows_total": len(rows),
            "rows_replayed": len(replayed),
            "rows_skipped": skipped,
            "rows_errored": errored,
            "mean_jaccard": mean_jaccard,
            "top1_stability_rate": top1_stability,
            "mean_latency_delta_ms": mean_latency_delta,
            "rows_over_2x_latency": rows_over_2x_latency,
        },
        "top_regressions": regressions,
        "results": results,
    }


def print_replay_report(report: Dict[str, Any], *, json_output: bool = False) -> None:
    if json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return
    summary = report["summary"]
    print(f"Replayed {summary['rows_replayed']} of {summary['rows_total']} captured queries ({summary['rows_skipped']} skipped, {summary['rows_errored']} errored)")
    print(f"Mean Jaccard@k:    {summary['mean_jaccard']:.3f}")
    print(f"Top-1 stability:   {summary['top1_stability_rate']:.1%}")
    print(f"Mean latency Δ:    {summary['mean_latency_delta_ms']:+.0f}ms")
    if report.get("top_regressions"):
        print("\nTop regressions:")
        for r in report["top_regressions"]:
            print(f"  jaccard={r['jaccard']:.2f} captured={len(r['captured_ids'])} current={len(r['current_ids'])} {r['query']!r}")
