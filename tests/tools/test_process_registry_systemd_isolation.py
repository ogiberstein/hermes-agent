import subprocess

from gateway.session_context import clear_session_vars, set_session_vars
from tools.environments.local import (
    _cleanup_stale_systemd_env_files,
    _cleanup_systemd_env_files_from_command,
    _gateway_systemd_isolation_enabled,
    _gateway_terminal_timeout_seconds,
    _systemd_run_command,
)
from tools.process_registry import ProcessRegistry, ProcessSession


class _FakePopen:
    def __init__(self, args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.pid = 12345
        self.returncode = None
        self.stdout = None
        self.stdin = None


def test_gateway_background_local_process_uses_transient_systemd_service(monkeypatch, tmp_path):
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakePopen(args, **kwargs)

    monkeypatch.setenv("HERMES_GATEWAY_SESSION", "1")
    monkeypatch.setenv("HERMES_TERMINAL_MEMORY_MAX", "600M")
    monkeypatch.setenv("HERMES_TERMINAL_MEMORY_SWAP_MAX", "0")
    monkeypatch.setattr("tools.environments.local.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("tools.environments.local.os.path.exists", lambda path: True)
    monkeypatch.setattr("tools.process_registry.subprocess.Popen", fake_popen)
    monkeypatch.setattr("tools.process_registry.ProcessRegistry._write_checkpoint", lambda self: None)
    monkeypatch.setattr("tools.process_registry.ProcessRegistry._reader_loop", lambda self, session: None)

    registry = ProcessRegistry()
    session = registry.spawn_local("sleep 60", cwd=str(tmp_path))

    assert captured["args"][:5] == [
        "/usr/bin/systemd-run",
        "--quiet",
        "--pipe",
        "--wait",
        "--collect",
    ]
    assert f"--unit={session.systemd_unit}" in captured["args"]
    assert "-p" in captured["args"]
    assert "MemoryMax=600M" in captured["args"]
    assert "MemorySwapMax=0" in captured["args"]
    assert session.systemd_unit.startswith("hermes-terminal-")
    assert session.pid_scope == "systemd-service"
    assert f"--working-directory={tmp_path}" in captured["args"]


def test_gateway_systemd_isolation_uses_session_context_without_env_gate(monkeypatch):
    """Slack/Telegram gateway tool calls must isolate even when systemd lacks HERMES_GATEWAY_SESSION."""
    monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
    monkeypatch.setenv("HERMES_TERMINAL_SYSTEMD_ISOLATION", "1")
    monkeypatch.setattr("tools.environments.local.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("tools.environments.local.os.path.exists", lambda path: True)

    tokens = set_session_vars(platform="slack", chat_id="C123")
    try:
        assert _gateway_systemd_isolation_enabled() is True
    finally:
        clear_session_vars(tokens)


def test_gateway_systemd_isolation_disabled_for_plain_cli_without_context(monkeypatch):
    monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
    monkeypatch.setenv("HERMES_TERMINAL_SYSTEMD_ISOLATION", "1")
    monkeypatch.setattr("tools.environments.local.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("tools.environments.local.os.path.exists", lambda path: True)

    assert _gateway_systemd_isolation_enabled() is False


def test_gateway_terminal_timeout_is_capped_for_transient_service(monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_TERMINAL_TIMEOUT_MAX", "180")
    assert _gateway_terminal_timeout_seconds(600) == 180
    assert _gateway_terminal_timeout_seconds(120) == 120

    cmd = _systemd_run_command(
        ["/bin/bash", "-c", "npm install hyperframes@0.5.5"],
        {},
        timeout_seconds=600,
        unit_name="hermes-terminal-test",
    )

    assert "/usr/bin/timeout" in cmd or "timeout" in cmd
    assert "180s" in cmd


def test_systemd_run_command_does_not_expose_env_values_in_process_args():
    cmd = _systemd_run_command(
        ["/bin/bash", "-c", "echo ok"],
        {"SECRET_TOKEN": "dummy-value-for-test", "PATH": "/usr/bin"},
        timeout_seconds=30,
        unit_name="hermes-terminal-test",
    )

    rendered = "\n".join(cmd)
    assert "dummy-value-for-test" not in rendered
    assert "--setenv=SECRET_TOKEN" not in rendered
    assert any(part.startswith("/tmp/hermes-terminal-env-") for part in cmd)


def test_kill_systemd_isolated_background_process_kills_unit_before_client(monkeypatch):
    calls = []

    class FakeProcess:
        pid = 54321
        returncode = None

        def kill(self):
            calls.append(("process.kill",))

    def fake_run(args, **kwargs):
        calls.append(("run", tuple(args)))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("tools.process_registry.subprocess.run", fake_run)
    monkeypatch.setattr("tools.process_registry.os.killpg", lambda pgid, sig: calls.append(("killpg", pgid, sig)))
    monkeypatch.setattr("tools.process_registry.os.getpgid", lambda pid: 999)
    monkeypatch.setattr("tools.process_registry.ProcessRegistry._write_checkpoint", lambda self: None)

    registry = ProcessRegistry()
    session = ProcessSession(id="proc_test", command="sleep 60")
    session.process = FakeProcess()
    session.pid = 54321
    session.pid_scope = "systemd-service"
    session.systemd_unit = "hermes-terminal-testunit"
    registry._running[session.id] = session

    result = registry.kill_process(session.id)

    assert result["status"] == "killed"
    assert ("run", ("systemctl", "kill", "--kill-whom=all", "hermes-terminal-testunit")) in calls
    assert ("run", ("systemctl", "stop", "hermes-terminal-testunit")) in calls


def test_recover_systemd_isolated_process_uses_unit_not_client_pid(monkeypatch):
    import json
    import tools.process_registry as pr

    checkpoint = [{
        "session_id": "proc_recover",
        "command": "sleep 60",
        "pid": 99999,
        "pid_scope": "systemd-service",
        "systemd_unit": "hermes-terminal-recover",
        "cwd": "/tmp",
        "started_at": 123.0,
    }]

    class FakePath:
        def exists(self):
            return True
        def read_text(self, encoding="utf-8"):
            return json.dumps(checkpoint)

    monkeypatch.setattr(pr, "CHECKPOINT_PATH", FakePath())
    monkeypatch.setattr(pr.ProcessRegistry, "_is_host_pid_alive", staticmethod(lambda pid: False))
    monkeypatch.setattr(pr.ProcessRegistry, "_systemd_unit_state", staticmethod(lambda unit: (unit == "hermes-terminal-recover", 4242)))
    monkeypatch.setattr(pr.ProcessRegistry, "_write_checkpoint", lambda self: None)

    registry = pr.ProcessRegistry()
    assert registry.recover_from_checkpoint() == 1
    session = registry.get("proc_recover")
    assert session is not None
    assert session.pid == 4242
    assert session.pid_scope == "systemd-service"
    assert session.systemd_unit == "hermes-terminal-recover"


def test_refresh_detached_systemd_session_finishes_when_unit_inactive(monkeypatch):
    from tools.process_registry import ProcessRegistry, ProcessSession

    monkeypatch.setattr(ProcessRegistry, "_systemd_unit_state", staticmethod(lambda unit: (False, None)))
    monkeypatch.setattr(ProcessRegistry, "_write_checkpoint", lambda self: None)

    registry = ProcessRegistry()
    session = ProcessSession(
        id="proc_done",
        command="sleep 60",
        pid=99999,
        pid_scope="systemd-service",
        systemd_unit="hermes-terminal-done",
        detached=True,
    )
    registry._running[session.id] = session

    refreshed = registry.get(session.id)
    assert refreshed.exited is True
    assert session.id in registry._finished


def test_terminate_systemd_unit_swallows_systemctl_timeout(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(tuple(args))
        raise subprocess.TimeoutExpired(args, 10)

    monkeypatch.setattr("tools.process_registry.subprocess.run", fake_run)

    from tools.process_registry import ProcessRegistry
    ProcessRegistry._terminate_systemd_unit("hermes-terminal-timeout")
    assert calls






def test_foreground_terminal_systemd_isolation_preserves_cwd(monkeypatch, tmp_path):
    from tools.environments.local import LocalEnvironment

    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakePopen(args, **kwargs)

    monkeypatch.setenv("HERMES_GATEWAY_SESSION", "1")
    monkeypatch.setattr("tools.environments.local.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("tools.environments.local.os.path.exists", lambda path: True)
    monkeypatch.setattr("tools.environments.local.subprocess.Popen", fake_popen)
    monkeypatch.setattr("tools.environments.local.LocalEnvironment.init_session", lambda self: None)

    env = LocalEnvironment(cwd=str(tmp_path))
    env._run_bash("pwd", timeout=30)

    assert f"--working-directory={tmp_path}" in captured["args"]
    assert captured["kwargs"]["cwd"] == str(tmp_path)


def test_failed_foreground_terminal_systemd_spawn_cleans_env_temp_file(monkeypatch, tmp_path):
    from pathlib import Path
    import tempfile
    from tools.environments.local import LocalEnvironment

    created_files = []
    real_mkstemp = tempfile.mkstemp

    def tracking_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        created_files.append(path)
        return fd, path

    def fake_popen(args, **kwargs):
        raise OSError("fake foreground launch failure")

    monkeypatch.setenv("HERMES_GATEWAY_SESSION", "1")
    monkeypatch.setattr("tools.environments.local.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("tools.environments.local.os.path.exists", lambda path: True)
    monkeypatch.setattr("tools.environments.local.tempfile.mkstemp", tracking_mkstemp)
    monkeypatch.setattr("tools.environments.local.subprocess.Popen", fake_popen)
    monkeypatch.setattr("tools.environments.local.LocalEnvironment.init_session", lambda self: None)

    env = LocalEnvironment(cwd=str(tmp_path), env={"FAKE_TOKEN": "fake-redacted"})
    try:
        env._run_bash("pwd", timeout=30)
    except OSError:
        pass
    else:
        raise AssertionError("_run_bash should propagate fake launch failure")

    assert created_files
    assert all(not Path(path).exists() for path in created_files)


def test_systemd_unit_state_uses_active_substate_and_main_pid(monkeypatch):
    def fake_run(args, **kwargs):
        assert args == [
            "systemctl", "show", "hermes-terminal-recover",
            "-p", "ActiveState", "-p", "SubState", "-p", "MainPID",
        ]
        return subprocess.CompletedProcess(
            args,
            0,
            "ActiveState=active\nSubState=running\nMainPID=4242\n",
            "",
        )

    monkeypatch.setattr("tools.process_registry.subprocess.run", fake_run)

    assert ProcessRegistry._systemd_unit_state("hermes-terminal-recover") == (True, 4242)


def test_systemd_unit_state_treats_exited_unit_as_inactive(monkeypatch):
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            0,
            "ActiveState=active\nSubState=exited\nMainPID=0\n",
            "",
        )

    monkeypatch.setattr("tools.process_registry.subprocess.run", fake_run)

    assert ProcessRegistry._systemd_unit_state("hermes-terminal-done") == (False, None)


def test_systemd_env_temp_file_cleanup_from_failed_launch_command():
    import tempfile
    from pathlib import Path

    fd, path = tempfile.mkstemp(prefix="hermes-terminal-env-", dir="/tmp", text=True)
    with open(fd, "w", encoding="utf-8") as f:
        f.write("export SECRET_TOKEN=fake-redacted-test-value\n")

    _cleanup_systemd_env_files_from_command(["systemd-run", path, "echo", "ok"])

    assert not Path(path).exists()


def test_stale_systemd_env_temp_file_cleanup_removes_only_old_owned_files():
    import os
    import tempfile
    import time
    from pathlib import Path

    fd_old, old_path = tempfile.mkstemp(prefix="hermes-terminal-env-", dir="/tmp", text=True)
    fd_new, new_path = tempfile.mkstemp(prefix="hermes-terminal-env-", dir="/tmp", text=True)
    os.close(fd_old)
    os.close(fd_new)
    old_time = time.time() - 7200
    os.utime(old_path, (old_time, old_time))

    try:
        _cleanup_stale_systemd_env_files(max_age_seconds=3600)
        assert not Path(old_path).exists()
        assert Path(new_path).exists()
    finally:
        Path(old_path).unlink(missing_ok=True)
        Path(new_path).unlink(missing_ok=True)


def test_failed_systemd_background_spawn_cleans_env_temp_file(monkeypatch, tmp_path):
    created_files = []
    real_mkstemp = __import__("tempfile").mkstemp

    def tracking_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        created_files.append(path)
        return fd, path

    def fake_popen(args, **kwargs):
        raise OSError("fake systemd-run launch failure")

    monkeypatch.setenv("HERMES_GATEWAY_SESSION", "1")
    monkeypatch.setattr("tools.environments.local.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("tools.environments.local.os.path.exists", lambda path: True)
    monkeypatch.setattr("tools.environments.local.tempfile.mkstemp", tracking_mkstemp)
    monkeypatch.setattr("tools.process_registry.subprocess.Popen", fake_popen)
    monkeypatch.setattr("tools.process_registry.ProcessRegistry._write_checkpoint", lambda self: None)

    registry = ProcessRegistry()
    try:
        registry.spawn_local("sleep 60", cwd=str(tmp_path), env_vars={"FAKE_TOKEN": "fake-redacted"})
    except OSError:
        pass
    else:
        raise AssertionError("spawn_local should propagate fake launch failure")

    assert created_files
    assert all(not __import__("pathlib").Path(path).exists() for path in created_files)


def test_missing_systemd_unit_kill_is_best_effort(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(tuple(args))
        return subprocess.CompletedProcess(args, 1, "", "Unit not loaded")

    monkeypatch.setattr("tools.process_registry.subprocess.run", fake_run)

    ProcessRegistry._terminate_systemd_unit("hermes-terminal-already-gone")

    assert calls == [
        ("systemctl", "kill", "--kill-whom=all", "hermes-terminal-already-gone"),
        ("systemctl", "stop", "hermes-terminal-already-gone"),
    ]
