## Context

The engine reads Chrome's tab through AppleScript (`tell application "Google Chrome"`), which addresses one running copy of the app; with two copies open it is not necessarily the one in front. The engine is started by hand in a terminal. Sunsama's MCP uses OAuth with 10-minute access tokens and a refresh token; each engine call builds a fresh OAuth provider that loads tokens from `data/sunsama_oauth.json`.

## Decisions

- **D1 Front-window check, not process targeting.** AppleScript can't address a copy of Chrome by pid, but System Events can read the front window title of the frontmost process. The tab script also returns that title; a tab title absent from it marks the read as foreign. Cost: one script per poll, as now. Considered: counting Chrome processes (says there is ambiguity but not which answer is right).
- **D2 Notices are plain notifications.** The helper gets a `notice` type with no category, so no nudge buttons; notices aren't stored as nudges and don't use the budget. Rate: one per kind per hour.
- **D3 Engine agent mirrors the Retrace agent.** Label `com.behaviornudge.engine`, `ProgramArguments` = the repo venv's `behaviornudge nudge run`, `WorkingDirectory` = repo (for `.env` and `behaviornudge.toml`), `KeepAlive`, `ThrottleInterval` 30, log `data/engine.log`. Verification = `data/nudge_status.json` updated after install. Shared launchctl plumbing is reused from `retrace_agent`.
- **D4 One lock around each Sunsama call.** `fcntl.flock` on `data/sunsama_oauth.lock` is held for the whole request, and the provider loads tokens only after the lock is taken, so a second caller sees the renewed token. Calls take ~1 s and run at most every 30 s, so serializing costs nothing noticeable.
- **D5 Login notice from the engine.** `LoginRequired` in either Sunsama path triggers one notice per hour; intent falls back to calendar as it already does.

## Risks / Trade-offs

- A lost renewal response (network timeout after Sunsama rotated the token) can still spend the refresh token; the notice makes that visible within minutes instead of a day.
- Chrome window titles can differ from tab titles in edge cases (very long titles truncated): the check compares a prefix of up to 40 characters, case-insensitively.
- Under launchd, macOS asks once whether the engine's Python may control Google Chrome; until allowed, tab reads fail (no URL), which `nudge status` already shows as an unknown browser tab.
