# Tasks

## 1. Supervision module

- [x] 1.1 Add `sources.retrace_repo` and `sources.retrace_api` to the example config and loader defaults; verify with a config test
- [x] 1.2 Implement plist generation (venv Python + `-m retrace.cli serve`, WorkingDirectory, RunAtLoad, KeepAlive, ThrottleInterval 30, logs to `~/.retrace/serve.log`, PATH) and the resolved-interpreter lookup; verify with a unit test that the plist round-trips through plistlib with the expected keys
- [x] 1.3 Implement the staleness check (`ok` / `stale` / `failing` / `down` from `status.json` + `/api/health`, presence-aware); verify with unit tests for each state including the 70-minute permission-error scenario

## 2. CLI

- [x] 2.1 `behaviornudge retrace agent install [--replace]`: refuse if a non-agent server is listening (unless `--replace`, which stops it), write the plist, `launchctl bootstrap`, trigger a capture and wait up to 120 s for a capture newer than install time; on a permission error report the interpreter path and Settings location; verify with unit tests using a fake launchctl and fake health source
- [x] 2.2 `behaviornudge retrace agent uninstall` and `status`; verify that uninstall removes the plist and that status prints the staleness state
- [x] 2.3 Add the staleness warning to `behaviornudge nudge status`; verify the warning line appears for a stale fixture and not for a healthy one

## 3. Install for real

- [x] 3.1 Run `install --replace` on this Mac; grant Screen Recording/Accessibility to the reported interpreter if asked; verify a new capture lands and `retrace agent status` says capturing
- [x] 3.2 Kill the supervised server and verify launchd restarts it within a minute
- [x] 3.3 Document the agent in the README (install, uninstall, the one-time permission grant)
