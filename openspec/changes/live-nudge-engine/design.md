# Design

## Context

See proposal.md (Why). Facts from the codebase and exploration that shape the approach:

- **Reusable pieces already exist** (archived `drift-prediction-backtest`):
  - activity keys (`keys.py`), session merging (`timeline.py`), the layered labeler with a label store and review queue (`labels.py`, `pipeline.py`, `jev.py`);
  - intent resolution with forgotten-timer capping (`intent.py`);
  - the herdr subscriber and Sunsama timer poller (`context.py`, `sunsama_mcp.py`);
  - the predictor contract and registry (`predictors/`);
  - the egress gate and audit log (`egress.py`).
- **Retrace's watcher fires on app switches only.** Tab changes inside a browser wait for its 45 s fallback capture, too slow for "you just landed". Reading the active tab URL with AppleScript works on this Mac (the terminal already has Automation permission).
- **macOS only lets app bundles request notification permission and show action buttons** (`UNUserNotificationCenter`). A bare command-line binary can't. `osascript display notification` has no buttons.
- **Call detection** in jhammant/maccap (MIT) reads CoreAudio's per-process `kAudioProcessPropertyIsRunningInput` (macOS 14.2+) and matches bundle ids against a call-app allowlist. No recording or audio permission is needed. This Mac has Swift 6.4 and Xcode.
- **`retrace-calendar 1` includes events in progress now** (start ≤ now < end), so live intent can use it. Calendar names are allowlisted (natejaune only).
- **Backtest result:** BehaviorGPT ROC-AUC 0.565 vs baseline 0.513, mean latency ~450 ms. Real but weak, so it is not used to gate nudges.

## Goals / Non-Goals

**Goals:**
- A nudge appears within ~5 s of the moment a rule fires.
- Every decision is explainable from the log: rule, thresholds, feedback adjustments, suppression reason.
- One long-running process (`behaviornudge nudge run`) plus one helper. No new services, nothing in the cloud except the existing allowlisted predictor calls.
- Feedback changes behavior immediately and is reversible.

**Non-Goals:**
- Read-later integrations (phase 2).
- Changing Retrace.
- A settings GUI: configuration stays in `behaviornudge.toml` plus System Settings for notifications.
- A notarized, distributable helper.
- Windows or Linux.

## Decisions

### D1. Process layout: Python engine plus one Swift helper app
```
 behaviornudge nudge run (Python, one process)
 |-- HelperClient ---- stdin/stdout JSON lines ----> BehaviorNudgeHelper.app (Swift, LSUIElement)
 |                                                    |- notifications + actions (UNUserNotificationCenter)
 |                                                    |- front-app changes (NSWorkspace)
 |                                                    |- calls (CoreAudio mic-in-use, MacCap technique)
 |                                                    '- idle / screen-lock state
 |-- BrowserURL poller (osascript, 2 s, only while a browser is frontmost)
 |-- herdr subscriber + Sunsama TimerPoller (existing context.py; also writes context log)
 |-- CalendarRefresher (retrace-calendar 1, every 5 min, allowlist)
 |-- LiveTracker -> RuleEngine -> Nudger -> FeedbackStore
 '-- ShadowPredictor (thread pool; never blocks rules)
```
- *Why one helper:* notifications must come from an app bundle anyway, so the same bundle also hosts the other macOS-native pieces (front app, calls, idle, lock). This avoids polling `System Events` from Python and keeps platform glue in one place.
- *Alternative:* a Swift CLI for calls plus `alerter` for notifications. Rejected in exploration: no per-app notification settings, clicks are lost when the `alerter` process exits, and no custom sounds.

### D2. Helper protocol (newline-delimited JSON)
- **Engine → helper:**
  - `{"cmd":"notify","id","type","title","body","sound","actions":[{"id","title"}]}`
  - `{"cmd":"withdraw","id"}`
  - `{"cmd":"config","call_allowlist":[...],"call_end_grace_s":45}`
  - `{"cmd":"ping"}`
- **Helper → engine:**
  - `{"event":"ready","authorized":bool}`
  - `{"event":"action","id","action"}`
  - `{"event":"dismissed","id"}`
  - `{"event":"front_app","bundle_id","name"}`
  - `{"event":"calls","active":[{"bundle_id","name"}]}` on change
  - `{"event":"idle","idle_s","locked"}` every 10 s
  - `{"event":"error","detail"}`
- The engine restarts the helper if it exits, waiting up to 10 s between attempts. Notification categories register the four actions: `back_to_task`, `too_soon`, `legit`, `snooze`. Buttons are always visible in "Alerts" style and on hover in "Banners" style; `nudge status` tells the user how to switch styles.

### D3. Helper build and packaging
- Swift sources live in `helper/`.
- `behaviornudge helper build` compiles with `swiftc`, assembles `data/helper/BehaviorNudgeHelper.app` (`Info.plist` with `CFBundleIdentifier` = `com.behaviornudge.helper`, `LSUIElement` = true, bundled sounds in `Resources/`), and signs it ad hoc (`codesign -s -`).
- The engine launches `…/Contents/MacOS/BehaviorNudgeHelper` directly, so stdin/stdout stay connected; the bundle identity still applies.
- It rebuilds when sources change, keyed by a source hash, as Retrace does.
- The first notification triggers macOS's permission prompt.

