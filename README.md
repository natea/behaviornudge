# behaviornudge

**Catch the drift before it starts.**

You leave real work to do something specific on LinkedIn or X, like replying to a message, looking someone up, or opening a link. Twenty-five minutes later you're still in the feed. A blocklist can't fix this, because half of your real tasks happen on those same sites.

behaviornudge reads the screen activity your Mac already records. It finds the moments when a purposeful visit turned into feed-scrolling and tests which model predicts those moments early enough to nudge you back. The long-term goal is a nudge that fires *before* the drift: at the trigger (a meeting just ended, Claude is working and you're waiting), on arrival ("you came to reply to Alex"), or once the reason for the visit is done ("reply sent. Back to your project?").

Built at [Sundai Hack 148 – Behavioral Models: Predict the next action](https://www.sundai.club/events/boston/agi-house-sf-mit-hack-energy), a [Sundai Club](https://www.sundai.club) hackathon at MIT on November 8, 2026. The event was centered on [Unbox AI's BehaviorGPT](https://github.com/Unbox-AI/behaviorgpt), a large behavioral model that predicts what someone will do next.

> **Status:** the offline backtest is complete. It builds a labeled timeline from local data, detects drift episodes, and compares BehaviorGPT with a baseline built from your own history. In the first run on real data, BehaviorGPT won 17 of 28 test weeks, but both signals are weak (ROC-AUC 0.565 vs 0.513). Live nudges come in a later change. See [`openspec/changes/drift-prediction-backtest/tasks.md`](openspec/changes/drift-prediction-backtest/tasks.md) for progress.

For the interactive architecture diagram, download [`docs/architecture.html`](docs/architecture.html) and open it in a browser.

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
| `src/behaviornudge/egress.py` | the outbound allowlist and audit log |

This project is planned with [OpenSpec](https://github.com/Fission-AI/OpenSpec). The proposal, design, specs and tasks live in [`openspec/changes/`](openspec/changes/):

- `drift-prediction-backtest`: the offline backtest (all 37 tasks done)
- `easytpp-drift-predictor`: a neural temporal-point-process predictor via [EasyTPP](https://github.com/ant-research/EasyTemporalPointProcess) (proposed)

## License

[MIT](LICENSE) © 2026 Nate Aune
