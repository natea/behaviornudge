# Design

## Context

See proposal.md (Why) for motivation. Facts found during exploration that shape the approach:

- **jhammant Retrace** (`~/Documents/code/retrace`, data in `~/.retrace/retrace.db`)
  - `captures` holds app, bundle id, window title, URL, screen text, an on-device caption and calendar event.
  - Captures happen on app switches (after a 1.5 s wait) plus a fallback tick (`capture_interval_s` = 45 s). Within one app the gaps run about 1–2 minutes.
  - `activity_events` was filled in from Chrome history (54k visits since Aug 10, but **no durations**: `seconds` = 0), Safari, and Screen Time (`knowledgec`, since Oct 22).
  - The retention purge deletes old activity rows as well as screenshots. It is now set to 365 days.
- **retrace.to** (`~/Library/Application Support/Retrace/retrace.db`, 23 GB)
  - The `segment` table has `bundleID, startDate, endDate (ms), windowName, browserUrl`: 245k segments from Apr 11 with exact durations.
  - The median segment is 4 s.
- **Sunsama** (MCP, already connected)
  - `sunsama://active-timer` returns the live focus task.
  - `sunsama://tasks/{day}` returns title, channel, projected time slots and estimate.
  - `get_task_actual_time` returns past timer sessions with start and end. It confirmed that timers get forgotten: one ran 3:00 PM to 8:11 AM.
  - The user's timezone is America/New_York.
- **herdr** (`herdr api snapshot`)
  - Shows the focused pane's `cwd`/`foreground_cwd` and each agent's `agent_status` (working / idle / done).
  - It shows **live state only, with no history**.
  - Terminal window titles are unreliable: Ghostty showed `parental-controls` while the focused pane was in `behaviornudge`.
- **BehaviorGPT** (Python SDK `behaviorgpt`)
  - `complete(history, catalog_id=, user=User(timezone=...), limit=)` ranks catalog items.
  - Events: `Search`, `View(item_id)`, `Order([ids])`, and the model reads hour and weekday from timestamps.
  - Catalogs are parquet files with 15 columns; `event_type` and `group` must be `product`.
  - Not trained on attention or app data, so this use is off-label.
- **TypeSafe Jev** (`POST /v1/systemone`, Bearer key, Python SDK)
  - Several named questions about one `state` per request.
  - Choice options can carry descriptions. Answers carry probabilities and a confidence score.
  - Does not train on user data. Zero data retention is enterprise-only. US-hosted.

## Goals / Non-Goals

**Goals:**
- A reproducible offline pipeline: rebuilding from raw sources gives the same timeline, labels and report.
- One module controls exactly what leaves the Mac.
- An honest comparison: the baseline and BehaviorGPT see the same history and are scored on the same episodes. Neither sees the future.

**Non-Goals:**
- Real-time performance, a daemon, or menu-bar UI.
- Tuning nudge wording or timing.
- Classifying screenshots or images.
- Supporting more than one user or Windows.

## Decisions

### D1. Normalized activity unit = "activity key" + session
Each raw record maps to an activity key at a level of detail meaningful for behavior:
- **Browser:** domain plus *path class*, e.g. `linkedin.com/feed`, `linkedin.com/messaging`, `linkedin.com/profile`, `x.com/home`, `x.com/status`.
- **Native apps:** the app, e.g. `zed`, `superhuman`.
- **Terminals and editors:** the app plus the project folder.

Consecutive records with the same key, separated by gaps under 30 s, merge into one session. Blips under 3 s are kept only as transitions, not as sessions.
- *Why:* raw URLs are too detailed for the catalog to generalize and leak personal data. App-only is too coarse, because it can't tell the LinkedIn feed from LinkedIn messages.
- *Alternative:* full URLs with embeddings. Rejected for privacy and catalog-size reasons (BehaviorGPT allows ≤100k items, and we want roughly 100–300).

### D2. Source precedence and backfill
- retrace.to segments supply durations up to the cutover date.
- jhammant captures and Screen Time supply durations after it.
- Chrome and Safari visit history fill in *which page* inside browser time that has no detail, but never supply durations.
- Sources overlap during the cutover period. On overlap, the source with exact durations wins, then sources with more context fields.

### D3. Intended task, resolved per moment
1. Use the Sunsama timer session covering that time, if any (from `get_task_actual_time`).
2. Otherwise use the projected time slot for the task planned at that time.
3. Otherwise use the calendar event covering that time (a non-all-day event the user is in, e.g. "Client sync" → intended activity is that meeting and its project).
4. Otherwise use the most recent creative project within a configurable window (default 30 min).
5. Otherwise "no intent".

