# behaviornudge

**Catch the drift before it starts.**

You leave real work to do something specific on LinkedIn or X, like replying to a message, looking someone up, or opening a link. Twenty-five minutes later you're still in the feed. A blocklist can't fix this, because half of your real tasks happen on those same sites.

behaviornudge reads the screen activity your Mac already records. It finds the moments when a purposeful visit turned into feed-scrolling and tests which model predicts those moments early enough to nudge you back. The long-term goal is a nudge that fires *before* the drift: at the trigger (a meeting just ended, Claude is working and you're waiting), on arrival ("you came to reply to Alex"), or once the reason for the visit is done ("reply sent. Back to your project?").

Built at [Sundai Hack 148 – Behavioral Models: Predict the next action](https://www.sundai.club/events/boston/agi-house-sf-mit-hack-energy), a [Sundai Club](https://www.sundai.club) hackathon at MIT on November 8, 2026. The event was centered on [Unbox AI's BehaviorGPT](https://github.com/Unbox-AI/behaviorgpt), a large behavioral model that predicts what someone will do next.

> **Status:** the offline backtest is complete. It builds a labeled timeline from local data, detects drift episodes, and compares BehaviorGPT with a baseline built from your own history. On real data, BehaviorGPT won 18 of 28 test weeks, but both signals are weak (ROC-AUC 0.57 vs 0.51). Live nudges are now available (see below). Browse every spec and change at **[natea.github.io/behaviornudge/specs](https://natea.github.io/behaviornudge/specs/)**, or see the archived backtest in [`openspec/changes/archive/2026-11-08-drift-prediction-backtest/`](openspec/changes/archive/2026-11-08-drift-prediction-backtest/) and the live engine in [`openspec/changes/archive/2026-11-08-live-nudge-engine/`](openspec/changes/archive/2026-11-08-live-nudge-engine/).

Project page: **[natea.github.io/behaviornudge](https://natea.github.io/behaviornudge/)**. Every setting explained: [configuration](https://natea.github.io/behaviornudge/configuration.html). Where the activity comes from: [capture sources](https://natea.github.io/behaviornudge/capture.html). How BehaviorGPT is used: [BehaviorGPT](https://natea.github.io/behaviornudge/behaviorgpt.html). Interactive architecture diagram: **[natea.github.io/behaviornudge/architecture.html](https://natea.github.io/behaviornudge/architecture.html)**.

## How it works

```
 sources (read-only)        normalize             understand              detect            predict
 -------------------        ---------             ----------              ------            -------
 retrace.to segments  --+   activity keys         intent: what you        drift episodes    baseline (your history)
 Retrace captures,      +-> (linkedin.com/feed)-> were supposed to do --> purposeful visit  BehaviorGPT
   Screen Time, history |   sessions + projects   labels: creative /      -> >=3 min off-   (swappable: EasyTPP,
 Apple Calendar         |                         reactive / consuming     task consuming    local LLM, Jev ...)
 Sunsama export       --+                         (rules -> cache ->      in 15 min                |
 herdr (live)                                      Jev -> you)                                     v
                                                                                       rolling weekly evaluation
                                                                                       -> report: which model goes live
```

1. **Timeline.** Read local activity sources strictly read-only, turn every record into a coarse *activity key* (`linkedin.com/feed` vs `linkedin.com/messaging`, `zed:my-repo`), resolve overlaps between sources, and merge records into sessions.
2. **Intent.** For every moment, work out what you were supposed to be doing, in this order: Sunsama timer (forgotten timers are capped), Sunsama timebox, calendar event, your most recent creative project, or no intent.
3. **Labels.** Tag each session with **mode** (creative / reactive / consuming), **purpose** (lookup / reply / follow-link / browse), **project**, and **alignment** with intent. Labels come from layers: rules first, then a cache, then [TypeSafe Jev](https://docs.typesafe.ai/) for the ambiguous rest. Anything still unclear goes to a review queue for you.
4. **Drift episodes.** An episode starts with a purposeful visit to a drift-prone site. It counts as *drift* when at least 3 minutes of off-task consuming follow within 15 minutes, before you return to work for at least 60 seconds. Triggers such as a meeting ending or an agent working are recorded alongside.
5. **Prediction.** Every predictor implements one interface: given the history before time *t*, the intent, and the site you just arrived on, return the probability of drift. A rolling weekly evaluation (no look-ahead) compares BehaviorGPT against a local baseline built from your own history, within a budget of at most 5 nudges per working day.

## Live nudges

`behaviornudge nudge run` watches what you're doing and sends a macOS notification when you start drifting, naming the task you meant to be doing (your Sunsama timer or timebox, a calendar block, your call, or the project an agent is working on).

| Nudge | Fires when (defaults) | Sound |
|---|---|---|
| Arrival | 20 s on a drift-prone site for a reason (reply, lookup, link) while your task is something else | Tink |
| Feed landing | 45 s straight onto a LinkedIn / X / Instagram feed while you have a task at hand | Tink |
| Purpose done | 30 s of off-task consuming right after that purposeful visit | Glass |
| Off-task | 8 min of off-task consuming while you have a task at hand | Sosumi |
| In a call | 30 s of consuming while a call app is using your microphone | none (silent) |
| Agent working | 2 min of consuming while a herdr agent is working | Pop |
| Messaging | During a task: 4+ different conversations in one stretch of Slack, Discord, WhatsApp, Signal, Messages or Telegram (after 60 s), or 5 min in them. A quick reply, or a task that's about messaging, stays quiet | Purr |

Each notification has four buttons:
- **Back to task:** a nudge that helped.
- **Too soon:** that kind of nudge waits 1.5× longer next time, in the same context.
- **Legit task:** this activity counts as on-task for this task from now on, both live and in backtests.
- **Snooze 30 min:** pauses all nudges for half an hour.

```sh
uv run behaviornudge helper build            # builds the small native helper app (done automatically too)
uv run behaviornudge nudge test off-task     # shows a sample nudge and prints the button you click
uv run behaviornudge nudge agent install     # run the engine under launchd: starts at login, restarts on exit
uv run behaviornudge nudge run               # or run it in a terminal (Ctrl-C to stop)
uv run behaviornudge nudge status            # running?, notifications allowed?, budget, task at hand, current activity
uv run behaviornudge nudge off | on | snooze 45
uv run behaviornudge nudge feedback list     # waits adjusted by "Too soon", legit-task suppressions
uv run behaviornudge nudge feedback reset s3 # undo one (a<N> = adjusted wait, s<N> = suppression)
uv run behaviornudge nudge stats --days 7    # nudges, actions, return-to-work within 5 min, shadow predictor ROC-AUC
```

**Settings:** all timings, the daily budget (8), cooldowns, quiet hours, sounds (a macOS sound name, a sound file, or `none`) and the call-app list are in the `[nudge]` section of `behaviornudge.toml`.

**Notifications:** the first nudge asks macOS for permission for "behaviornudge". In System Settings → Notifications → behaviornudge:
- set **Alert Style → Persistent** so nudges stay on screen with their buttons (Temporary banners vanish after a few seconds);
- turn **Summarize notifications** off so nudges aren't batched or reworded.

**Focus modes:** a Focus such as Work silences behaviornudge unless it's on that Focus's allowed list, and nudges then land quietly in Notification Center.

**Privacy:**
- **Call detection only checks which call apps are using the microphone. It never records or listens.** The technique comes from [jhammant/maccap](https://github.com/jhammant/maccap) (MIT).
- Notifications are local.
- BehaviorGPT scores each nudge moment in shadow mode through the same allowlisted, audited egress gate; the score is logged and never decides anything.

## Privacy

- **Local-first.** All source data stays on your Mac. `data/`, `.env` and your personal `behaviornudge.toml` are git-ignored.
- **One outbound path.** Every network call goes through a single gate with an allowlist. Allowed: activity key, URL path class, title trimmed to 80 characters, caption trimmed to 500 characters, project/channel, and a trimmed task title. A channel-only mode drops task titles. Screen text, full URLs, query strings, screenshots and HTML never leave the machine.
- **Audited.** Every outbound request is written to `data/audit.jsonl`.
- **Read-only sources.** Databases are opened with SQLite `mode=ro`. Nothing is written to Retrace, Sunsama or herdr.

## Requirements

- macOS on Apple Silicon
- Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/)
- At least one activity source:
  - [jhammant/retrace](https://github.com/jhammant/retrace): live capture, captions, Screen Time, browser history, and an EventKit calendar helper
  - [retrace.to](https://retrace.to) (haseab/retrace): segments with exact durations
- Optional:
  - [Sunsama](https://sunsama.com) (timers and timeboxes, exported to JSON)
  - [herdr](https://herdr.dev) (terminal pane folder and agent status)
  - API keys for [Unbox AI](https://portal.unboxai.com) and [TypeSafe](https://console.typesafe.ai)

## Setup

```sh
git clone https://github.com/natea/behaviornudge.git
cd behaviornudge
uv sync

cp behaviornudge.example.toml behaviornudge.toml   # edit paths, calendar names, repo -> channel mapping
cp .env.example .env                               # add UNBOXAI_API_KEY / TYPESAFE_API_KEY (never commit these)
```

## Usage

```sh
# 1. Build the timeline (use a fixed --until for reproducible builds)
uv run behaviornudge timeline build --until 2026-11-08T19:00:00

# 2. Label sessions (rules + cache; add --jev to send the ambiguous rest to TypeSafe Jev)
uv run behaviornudge label

# 3. Answer what the labeler couldn't decide
uv run behaviornudge review list
uv run behaviornudge review answer 42 --mode consuming

# 4. Detect drift episodes
uv run behaviornudge episodes

# 5. Upload the activity catalog to Unbox once, then run the rolling evaluation
uv run behaviornudge catalog upload
uv run behaviornudge report            # writes data/report.md (+ SVG charts)

# Live context (herdr focus/agent status + Sunsama timer), going forward
uv run behaviornudge sunsama-timer --login   # one-time browser sign-in to Sunsama's MCP server
uv run behaviornudge context log
```

`label --jev` sends each unique ambiguous activity to TypeSafe Jev once (cached). Low-probability answers go to `behaviornudge review list` for you to decide.

Calendar history is loaded through jhammant Retrace's `retrace-calendar` helper (Apple Calendar, which a Google calendar can sync into). Only calendars on `intent.calendar_allowlist` are used.

### Keeping Retrace running

Gaps in Retrace's capture become gaps in the history, so behaviornudge can put jhammant Retrace's server under launchd. Once installed, it starts at login and restarts within seconds if it dies.

```sh
uv run behaviornudge retrace agent install --replace   # --replace stops a Retrace you started by hand
uv run behaviornudge retrace agent status
uv run behaviornudge retrace agent uninstall
```

- **Config:** the agent is `~/Library/LaunchAgents/com.behaviornudge.retrace.plist`. It runs `python -m retrace.cli serve` from `sources.retrace_repo` and logs to `~/.retrace/serve.log`.
- **One-time permission grant:** a process started by launchd doesn't inherit your terminal's privacy permissions. Grant **Screen & System Audio Recording** and **Accessibility** to the Python interpreter that `install` prints, which is the real binary behind Retrace's `.venv/bin/python`. Then run `launchctl kickstart -k gui/$(id -u)/com.behaviornudge.retrace`.
- **Install check:** `install` waits for a real new capture, not just a healthy API, and tells you if the grant is missing.
- **Warnings:** `nudge status` and `retrace agent status` warn when the server is down, when captures are failing, or when the capture loop has stopped.
- **Python upgrades:** if uv upgrades that Python, the binary's path changes and the grant has to be given again; status will warn you when that happens.

### Keeping the nudge engine running

The engine can run under launchd too, so nudges don't depend on a terminal window staying open.

```sh
uv run behaviornudge nudge agent install --replace   # --replace stops an engine running in a terminal
uv run behaviornudge nudge agent status
uv run behaviornudge nudge agent uninstall
```

- **Config:** the agent is `~/Library/LaunchAgents/com.behaviornudge.engine.plist`. It runs `.venv/bin/behaviornudge nudge run` from the repo (so `behaviornudge.toml` and `.env` are found) and logs to `data/engine.log`.
- **Permissions:** it runs as the same uv Python as Retrace's agent, so Screen Recording and Accessibility carry over. The first time it reads a browser tab, macOS asks whether `python3.12` may control Google Chrome: click Allow.
- **Notices:** under launchd the log is out of sight, so the engine tells you in a plain notification (no nudge buttons, at most hourly) when the Sunsama sign-in has expired, or when another copy of your browser, such as an automation browser, is answering tab reads instead of the window in front.

## Development

```sh
uv run pytest
```

| Path | What it does |
| --- | --- |
| `src/behaviornudge/sources.py` | read-only readers for both Retraces |
| `src/behaviornudge/keys.py` | activity-key normalization |
| `src/behaviornudge/timeline.py`, `build.py` | overlap resolution, sessions, storage |
| `src/behaviornudge/projects.py` | terminal/editor project resolution (worktrees, titles, screen text) |
| `src/behaviornudge/calendar.py`, `intent.py` | calendar loading, intent resolution, forgotten-timer capping |
| `src/behaviornudge/labels.py`, `pipeline.py` | layered labeler, cache, review queue |
| `src/behaviornudge/episodes.py` | drift episodes and triggers |
| `src/behaviornudge/predictors/` | predictor contract, baseline, BehaviorGPT adapter |
| `src/behaviornudge/jev.py` | TypeSafe Jev labeling (question wording, probability routing) |
| `src/behaviornudge/evaluate.py`, `report.py` | rolling evaluation, metrics, decision, report |
| `src/behaviornudge/context.py`, `sunsama_mcp.py` | live herdr logger; Sunsama timer via Sunsama's MCP server |
| `src/behaviornudge/nudge/` | live nudge engine: tracker, rules, delivery, feedback, shadow scoring |
| `helper/BehaviorNudgeHelper.swift` | native helper: notifications with buttons, front app, call detection, idle |
| `src/behaviornudge/egress.py` | the outbound allowlist and audit log |

This project is planned with [OpenSpec](https://github.com/Fission-AI/OpenSpec). Browse it all at [natea.github.io/behaviornudge/specs](https://natea.github.io/behaviornudge/specs/) (generated with [spek](https://github.com/spekhq/spek)). Current capabilities live in [`openspec/specs/`](openspec/specs/); changes in [`openspec/changes/`](openspec/changes/):

- [`drift-prediction-backtest`](openspec/changes/archive/2026-11-08-drift-prediction-backtest/): the offline backtest (done and archived; its specs are in `openspec/specs/`)
- [`live-nudge-engine`](openspec/changes/archive/2026-11-08-live-nudge-engine/): live Mac notifications with feedback (done and archived; its specs are in `openspec/specs/`)
- [`retrace-supervision`](openspec/changes/archive/2026-11-09-retrace-supervision/): keep jhammant Retrace running under launchd and warn when capture stops (done and archived; its spec is in `openspec/specs/`)
- [`easytpp-drift-predictor`](openspec/changes/easytpp-drift-predictor/): a neural temporal-point-process predictor via [EasyTPP](https://github.com/ant-research/EasyTemporalPointProcess) (proposed)

## License

[MIT](LICENSE) © 2026 Nate Aune
