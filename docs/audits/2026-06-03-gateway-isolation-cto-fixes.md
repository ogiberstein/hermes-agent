# Gateway isolation CTO-audit fixes — 2026-06-03

## Findings fixed

- Preserved requested working directories through isolated `systemd-run` launches with `--working-directory=<cwd>` coverage for foreground terminal, PTY/background, and LSP paths.
- Recovered systemd-isolated background jobs from transient-unit state rather than the short-lived `systemd-run` client PID, using `systemctl show <unit>` with `ActiveState`, `SubState`, and `MainPID`.
- Reduced environment temp-file leakage: transient-service env files are trap-cleaned by the service shell, stale Hermes env files are swept defensively, and failed launch paths clean created env files with finally-style handling.
- Made transient-unit cleanup best-effort for missing/exited units so natural process-exit races do not become false user-visible failures.
- Kept PTY tests independent of host-only optional modules by stubbing the PTY process implementation in tests.

## Evidence

- Targeted tests: `python -m pytest tests/tools/test_process_registry_systemd_isolation.py tests/agent/lsp/test_systemd_isolation.py -q -o 'addopts='`.
- Sanitization scan: added tests use fake/redacted values only and do not include real secrets, platform IDs, private runtime config, or machine-specific private paths.
- Optional smoke: when `systemd-run` is available on a real systemd host, run a gateway-context terminal command from a non-gateway shell with fake env and verify the transient unit/cgroup manually before claiming live proof.

## Residual risks

- Unit-state recovery remains best-effort when `systemctl` is unavailable, times out, or systemd has already collected the transient unit.
- Compatibility tests prove command construction and recovery logic; live cgroup truth still requires a real deployment smoke test observing `hermes-terminal-*`, `hermes-terminal-pty-*`, or `hermes-lsp-*` outside `hermes-gateway.service`.