**Sunsama is used inconsistently, so steps 1–2 cover only a minority of the timeline.** For this user, step 4 (recent creative work) is the expected *main* source, and "no intent" will be common. That's fine: drift detection counts consuming time with no intent toward drift (see the drift-detection spec), and the report breaks results down by intent source (D9).

**Calendar source.** The user's Google calendar (natejaune) syncs into Apple Calendar, so jhammant Retrace's existing EventKit helper (`~/.retrace/bin/retrace-calendar [days_back]`) is both the **history** and the **live** source, and no new calendar integration code is needed. Verified: `retrace-calendar 180` returned 3,387 events from 2026-05-12 to 2026-11-08 across the natejaune calendar and habit calendars (Meditation, Yoga, Exercise, Music), with fields `title, start, end (unix s), location, calendar, allDay, id`. Requesting about 215 days covers retrace.to's start (Apr 11).
- **Only the "Nate Aune (natejaune)" calendar is used.** All other calendars (Meditation, Yoga @ YMCA, Exercise, Exercise schedule, Music, CC, Birthdays, …) are excluded from intent and triggers, by user decision. The allowlist of calendar names lives in config.
- *Limitation:* the helper does not report declined events, so declined meetings count as intent. Accepted for now. If it skews results, Sunsama's `sunsama://calendar/events/{day}` or Google Calendar can be used to filter them.
- The specs don't depend on which calendar provider is used. The same calendar data supplies the `meeting-ended` trigger (D6).

**Window tuning.** The report shows how the share of moments with a known intent changes with the window length (15 / 30 / 60 / 120 min), so the default can be chosen from data.

**Forgotten timers are capped.** A timer session counts as "intended" only while Retrace shows activity related to that task's project within the last 45 minutes. Past that point it becomes "no intent".
- *Why:* forgetting to stop timers is routine for the user, not a rare case. A forgotten timer would label 17 hours as "intended", which would poison the alignment labels.
- *By-product:* capping gives an estimate of the **corrected time per task**: how long the screen actually showed work related to the task while its timer ran. The report compares this to Sunsama's recorded time, which helps future time estimates. The capped end time is also the natural trigger for a future "Still on X?" nudge, which is out of scope here.

### D4. Terminal project: herdr live, best-effort for history
- **Read the focused pane from the snapshot.** The current terminal project is `herdr api snapshot` → `focused_pane_id` → that pane's `foreground_cwd`. Do **not** use `herdr pane current`: it resolves the pane the command runs in through `HERDR_PANE_ID`, so a background process gets the wrong pane or none.
- **Trust herdr only while its terminal is frontmost.** herdr's "focused" means focused *inside herdr* and stays set while the user is in Chrome. So the herdr folder counts as the current project only while the frontmost macOS app (from `retrace-watch` / Retrace captures) is the terminal hosting herdr (Ghostty). Otherwise the project comes from the frontmost app itself.
- **Resolve worktrees to their main repo.** Folder names such as `worktree-lucky-meadow-8c6c` or `feat-ios-wake-word-idle-sleep` are git worktrees. Map the cwd to its main repo via git (the common git dir), then the repo to a Sunsama channel through a small editable mapping table.
- **Use events, not polling.** The sampler subscribes to herdr's socket API (`events.subscribe`) for `pane.focused`, `tab.focused`, `workspace.focused`, `pane.updated` and `pane.agent_status_changed`, and logs each event with a timestamp to a local file. Agent status changes (working / done) are logged as possible triggers. The Sunsama active timer is still polled every 30 s, since it has no event stream.
- **History is best-effort.** herdr keeps no history, so terminal sessions before the sampler started get their project from Retrace screen text and caption, matched against known repo folder names. The window title is a last resort.
- *Alternatives:* trusting window titles, rejected because a title proved wrong (`parental-controls` vs. `behaviornudge`); polling the snapshot every 30 s, rejected because it misses short pane switches and delays trigger events.

