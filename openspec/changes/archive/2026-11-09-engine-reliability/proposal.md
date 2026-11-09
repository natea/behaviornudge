## Why

On 2026-11-09 a whole day of LinkedIn scrolling went un-nudged. Three separate weak points:

- An automation copy of Google Chrome (started by a Playwright session) was open. AppleScript's "Google Chrome" resolved to that copy, so every tab read returned the same local page and the engine never saw linkedin.com.
- The engine only runs while a terminal window keeps `behaviornudge nudge run` alive; it does not survive a closed window, a crash or a reboot.
- The Sunsama sign-in broke overnight. Access tokens last 10 minutes; the timer poller and the timebox refresher renew them independently, and a renewal that overlaps another one, or whose response is lost, can leave only a spent refresh token on disk.

## What Changes

- Tab reads are checked against the title of the window that is really in front. A mismatch means the answer came from another copy of the browser: its URL is ignored and the user is told once, in a plain notification, what to close.
- `behaviornudge nudge agent install|uninstall|status` supervises the engine with a per-user LaunchAgent, like `retrace agent`: it starts at login, restarts on exit and logs to `data/engine.log`. `nudge status` shows whether the agent is loaded.
- Sunsama token renewal is serialized across threads and processes with a file lock, and tokens are re-read after taking the lock, so only one renewal uses a refresh token. When a sign-in is genuinely needed, the engine says so once in a notification instead of only in its log.

## Capabilities

### New Capabilities
- `engine-supervision`: running the live nudge engine under launchd.

### Modified Capabilities
- `live-activity-tracking`: tab reads must come from the browser window in front.
- `intent-context`: the Sunsama sign-in survives concurrent renewals; an expired sign-in is surfaced.

## Impact

- New `src/behaviornudge/engine_agent.py`; `cli.py` (`nudge agent`), `nudge/browser.py`, `nudge/engine.py`, `sunsama_mcp.py`, helper Swift (plain notices without nudge buttons).
- Under launchd the engine runs as the same uv Python that Retrace already uses, so Screen Recording and Accessibility are already granted; macOS asks once for permission to read Chrome's tabs (Automation).
