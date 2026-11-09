# Design

## Context

See proposal.md (Why). Facts from investigating Retrace (`~/Documents/code/retrace`):

- **How it runs today:** `retrace menubar` starts `python -m retrace.cli serve` as a detached process (`start_new_session=True`, output to `/dev/null`) only if the API isn't up. The menu bar never restarts it, and nothing starts it at login.
- **Health check:** `GET /api/health` returns `ok`, `problems`, `daemon`, `enabled` and `last_capture_at`. `~/.retrace/status.json` holds `last_capture_at`, `last_error` and presence (idle, screen locked).
- **The health check can mislead:** it said `ok: true` while every capture failed on a permission error, so health alone can't confirm capture.
- **Permissions follow whatever launched the process.** A process started from Ghostty inherits Ghostty's Screen Recording grant; from a herdr pane, herdr's (none). A process started by **launchd** is attributed to its own executable: here the uv-managed Python behind `~/Documents/code/retrace/.venv/bin/python`, currently `~/.local/share/uv/python/cpython-3.12.12-macos-aarch64-none/bin/python3.12`.

## Goals / Non-Goals

**Goals:** Retrace survives crashes and reboots; problems are visible within minutes; install and uninstall are reversible and don't fight a running server.

**Non-Goals:** modifying Retrace, auto-starting its menu bar, Windows, system-wide (root) daemons.

## Decisions

### D1. Per-user LaunchAgent with KeepAlive
- **Plist:** `~/Library/LaunchAgents/com.behaviornudge.retrace.plist`.
- **ProgramArguments:** the Retrace venv's Python with `-m retrace.cli serve`; `WorkingDirectory` is the Retrace repo.
- **Lifecycle:** `RunAtLoad` true; `KeepAlive` true (restart whenever it exits); `ThrottleInterval` 30 s, so a crash loop doesn't spin.
- **Logs:** `StandardOutPath` and `StandardErrorPath` both `~/.retrace/serve.log`.
- **Environment:** `EnvironmentVariables` gives a PATH with Homebrew and the user's local bin, so Retrace's helpers and `swiftc` resolve.
- **Loading:** with `launchctl bootstrap gui/<uid>`. Removal is `launchctl bootout`.
- *Why the venv Python directly, not `uv run`:* the grant must attach to a stable executable. `uv run` adds a parent process and re-resolves the environment at every start. The venv's Python is what `uv run` ends up running anyway.
- *Alternative:* a wrapper `.app` so the grant shows as "Retrace". Not now: it's more moving parts, and the Python grant works.

### D2. Takeover of a running server
- Install detects a listening API on the configured port that isn't the agent's own job (`launchctl print gui/<uid>/com.behaviornudge.retrace` absent).
- Without `--replace` it stops and explains. With it, it stops the running `retrace serve` processes (SIGTERM, then a wait), then bootstraps the agent.

### D3. Capture verification
- After bootstrap, poll `status.json` (and `/api/health`) for up to 120 s, and trigger one capture via `POST /capture/tick?force=true`.
- **Success** = `last_capture_at` newer than the install time.
- **Permission failure** = `last_error` contains "TCC" or "declined". Report the resolved interpreter path and the Settings location, and leave the agent installed so it picks up the grant on its next restart (`launchctl kickstart -k`, offered in the message).

### D4. Staleness check
- A function reads `status.json` and `/api/health` and returns `ok`, `quiet`, `paused`, `stale`, `failing` or `down`.
- **Why not just "no capture for 10 minutes":** Retrace only advances `last_capture_at` when it stores a frame. Duplicate frames (reading a static page) and gated ones (denylisted app, idle, locked) don't advance it, so that rule would warn during long reads. Every ledger write does update `updated_at`, which `/api/health` exposes as `ledger_age_s`, so it serves as the capture loop's heartbeat.
- **States:**
  - **down:** the API doesn't answer.
  - **failing:** `last_error_at` is newer than `last_capture_at`.
  - **stale:** the heartbeat is older than 5 minutes.
  - **paused:** capture is disabled or snoozed.
  - **quiet:** no capture for 10+ minutes while the heartbeat is fresh. Informational only.
  - **ok:** otherwise.
- It's used by `nudge status` (a warning line for down/failing/stale) and `retrace agent status`.

### D5. Config
- `sources.retrace_repo` (default `~/Documents/code/retrace`).
- `sources.retrace_api` (default `http://127.0.0.1:8765`).

## Risks / Trade-offs

- **Granting Screen Recording to a general-purpose Python** is broader than granting it to an app: any script run with that exact interpreter would also be allowed to capture the screen → the grant is per binary path (the uv-managed 3.12.12). Documented; a wrapper app can narrow it later.
- **A uv Python upgrade changes the binary path,** so the grant no longer applies → install verifies capture, and status warns, so it shows up immediately; re-grant to the new path.
- **The menu bar may start a second server if run while the agent restarts** → it only starts one if the API is down; the agent's 30 s throttle keeps the window small.

## Migration Plan

Run `behaviornudge retrace agent install --replace` once. Rollback: `behaviornudge retrace agent uninstall`, then start Retrace by hand as before.