### D5. Layered labeler
The layers run in order: rules, then cache, then Jev, then the user.
- **Rules:** a hand-written table on activity key covers the clear cases (Zed or Ghostty → creative, `*/feed` and `x.com/home` → consuming, messaging → reactive).
- **Cache:** keyed on (activity key, trimmed title, intended task id).
- **Jev (as built, following the TypeSafe skill's "judgments from Jev, decisions in code"):** one request per unique *activity* (activity key + trimmed title) that rules left without a mode, cached by activity and question fingerprint. Scope: sessions of at least 60 s, plus every session inside a drift window. `state` = {app, activity_key, path_class, title ≤ 80 chars, caption ≤ 500 chars, prev_activity_key}, built by the egress gate. Questions, all worded in `jev.py`:
  - `mode`: Choice with the user's own definitions of creative / reactive / consuming.
  - `purpose`: Choice of lookup / reply / follow_link / browse / own_work (→ `n/a`).
  - `project`: Choice among the Sunsama channels plus `none`.
  - `fits`: companion Noul ("is the person actively using this?") to catch idle, locked and system screens.
  - **Alignment is not asked:** it is decided in code from mode, project and intent (`rule_alignment`). For drift detection only consuming sessions' alignment matters, and code decides those.
- **Routing by the probability of the chosen mode** (not `confidence`, which measures how concentrated the distribution is, not correctness):
  - ≥ 0.9: accept.
  - 0.5–0.9: accept and add to the review sample.
  - < 0.5, or `fits` < 0.3: queue for the user, and **hold the mode back** (unlabeled) until answered; purpose and project are kept.
  - The user's answers become cached labels.
- **Pin the Jev model version** (`jev-1.14.0`; `GET /v1/models` lists only aliases, but the pinned id is accepted and echoed in every response) for the whole backtest.
- *Why layers:* most of the time is covered by cheap rules, so Jev only handles the ambiguous long tail. That keeps cost negligible and the labels explainable.

### D6. Drift episode definition
An **episode** starts at an *entry*: a session with `purpose` ∈ {lookup, reply, follow-link} on a site the user has marked as a drift-prone site (initially LinkedIn, X and Instagram, configurable).

It is a **drift** if within 15 minutes of entry:
- the timeline contains ≥ 3 minutes of `consuming` and `off-task` activity in total,
- *before* returning to an `on-task` or creative session.

Otherwise it is a **clean visit**.

**Triggers** are also recorded for each entry: calendar meeting ended in the last 5 min, a notification in the last 2 min, or a terminal agent `working` (from the sampler going forward). These are features to analyze, not part of the label.

### D7. Adapter 1: BehaviorGPT (see D12)
Everything in this section lives inside the BehaviorGPT adapter. None of it leaks into the shared timeline, labels or harness.
- **Catalog:** one row per activity key.
  - `name` = a human label ("LinkedIn feed").
  - `brand` = the domain or app.
  - `categories` = [mode, drift-prone flag].
  - `event_type` and `group` = `product`; the other columns are null or defaults.
- **History:** the last N sessions before the decision point.
  - Every session becomes a `View(key)`.
  - Sessions of ≥ 10 min become an `Order([key])`, signaling commitment.
  - Timestamps are real; `User(timezone="America/New_York")`.
- **Score:** `complete(..., limit=K)` returns ranked keys, and the drift score is the summed score of consuming keys in the top K. Because the scale of the scores is not documented, a rank-based variant is reported alongside (the best rank of any consuming key).
- `predict=` is left at the SDK default (`"view"`: the next item looked at).
- **Transport: raw API with real timestamps (user decision, 2026-11-08).** The SDK's `complete()` re-stamps every history event a few milliseconds before "now", which would hide the real hour and weekday of past episodes. The adapter therefore builds requests with the SDK's public models (`UnboxAIRequest`, `SessionEvent`, `View`/`Order`) carrying each session's **real timestamp**, plus the request-level as-of `timestamp` = the decision time, and posts them to `POST /vector/recommendations` with the same `UNBOXAI_API_KEY` (`x-api-key`). `register_event=False` so Unbox does not store the history. Work blocks (gaps > 30 min) become separate `session_id`s.
- **Catalog scope:** keys seen in at least 5 sessions get their own catalog item; rarer keys collapse into one `other-web` / `other-app` item. This limits which visited sites are uploaded and helps the model generalize.

### D8. Adapter 2: local baseline (see D12)
A local model of the next activity type given (current key, previous key, hour of day, weekday), with smoothing, trained only on data before each test week. It's cheap, transparent, and the bar BehaviorGPT must clear.

### D9. Evaluation protocol
- **Decision point:** the moment of each episode entry. Each predictor outputs a drift score using only events before that moment.
- **Rolling split:** train or calibrate on weeks 1..k, test on week k+1, then advance a week.
- **Metrics:**
  - ROC-AUC and PR-AUC for drift vs. clean.
  - Precision and recall at a threshold producing ≤ 5 nudges per working day.
  - Median warning lead time (from decision point to the first consuming session).
  - Results split by trigger type and time of day.
  - Results split by intent source (timer / planned / calendar / recent-creative / none), because Sunsama timers are rare for this user and a predictor that only does well on moments with a timer isn't useful.
- **Pass bar for adopting BehaviorGPT:** better PR-AUC than the baseline across most test weeks, at a cost and latency suitable for live use. Otherwise ship the baseline and record why.
- **A second experiment,** run separately, asks whether BehaviorGPT helps *before* entry. At trigger moments, does it predict that the next session is drift-prone?

### D10. Privacy boundary
All network calls go through one `egress` module with an allowlist of fields: activity key, path class, title trimmed to 80 chars, caption trimmed to 500 chars, Sunsama channel, and trimmed task title. Screen text, full URLs, query strings, screenshots and HTML are never sent. Every request is written to a local audit log.

Data and caches live in `./data/`, which is gitignored. API keys come from environment variables only.

### D12. Swappable predictor interface
BehaviorGPT was trained on shopping, not computer activity, and no off-the-shelf desktop-activity model exists yet. So the predictor is a swappable component.

**Contract:**
- `predict(history, at, intent) -> DriftScore`.
- `history` is the list of shared-timeline sessions strictly before `at`.
- `intent` is the D3 result.
- `DriftScore` = {`p_drift` (probability of drift within the D6 window), `ranked_next` (activity keys with scores), `model_id`, `latency_ms`}.
- Adapters may also implement `fit(train_timeline)`. Zero-shot adapters treat it as a no-op.

**Rules:**
- **Model details stay in the adapter.** Model-specific mapping (catalogs, event encodings, prompts, API keys, tokenization) lives only inside the adapter.
- **The harness knows only the interface.** The evaluation harness (D9) knows only this interface plus a registry of adapters. Adding a model = one adapter plus a rerun of the same report on the same episodes.
- **Outbound calls still go through D10.** Any network call an adapter makes goes through the `egress` module.
- **The live engine picks by config.** The future live engine selects the adapter by configuration, falling back to the baseline when a cloud adapter fails or times out.

**Adapters:**
- **In this change:** BehaviorGPT (D7, used for the hackathon) and the local baseline (D8, always on as the bar to beat).
- **Future, not built here:**
  - a neural temporal point process via EasyTPP (proposed separately as `easytpp-drift-predictor`);
  - a small local LLM reading the timeline as text;
  - Jev used as a predictor, asking a yes/no question about drift.

*Alternative:* hard-wiring BehaviorGPT and refactoring later. Rejected, because the comparison is the point of this change and a second model is already planned.

### D11. Stack
Python ≥ 3.11 with `uv`, the `behaviorgpt` and TypeSafe SDKs, SQLite for intermediate tables, pandas for analysis, and a Markdown report with charts. The Sunsama history is pulled once into local JSON. Whether that goes through the MCP or the API is a detail that doesn't affect the rest of the design.

## Risks / Trade-offs

- **Off-label use:** BehaviorGPT isn't trained on attention data and may underperform → that's what the baseline comparison is for. The outcome "the baseline wins" is acceptable and is recorded.
- **Too few drift episodes:** with only weeks of rich data there may not be enough episodes → widen the entry definition (all LinkedIn/X sessions) and pool weeks, and report confidence intervals.
- **Overlap between the two Retraces** could double-count time → apply D2 precedence and drop duplicates by time window and app.
- **Chrome history has no durations** → use it only to name pages inside timed browser sessions.
- **Jev labels drift if the model changes** → pin the model version, and log version and prompt hash with each cached label.
- **Personal data in task titles** (people's names) reaches Jev → trim titles, and offer a channel-only mode (config flag).
- **Sunsama projected slots** may not be retrievable for past days → fall back to timer sessions plus last creative project, and report how much of the timeline has intent from each source.
- **Unbox catalog limit** (2 catalogs per key) → reuse one catalog, re-embedding when activity keys change.

## Open Questions

- The TypeSafe API key environment-variable name and the exact pinned Jev model name: both come from the SDK constants and `GET /v1/models` at implementation time.
- Whether the live engine should later read Sunsama through the MCP from a background process, or through its calendar sync. This doesn't affect the backtest.
