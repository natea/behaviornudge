# Tasks

Ordering note: group 2 proves the riskiest piece (a native notification with buttons and a reported click) before the engine is built on it. Groups 3–9 build the engine; group 10 runs it live.

## 1. Configuration and stores

- [x] 1.1 Add a `[nudge]` section to `behaviornudge.example.toml`: enabled; delays (arrival 20 s, purpose-done 30 s, off-task 8 min, in-call 30 s, agent 2 min); cooldown 15 min; daily budget 8; quiet hours; snooze 30 min; sounds per type (arrival Tink, purpose-done Glass, off-task Sosumi, agent Pop, in-call none); the call allowlist and 45 s end grace; too-soon factor 1.5 and cap 4.0. Add matching defaults in the loader; verify with a config test that defaults load and a user override takes effect
- [x] 1.2 Create the nudge store (`data/nudges.sqlite`: `nudges`, `adjustments`, `suppressions`, `state`) with a small Python API; verify with unit tests for insert, update-outcome, and state round-trips

## 2. Native helper app (Swift)

- [x] 2.1 Add `helper/` Swift sources with a JSON-lines stdin/stdout loop (`ping` → `pong`, `ready` on start) and a `behaviornudge helper build` command that compiles with `swiftc`, assembles `data/helper/BehaviorNudgeHelper.app` (`Info.plist`: `com.behaviornudge.helper`, `LSUIElement`), signs it ad hoc, and rebuilds only when the source hash changes; verify that the build succeeds, `codesign -dv` shows the bundle id, and a ping round-trips
- [x] 2.2 Implement notifications: request authorization (report `authorized` in `ready`), register the category with the four actions (back_to_task, too_soon, legit, snooze), handle `notify` with title, body and per-type sound (system sound name, bundled file, or none) and `withdraw`, and emit `action` and `dismissed` events; verify with `behaviornudge nudge test off-task`, which shows a notification with buttons, plays Sosumi, and prints the clicked action
- [x] 2.3 Implement call detection (CoreAudio per-process `IsRunningInput` matched against the configured allowlist by bundle-id prefix, checked every 2 s, credited to jhammant/maccap in a source comment and the README) and emit `calls` on change, with no audio opened; verify on a real Zoom or Meet call that `calls` reports the app, and that dictation or a non-allowlisted app does not
- [ ] 2.4 Emit `front_app` on NSWorkspace activation and `idle` (seconds idle, screen locked) every 10 s; verify by switching apps and locking the screen while watching the helper's output

## 3. Python helper client

- [x] 3.1 Implement `HelperClient`: spawn the helper binary, send commands, dispatch events to callbacks, restart on exit (backoff, logged), and expose `authorized`; verify with tests against a fake helper script that echoes events and is killed mid-test (the client restarts it within 10 s)

## 4. Labeling precedence (modified `activity-labeling`)

- [x] 4.1 Make user labels (review answers and Legit-task feedback, source `user`) take precedence over rules in `label_all` and the live labeler; rule → cache → Jev otherwise unchanged; verify with the "User label overrides a rule" scenario test and by re-running the existing labeling tests

## 5. Calls in the context log (modified `intent-context`)

- [x] 5.1 Track call state in the engine from helper `calls` events with the 45 s end grace; write `call_start` (app, meeting name from the overlapping allowlisted calendar event, or the app name) and `call_end` entries to the context log; verify with tests for brief mutes (no split), naming from the calendar, and the log entries

## 6. Live activity tracking

- [x] 6.1 Implement the browser URL poller (AppleScript for Chrome, Safari, Arc, Brave; 2 s while a browser is frontmost, 5 s on battery; Firefox falls back to the app key) and turn front-app and URL changes into `Record`s; verify with tests using a fake osascript runner, and live by switching tabs (key updates within 5 s)
- [x] 6.2 Build incremental live sessions with the existing key and merge rules, terminal project from the herdr focus index, and labels from user labels → rules → cache, queuing unknown activities for background Jev labeling through the egress gate when enabled; verify that a live session on `x.com/home` gets the same labels as `label_all`, and that a new site is tracked immediately with no mode
- [x] 6.3 Resolve live intent from the TimerPoller (capped), today's Sunsama timeboxes (via `sunsama_mcp`, refreshed every 5 min), calendar spans (`retrace-calendar 1`, allowlist, every 5 min), and recent creative live sessions; verify the "Timer started mid-session" and "Calendar block in progress" scenarios with fakes
- [x] 6.4 Pause accumulation while idle over 5 min or locked; verify with a test that off-task time doesn't grow during a locked period

