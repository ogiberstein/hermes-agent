import asyncio
import types

import pytest

from agent.lsp.client import LSPClient


@pytest.mark.asyncio
async def test_lsp_spawn_uses_systemd_transient_service_in_gateway(monkeypatch, tmp_path):
    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = list(args)
        captured["kwargs"] = kwargs

        class EmptyStream:
            async def readline(self):
                return b""

            async def readuntil(self, sep):
                raise asyncio.IncompleteReadError(b"", None)

        class Proc:
            returncode = 0
            stdin = object()
            stdout = EmptyStream()
            stderr = EmptyStream()

        return Proc()

    monkeypatch.setenv("HERMES_GATEWAY_SESSION", "1")
    monkeypatch.setenv("HERMES_LSP_MEMORY_MAX", "650M")
    monkeypatch.setenv("HERMES_LSP_MEMORY_SWAP_MAX", "0")
    monkeypatch.setattr("tools.environments.local.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("tools.environments.local.os.path.exists", lambda path: True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    client = LSPClient(
        server_id="typescript",
        workspace_root=str(tmp_path),
        command=["typescript-language-server", "--stdio"],
    )
    await client._spawn()

    assert captured["args"][:5] == [
        "/usr/bin/systemd-run",
        "--quiet",
        "--pipe",
        "--wait",
        "--collect",
    ]
    assert any(str(part).startswith("--unit=hermes-lsp-") for part in captured["args"])
    assert "MemoryMax=650M" in captured["args"]
    assert "MemorySwapMax=0" in captured["args"]
    assert f"--working-directory={tmp_path}" in captured["args"]
    assert "typescript-language-server" in captured["args"]


def test_pty_background_process_uses_systemd_pty_in_gateway(monkeypatch, tmp_path):
    from tools.process_registry import ProcessRegistry

    captured = {}

    class FakePty:
        pid = 1234

        @classmethod
        def spawn(cls, args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return cls()

        def read(self, size=4096):
            return b""

        def terminate(self, force=False):
            pass

    monkeypatch.setenv("HERMES_GATEWAY_SESSION", "1")
    monkeypatch.setattr("tools.environments.local.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("tools.environments.local.os.path.exists", lambda path: True)
    monkeypatch.setitem(__import__("sys").modules, "ptyprocess", types.SimpleNamespace(PtyProcess=FakePty))
    monkeypatch.setattr("tools.process_registry.ProcessRegistry._write_checkpoint", lambda self: None)

    registry = ProcessRegistry()
    session = registry.spawn_local("claude", cwd=str(tmp_path), use_pty=True)

    assert captured["args"][:5] == [
        "/usr/bin/systemd-run",
        "--quiet",
        "--pty",
        "--wait",
        "--collect",
    ]
    assert session.systemd_unit.startswith("hermes-terminal-pty-")
    assert session.pid_scope == "systemd-service"
    assert f"--working-directory={tmp_path}" in captured["args"]
    assert "claude" in " ".join(captured["args"])



@pytest.mark.asyncio
async def test_failed_lsp_systemd_spawn_cleans_env_temp_file(monkeypatch, tmp_path):
    import tempfile
    from pathlib import Path

    created_files = []
    real_mkstemp = tempfile.mkstemp

    def tracking_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        created_files.append(path)
        return fd, path

    async def fake_create_subprocess_exec(*args, **kwargs):
        raise OSError("fake lsp launch failure")

    monkeypatch.setenv("HERMES_GATEWAY_SESSION", "1")
    monkeypatch.setattr("tools.environments.local.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("tools.environments.local.os.path.exists", lambda path: True)
    monkeypatch.setattr("tools.environments.local.tempfile.mkstemp", tracking_mkstemp)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    client = LSPClient(
        server_id="typescript",
        workspace_root=str(tmp_path),
        command=["typescript-language-server", "--stdio"],
        env={"FAKE_TOKEN": "fake-redacted"},
    )
    with pytest.raises(OSError):
        await client._spawn()

    assert created_files
    assert all(not Path(path).exists() for path in created_files)
