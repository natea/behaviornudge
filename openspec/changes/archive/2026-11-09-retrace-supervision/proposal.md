# Proposal

## Why

behaviornudge's history and evaluation depend on jhammant Retrace capturing continuously, but nothing keeps Retrace running. Its server is started by hand (`retrace menubar`). On 2026-11-08 it died at 15:57 with no log and nothing restarted it. A restart from a herdr pane then ran for hours "healthy" while every capture failed on a Screen Recording permission it didn't inherit. About five hours of screenshots were lost before anyone noticed. Retrace also doesn't start again after a reboot or logout.

## What Changes

- **A LaunchAgent for Retrace:** `behaviornudge retrace agent install` writes and loads a per-user LaunchAgent that starts Retrace's server at login and restarts it if it exits. Output goes to `~/.retrace/serve.log`. `uninstall` and `status` round it out.
- **Safe takeover:** if Retrace is already running outside launchd, install stops it first only when asked (`--replace`), so two servers never fight over port 8765.
- **Permission check after install:** install waits for a real new capture, not just a healthy API. If capture fails on Screen Recording, it says exactly which program needs the permission (the Python that launchd runs).
- **Staleness warning:** `behaviornudge nudge status` (and `retrace agent status`) warn when Retrace hasn't saved a capture for more than 10 minutes while the Mac is in use, and show the last error.
- Out of scope: changing Retrace's code, starting the Retrace menu bar app at login (it's only a status view), and Windows.

## Capabilities

### New Capabilities
- `retrace-supervision`: keeping jhammant Retrace running (start at login, restart on exit, logs), installing and removing that supervision safely, and detecting and reporting when capture has stopped or is failing.

### Modified Capabilities
(none)

## Impact

- **New code:** a `retrace` CLI group in behaviornudge, a small supervision module, and a staleness check used by `nudge status`.
- **New file outside the repo:** `~/Library/LaunchAgents/com.behaviornudge.retrace.plist` (user scope; removed by `uninstall`).
- **One-time permission grants:** launchd-started processes don't inherit the terminal's permissions, so Screen Recording and Accessibility must be granted once to Retrace's Python interpreter.
- **Config:** new `sources.retrace_repo` (path to the Retrace checkout) and `sources.retrace_api` (default `http://127.0.0.1:8765`).
