# Proposal

## Why

The backtest (archived `drift-prediction-backtest`) confirmed the trap is real and frequent: about 32% of purposeful visits to LinkedIn, X and Instagram turn into off-task feed time. But nothing interrupts it yet. The user wants to be caught in the moment, with a Mac notification and sound that names the task they meant to be doing, plus a way to tell the system when a nudge was wrong so it improves. The live context sources now exist (herdr events and the Sunsama timer via MCP), and the backtest's predictor signal is real but weak. So the live engine should decide with transparent rules now, run BehaviorGPT alongside in shadow mode, and turn real feedback into better rules and better evaluation data.

## What Changes

- **A live engine** (`behaviornudge nudge run`) that watches the front app and active browser URL every ~2 s, builds sessions with the existing activity keys, labels and intent resolver, and evaluates nudge rules.
- **Nudge moments**, decided in code:
  - **Arrival:** landing on a drift-prone site for a purpose (reply, lookup, follow a link) while the intent is something else.
  - **Purpose done:** a purposeful visit turns into consuming (e.g. messages, then the feed).
  - **Off-task:** consuming, off-task time exceeds a threshold while an intent exists, even without a purposeful entry.
  - **Trigger moments:** consuming starts while in a call (losing presence in a meeting), or after a herdr agent starts working (waiting turns into doom scrolling).
- **Nudge content:** each notification names the task at hand, from the Sunsama timer, Sunsama timebox or calendar block, and offers feedback buttons.
- **A native macOS helper** (`BehaviorNudgeHelper.app`, Swift):
  - shows notifications with action buttons and sounds, and reports clicks back to the engine;
  - detects calls by checking which call apps are using the microphone (the technique from jhammant/maccap, MIT, credited), with **no audio recording**.
  - It talks to the engine over JSON lines and appears as "behaviornudge" in System Settings → Notifications.
- **Controls:** global on/off, a sound per nudge type (or none), snooze, quiet hours, a daily nudge budget and per-moment cooldowns.
- **Feedback loop:** buttons such as *Back to task*, *Too soon*, *Legit task* and *Snooze*.
  - "Too soon" lengthens that moment's wait in that context.
  - "Legit task" becomes a user label (on-task for that activity and intent) that suppresses future nudges there and corrects future labels.
  - Every nudge and its outcome (clicked, ignored, what happened over the next 15 minutes) is logged for replay through the evaluation report.
- **Shadow predictions:** the configured predictor (BehaviorGPT by default, baseline fallback) scores each nudge moment. The score is logged, not acted on.
- **Out of scope:**
  - adding items to ReadingQueue, Raindrop or Readwise (phase 2);
  - changes to jhammant Retrace or its menu bar;
  - letting the predictor gate nudges;
  - distributing a notarized helper build (local self-signed build only).

## Capabilities

### New Capabilities
- `live-activity-tracking`: real-time view of the current activity (front app, browser URL, terminal project, call state) turned into live sessions with labels and intent, using the existing timeline, labeling and intent rules.
- `nudge-rules`: when a nudge fires (arrival, purpose done, off-task, in-call, agent-working), with thresholds, cooldowns, the daily budget, quiet hours, snooze and the global switch.
- `nudge-delivery`: the native helper's notifications: content (task at hand), action buttons, per-type sounds, click reporting, and the engine–helper protocol.
- `call-detection`: detecting that the user is in a call from microphone use by call apps, without recording, plus the call-app allowlist.
- `nudge-feedback`: capturing feedback actions, applying them (wait times, user labels, suppression), and logging nudge outcomes for evaluation.

### Modified Capabilities
- `intent-context`: the "Log live context going forward" requirement extends to call start/end events from call detection, so the context log records meetings as they happen (not only herdr and the Sunsama timer).
- `activity-labeling`: the "Apply labeling layers in order" requirement changes so the user's own labels (including "Legit task" feedback) take precedence over rules. Today rules come first, which would let a rule keep calling a legitimate activity off-task after the user corrected it.

## Impact

- **New code:**
  - Python: engine, live tracker, rules, feedback store.
  - A Swift helper app in this repo (built locally with the Xcode toolchain, self-signed).
- **New permissions on the Mac:**
  - Notifications for "behaviornudge", granted once.
  - Automation access to read the browser's active tab URL (already granted to the terminal).
- **Reuses:** activity keys, labeler and review store (`activity-labeling`), intent resolver and live context log (`intent-context`), drift-prone site definitions (`drift-detection`), and the predictor contract (`drift-prediction-evaluation`).
- **Network:** shadow predictions call BehaviorGPT through the existing egress gate and audit log, with the same allowlisted fields. Notifications and call detection are fully local.
- **Data:** a nudge and feedback log in `data/` (git-ignored); the feedback log becomes new evaluation ground truth.