## 7. Nudge rules

- [x] 7.1 Implement the per-stretch state machine for arrival, purpose-done, off-task, in-call and agent-working, with effective delays = default × adjustment multiplier and precedence in-call > purpose-done > arrival > agent > off-task; verify with tests for every nudge-rules scenario (including "Site is the task", "No intent" and "Call with no calendar event")
- [x] 7.2 Implement the gates (global switch → quiet hours → snooze → legit suppression → cooldown → budget), logging the first failing gate as the suppression reason; verify with tests for budget used, snooze and quiet hours
- [x] 7.3 Submit shadow predictions at every moment (2-thread pool, 3 s timeout, baseline fallback, egress gate), storing the score asynchronously; verify that a timing-out fake predictor never delays a nudge and that the error is logged

## 8. Delivery content

- [x] 8.1 Build notification content (title per type; body naming the task at hand and its source: timer, timebox, calendar, call, agent project) and pick the configured sound per type (in-call silent by default); verify with unit tests for each type and the "Timer task named" scenario
- [x] 8.2 Report denied notification permission in the log and `nudge status`, with guidance to enable Alerts style for always-visible buttons; verify by testing the status output with `authorized=false`

## 9. Feedback

- [x] 9.1 Apply actions: too_soon (×1.5, cap 4.0 for type + site + intent source), back_to_task (10% toward 1.0), legit (suppression plus a user on-task label in `LabelStore`), snooze (30 min), dismissed and ignored; verify with tests for each spec scenario (the 8 → 12 minute example, legit suppressing future nudges, snooze expiry)
- [x] 9.2 Record outcomes 5 and 15 minutes after each fired nudge (returned to a ≥60 s on-task or creative session, kept consuming, other work); verify with a simulated timeline test
- [x] 9.3 Add `nudge feedback list` and `nudge feedback reset <id>` (undo adjustments and legit suppressions, removing the user label); verify with the "Undo a legit-task suppression" scenario
- [x] 9.4 Add `nudge stats`: per type and overall fired, actions, return-to-work within 5 min, and shadow ROC-AUC against outcomes; verify on a seeded nudge store with hand-computed expected numbers

## 7b. Feed-landing arrival (added by user request)

- [x] 7b.1 Fire the arrival nudge 45 s (`feed_arrival_delay_s`, adjustable by Too soon) after landing directly on a drift-prone site's consuming page while a known, unaligned intent exists; consuming time on the same site adds up across feed/post hops; content says which feed; verify with the "Landing straight on the feed" and "Feed hopping" scenario tests

## 10. Engine and live run

- [x] 10.1 Implement `behaviornudge nudge run` wiring the helper client, URL poller, herdr subscriber, TimerPoller, calendar refresher, tracker, rules, delivery, feedback and shadow predictor in one process (also writing the context log), plus `nudge status`, `nudge on` / `nudge off` and `nudge snooze N`; verify that `nudge run` starts cleanly, `status` shows helper authorized, the current intent and budget, and Ctrl-C exits cleanly
- [ ] 10.2 Live check: with a Sunsama timer running, reply on LinkedIn messaging and then open the feed (expect arrival, then purpose-done); click Too soon and confirm the adjusted wait in `nudge feedback list`; join a short call and browse (expect a silent in-call nudge); confirm every outbound request in `data/audit.jsonl` has only allowed fields
- [x] 10.3 Document `nudge run`, controls, sounds, feedback and stats in the README (crediting jhammant/maccap), and verify the documented commands run as written

## Workflow follow-up

- Live with the defaults for a week, review `nudge stats`, then decide about auto-start at login (design open question).
- Phase 2 change: "save for later" actions (ReadingQueue, Raindrop, Readwise).
- Archive the change once implementation and review are done.
