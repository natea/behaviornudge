# Tasks

## 1. Browser in front

- [x] 1.1 Tab script also returns the frontmost window title (System Events); `read_tab` drops the URL when the tab title isn't in it; verify with tests (match, mismatch, no window title, Safari)
- [x] 1.2 Engine logs and shows one notice per hour on a foreign read; helper supports `notice` (no buttons); verify with a test

## 2. Engine agent

- [x] 2.1 `engine_agent.py`: plist, install (refuse/`--replace`), verify heartbeat, uninstall, status; verify with fake-system tests
- [x] 2.2 CLI `nudge agent install|uninstall|status`; `nudge status` shows the agent line; README

## 3. Sunsama sign-in

- [x] 3.1 File lock around each Sunsama call, tokens loaded after the lock; verify with a test that two concurrent callers refresh once
- [x] 3.2 Engine shows one login notice per hour on `LoginRequired`; verify with a test

## 4. Live check

- [x] 4.1 Install the agent, confirm the engine runs under launchd, Chrome tabs read correctly, and the full suite passes
