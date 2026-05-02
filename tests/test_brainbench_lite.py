import json
from pathlib import Path
from unittest.mock import MagicMock


def test_brainbench_capture_is_off_by_default(tmp_path, monkeypatch):
    from hermes_cli.brainbench import capture_search_event, candidates_path

    monkeypatch.delenv("HERMES_BRAINBENCH_CAPTURE", raising=False)
    monkeypatch.setenv("HERMES_BRAINBENCH_DIR", str(tmp_path))

    assert capture_search_event("session_search", "secret@example.com", ["s1"], 12) is False
    assert not candidates_path().exists()


def test_brainbench_capture_redacts_pii_and_appends_ndjson(tmp_path, monkeypatch):
    from hermes_cli.brainbench import capture_search_event, iter_candidates

    monkeypatch.setenv("HERMES_BRAINBENCH_CAPTURE", "1")
    monkeypatch.setenv("HERMES_BRAINBENCH_DIR", str(tmp_path))

    assert capture_search_event(
        "session_search",
        "email alice@example.com and bearer token Bearer sk-live-123456789",
        ["session-a", "session-b"],
        37,
        metadata={"role_filter": "user"},
    ) is True

    rows = list(iter_candidates())
    assert len(rows) == 1
    row = rows[0]
    assert row["schema_version"] == 1
    assert row["tool_name"] == "session_search"
    assert row["query"] == "email [REDACTED_EMAIL] and bearer token Bearer [REDACTED_TOKEN]"
    assert row["retrieved_ids"] == ["session-a", "session-b"]
    assert row["latency_ms"] == 37
    assert row["metadata"] == {"role_filter": "user", "exclude_sources": ["tool"]}


def test_brainbench_capture_files_are_private(tmp_path, monkeypatch):
    from hermes_cli.brainbench import capture_search_event, candidates_path

    monkeypatch.setenv("HERMES_BRAINBENCH_CAPTURE", "1")
    monkeypatch.setenv("HERMES_BRAINBENCH_DIR", str(tmp_path / "bench"))

    assert capture_search_event("session_search", "query", ["s1"], 1) is True

    data_path = candidates_path()
    assert oct(data_path.parent.stat().st_mode & 0o777) == "0o700"
    assert oct(data_path.stat().st_mode & 0o777) == "0o600"


def test_brainbench_replay_reports_stability_and_latency(tmp_path, monkeypatch):
    from hermes_cli.brainbench import capture_search_event, replay_candidates

    monkeypatch.setenv("HERMES_BRAINBENCH_CAPTURE", "1")
    monkeypatch.setenv("HERMES_BRAINBENCH_DIR", str(tmp_path))
    capture_search_event("session_search", "docker deploy", ["s1", "s2"], 10)

    db = MagicMock()
    db.search_messages.return_value = [
        {"session_id": "s1"},
        {"session_id": "s3"},
    ]

    report = replay_candidates(db=db)

    assert report["schema_version"] == 1
    assert report["summary"]["rows_total"] == 1
    assert report["summary"]["rows_replayed"] == 1
    assert report["summary"]["mean_jaccard"] == 1 / 3
    assert report["summary"]["top1_stability_rate"] == 1.0
    assert report["summary"]["rows_over_2x_latency"] in (0, 1)
    db.search_messages.assert_called_once_with(
        query="docker deploy",
        limit=50,
        offset=0,
        exclude_sources=["tool"],
    )
    assert report["results"][0]["captured_ids"] == ["s1", "s2"]
    assert report["results"][0]["current_ids"] == ["s1", "s3"]


def test_session_search_captures_raw_search_results_when_enabled(tmp_path, monkeypatch):
    from tools.session_search_tool import session_search
    from hermes_cli.brainbench import iter_candidates

    monkeypatch.setenv("HERMES_BRAINBENCH_CAPTURE", "1")
    monkeypatch.setenv("HERMES_BRAINBENCH_DIR", str(tmp_path))

    db = MagicMock()
    db.search_messages.return_value = [
        {"session_id": "s1", "content": "match", "source": "cli", "session_started": 1700000000, "model": "test"},
    ]
    db.get_session.return_value = {"parent_session_id": None}
    db.get_messages_as_conversation.return_value = [
        {"role": "user", "content": "match"},
    ]
    async def fake_summarize(*args, **kwargs):
        return None

    monkeypatch.setattr("tools.session_search_tool._summarize_session", fake_summarize)
    monkeypatch.setattr("model_tools._run_async", lambda coro: __import__("asyncio").run(coro))

    result = json.loads(session_search("email bob@example.com", db=db))
    assert result["success"] is True

    rows = list(iter_candidates())
    assert len(rows) == 1
    assert rows[0]["tool_name"] == "session_search"
    assert rows[0]["query"] == "email [REDACTED_EMAIL]"
    assert rows[0]["retrieved_ids"] == ["s1"]