### D4. Live tracking reuses the backtest pipeline
- **Records:** front-app events plus browser URL polls become `Record`s.
- **Keys and sessions:** `keys.activity_key()` and incremental session merging (same 30 s gap / 3 s transition rules).
- **Terminal project:** from the herdr focus index when Ghostty is frontmost.
- **Labels:** user labels → rules → label cache → (optional) background Jev request through the egress gate.
- **Intent:** `IntentResolver` over (live timer span from TimerPoller, capped; today's Sunsama timeboxes via `sunsama_mcp`, refreshed with the calendar; calendar spans; recent creative live sessions).
- **Pauses:** idle over 5 min or screen locked pauses accumulation.

### D5. Rules as a small state machine per stretch
```
 idle/work --(purposeful visit to drift-prone site)--> VISIT
 VISIT --(>= arrival_delay, not aligned)--> fire ARRIVAL (once per visit)
 any  --(direct landing on a drift-prone feed, consuming >= feed_arrival_delay, intent known)--> fire ARRIVAL
 VISIT --(consuming off-task/no-intent >= purpose_done_delay within 15 min)--> fire PURPOSE_DONE
 any  --(consuming off-task stretch >= off_task_threshold, intent known)--> fire OFF_TASK
 any  --(call active and consuming >= in_call_delay)--> fire IN_CALL
 any  --(agent working >= 30 s and consuming >= agent_delay)--> fire AGENT_WORKING
 work session >= 60 s --> stretch ends (reset)
```
- Defaults: arrival 20 s, feed landing 45 s (user decision: 8 min was too long to flag landing on a feed), purpose-done 30 s, off-task 8 min, in-call 30 s, agent 2 min, cooldown 15 min per (type, site), budget 8 per day. All live in config.
- Effective delay = default × feedback multiplier for (type, site, intent source) (D7).
- **Gate order:** global switch → quiet hours → snooze → legit suppression → cooldown → budget. The first failing gate is logged as the suppression reason.
- **Precedence when several moments qualify at once:** in-call > purpose-done > arrival > agent-working > off-task. Only one nudge fires per tick.

### D6. Nudge content
- **Title:** what happened ("Back from LinkedIn?", "8 min on X", "In your call").
- **Body:** "Task at hand: *Client tests* (Sunsama timer)" / "(calendar: Deep Work — Client Projects)" / "(agent working in behaviornudge)".
- **Sounds per type, configurable:**
  - arrival: Tink
  - purpose-done: Glass
  - off-task: Sosumi
  - agent-working: Pop
  - in-call: none (so call participants don't hear it)
- **Text through the egress gate?** No. Notifications are local, so titles aren't trimmed for privacy, but they're shortened for display.

### D7. Feedback store (`data/nudges.sqlite`)
- **Tables:**
  - `nudges` (id, time, type, key, intent, source, fired/suppressed + reason, shadow score, action, outcome fields)
  - `adjustments` (type, site, intent_source → multiplier)
  - `suppressions` (activity key, intent ref → legit)
  - `state` (snooze until, enabled)
- **Too soon:** multiplier ×1.5, capped at 4.0. **Back to task:** multiplier moves 10% toward 1.0.
- **Legit:** writes a suppression and a user label in the existing `LabelStore` (sources = user, alignment on-task) under the intent-aware cache key, so backtests pick it up (see the modified `activity-labeling` requirement).
- **Outcome:** a follow-up check 5 and 15 minutes after each fired nudge records whether a ≥60 s on-task or creative session started.

### D8. Labeling precedence change
`label_all` and the live labeler check user labels (review answers and feedback) **before** rules. The cache lookup moves ahead of the rule table only for entries whose source is `user`. Rule → cache → Jev order is unchanged otherwise.

### D9. Shadow predictions
- At each nudge moment (fired or suppressed), submit `predict(history, now, intent, current_key)` to a 2-thread pool with a 3 s timeout. Store the score when it returns.
- Uses the existing BehaviorGPT adapter and egress gate. On failure or timeout, record the error and fall back to the baseline score.
- `nudge stats` computes ROC-AUC of the shadow scores against nudge outcomes, which is the live ground truth the backtest lacked.

### D10. CLI
- `behaviornudge nudge run` (foreground; Ctrl-C to stop)
- `nudge status` (enabled, snooze, budget used, helper and permission state, current intent)
- `nudge on` / `nudge off`, `nudge snooze 30`
- `nudge feedback list` / `nudge feedback reset <id>`
- `nudge stats`
- `helper build`
- `nudge test <type>` shows a sample notification

## Risks / Trade-offs

- **Nudge fatigue erodes trust fast** → conservative defaults, cooldowns, a daily budget, Too soon feedback, and silent in-call nudges. Review `nudge stats` after week 1.
- **AppleScript polling every 2 s costs CPU and battery** → only while a browser is frontmost; back off to 5 s on battery.
- **The browser in the allowlist makes any mic-using tab count as a call** (e.g. voice notes in a web app) → 45 s end grace, and the user can trim the allowlist; the meeting name comes from the calendar when available.
- **Ad-hoc signed helper**: macOS may re-prompt for notification permission after rebuilds that change the signature → rebuild only on source changes; document it.
- **Banners hide action buttons until hover** → `nudge status` recommends Alerts style; clicking the body still counts as acknowledgement.
- **Weak shadow-predictor signal** → shadow only; it can't cause harm.
- **Live labels lag for new sites** (background Jev) → rules only nudge when a mode exists; consuming sites in the rule table cover the main traps from the start.

## Migration Plan

Additive, nothing to migrate.
- The context logger's behavior is unchanged, apart from adding call events.
- The labeling precedence change only affects sessions that have user labels. Re-running `label` and `report` after feedback reflects it.
- Rollback: stop `nudge run` and remove `data/helper/`.

## Open Questions

- Whether to auto-start `nudge run` at login (LaunchAgent). Deferred until the defaults have been lived with for a week; doesn't affect the specs.
