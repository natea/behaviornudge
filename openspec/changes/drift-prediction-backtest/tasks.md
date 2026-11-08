# Tasks

Ordering note: groups 1–9 produce a full end-to-end run with **rule-only labels** and BehaviorGPT. That's the hackathon demo. Group 10 adds the Jev labeling layer and re-runs. Group 11 adds the live context logger.

## 1. Project setup

- [x] 1.1 Create a `uv` Python ≥ 3.11 project (`pyproject.toml`, package `behaviornudge`, `tests/`) with dependencies `behaviorgpt`, the TypeSafe SDK, `pandas`, `pyarrow` and `pytest`; verify `uv sync` succeeds and `uv run pytest` runs (zero tests is fine)
- [x] 1.2 Add `.gitignore` covering `.env`, `data/`, `.venv/` and `__pycache__/`, and an `.env.example` listing `UNBOXAI_API_KEY` and the TypeSafe key variable with empty values; verify `git check-ignore .env data/x` reports both as ignored
- [x] 1.3 Add a config file (`behaviornudge.toml`) holding source paths, timezone `America/New_York`, drift-prone sites, drift window/threshold, confidence thresholds, mode definitions, channel-only flag, repo→channel mapping and calendar allowlist (`["Nate Aune (natejaune)"]`), plus a loader that reads secrets only from the environment or `.env`; verify with a unit test that a missing key raises a clear error naming the variable

## 2. Privacy boundary and audit log

- [x] 2.1 Implement the outbound-request gate: an allowlist of fields with trimming (title 80 chars, caption 500 chars) that rejects screen text, full URLs and query strings, plus the channel-only mode; verify with unit tests for each allowed and disallowed field
- [x] 2.2 Write every outbound request to `data/audit.jsonl` (time, destination, fields sent) and make sure secrets never appear in logs; verify with a test that sends a fake request and checks the log entry and that the key is absent

## 3. Activity timeline

- [x] 3.1 Implement read-only readers for jhammant Retrace (`captures`, `activity_events`) and retrace.to (`segment`), opening SQLite in `mode=ro`; verify with a test on a small fixture database, and by checking that the real databases' modification times are unchanged after a run
- [x] 3.2 Implement activity-key normalization (domain + path class, app, app + project) with a path-class table for LinkedIn, X, Instagram, YouTube and GitHub; verify with unit tests covering the spec scenarios (`linkedin.com/feed` vs `/messaging`, query strings dropped)
- [x] 3.3 Implement session merging (gap under 30 s merges, under 3 s becomes a transition) and overlap precedence (exact durations win; Chrome/Safari visits only refine page identity); verify with fixture tests for rapid records, blips, two-Retrace overlap and a Chrome visit inside an untitled Chrome session
- [x] 3.4 Store sessions in `data/timeline.sqlite` with UTC times and local hour/weekday, and add `uv run behaviornudge timeline build` printing a summary (sessions, date range per source, missing sources); verify that two builds produce identical output and that the local hour for 18:46 UTC on 2026-11-08 is 13

## 4. Intent context

- [x] 4.1 Export Sunsama history (tasks per day with channel and planned slots, timer sessions per task) to `data/sunsama/*.json` using read-only calls; verify the export covers the timeline's date range and record which days have planned-slot data
- [x] 4.2 Load calendar history by running jhammant Retrace's existing helper (`~/.retrace/bin/retrace-calendar <days_back>`, sized to the timeline's start date, about 215 days) and saving its output to `data/calendar/events.json`; convert unix times to UTC, drop all-day events, and keep only events from calendars on the config allowlist (initially "Nate Aune (natejaune)"); verify that no events from other calendars (e.g., Meditation, Exercise) remain, and the events span the timeline's date range and that a known meeting appears with correct local start/end times
- [x] 4.3 Implement intended-task resolution (timer → planned slot → calendar event → recent creative within a configurable window, default 30 min → none), recording the source, plus an intent-source coverage summary per day and for windows of 15/30/60/120 min; verify with tests for each spec scenario (including "Sunsama not used that day" and the configurable window), and by printing coverage on real data
- [x] 4.4 Implement forgotten-timer capping (45 min without related activity) and the corrected-time-per-task report; verify with a test reproducing the 15:00→08:11 timer, capped at 45 min after the last related activity
- [x] 4.5 Implement folder→project resolution (git worktree → main repo via the common git dir → channel mapping) and best-effort historical inference from screen text/captions marked as inferred; verify with tests using a temporary git repo with a worktree, and a fixture capture whose text names a repo

## 5. Rule labeling, cache and review queue

- [x] 5.1 Implement the rule table for mode, purpose, project and alignment (e.g., Zed/Ghostty → creative; `*/feed` and `x.com/home` → consuming/browse; `linkedin.com/messaging` → reactive/reply; `linkedin.com/search` and `/in/` → reactive/lookup; `x.com/status` → follow-link), recording the label source; verify with unit tests, and by printing the share of session time covered by rules on real data
- [x] 5.2 Implement the label cache keyed by (activity key, trimmed title, intended task, definitions fingerprint) and the review queue with a CLI to answer queued items, where answers become cache entries; verify with tests that a cache hit avoids further labeling and that an answer is reused
- [x] 5.3 Implement alignment from rules plus intent (on-task when the project matches the intended project, `no-intent` when there is none, off-task otherwise); verify with tests including "feed is the task" (intended "Alex re LI post" + LinkedIn → on-task)

## 6. Drift detection

- [x] 6.1 Implement episode entry detection (purposeful session on a configured drift-prone site) and drift/clean classification (≥ 3 min consuming + off-task within 15 min before returning to on-task/creative); verify with tests for each spec scenario
- [x] 6.2 Record episode details and triggers (meeting ended within 5 min from the calendar export in 4.2, notification within 2 min, agent working from the herdr log when available); verify with fixture tests for `meeting-ended` and `agent-working`
- [x] 6.3 Add `uv run behaviornudge episodes` producing the episode table and a summary by site, purpose, trigger, hour and weekday; verify on real data and record the episode count. If fewer than ~50, apply the widened entry definition from design Risks and note it

## 7. Predictor contract and baseline

- [x] 7.1 Define the predictor contract (history before `at`, `at`, intent → drift probability, ranked next keys, model id, latency; optional `fit`) and a registry selectable by name; verify with a test that a dummy predictor registers and runs through the contract
- [x] 7.2 Implement the baseline predictor (smoothed next-mode model on current key, previous key, hour and weekday, trained only on data before the test week); verify with unit tests on a synthetic timeline with a known pattern, and a test that it never sees sessions after `at`

## 8. BehaviorGPT predictor

- [ ] 8.1 Build the activity catalog (one row per activity key; `name`, `brand`, `categories` = [mode, drift-prone]; `event_type`/`group` = `product`; all 15 columns present) as parquet, and upload it once with `embed(..., wait=True)`, storing the catalog id in `data/`; verify the parquet passes a column check and that `list_catalogs()` shows the catalog
- [ ] 8.2 Implement the BehaviorGPT predictor: history → `View(key)` per session, `Order([key])` for sessions ≥ 10 min, real timestamps, `User(timezone="America/New_York")`; drift score = summed score of consuming keys in the top K, plus a rank-based variant; all calls go through the outbound gate; verify with a test using a mocked client that checks the request contains only keys and timestamps, then one live call that returns ranked items
- [ ] 8.3 Handle failures and timeouts by recording the failure and continuing; verify with a mocked error test that the run continues and the failure is reported

## 9. Evaluation harness and report (hackathon demo point)

- [ ] 9.1 Implement the rolling weekly evaluation: query each registered predictor at every episode entry with history before `at`, and train/fit only on prior weeks; verify with a test that asserts no session after `at` reaches any predictor
- [ ] 9.2 Compute metrics per predictor and week (ROC-AUC, PR-AUC, precision/recall at ≤ 5 nudges/working day, median lead time, false alarms/day, latency, breakdowns by trigger, hour and intent source with per-group episode counts) and exclude failures from metrics; verify with a unit test on hand-computed toy predictions
- [ ] 9.3 Add the separate pre-entry analysis at trigger moments; verify on a fixture with a meeting-ended trigger followed by a drift
- [ ] 9.4 Generate `data/report.md` with charts, the corrected-time-per-task table, label-source coverage, intent-source coverage and the recommendation (BehaviorGPT vs baseline, with reason); verify by running `uv run behaviornudge report` end to end on real data with rule-only labels and reading the report

## 10. Jev labeling layer

- [ ] 10.1 Confirm the TypeSafe key variable name from the SDK constants and pick a pinned model name from `GET /v1/models`; record both in config and `.env.example`; verify with a single `GET /v1/models` call that succeeds
- [ ] 10.2 Implement the Jev layer for cache misses: one request per session with questions `mode`, `purpose`, `project` (Sunsama channels + `none`) and `alignment`, a companion Noul `fits`, the user's mode definitions as option descriptions, and the payload built by the outbound gate; store version and prompt fingerprint with each label; verify with a mocked test of the request shape, then a live run on 20 sessions that checks the audit log contents
- [ ] 10.3 Implement confidence routing (≥ 0.9 accept, 0.5–0.9 accept plus review sample, < 0.5 or `fits` < 0.3 → review queue); verify with unit tests at each boundary
- [ ] 10.4 Re-run labeling, episodes and report with the Jev layer enabled; verify that the report shows label-source coverage including Jev, and compare episode counts and predictor results with the rule-only run

## 11. Live context logger

- [ ] 11.1 Implement the read-only logger: subscribe to herdr `events.subscribe` (pane/tab/workspace focus, pane updates, agent status changes), read the focused pane from `herdr api snapshot` on each focus event, poll the Sunsama active timer every 30 s, and append to `data/context-log.jsonl`; verify by switching herdr panes and starting a Sunsama timer and seeing both appear within a few seconds
- [ ] 11.2 Use the context log as a source for live terminal projects (only while Ghostty is frontmost) and for the `agent-working` trigger; verify with a test that a log entry overrides a misleading terminal window title

## 12. Integration checks

- [ ] 12.1 Run the full pipeline from scratch (`timeline build` → labeling → `episodes` → `report`) and verify it completes, both Retrace databases are unchanged, and every outbound request appears in `data/audit.jsonl` with only allowed fields
- [ ] 12.2 Verify that rebuilding from unchanged sources and cached labels produces an identical report

## Workflow follow-up

- Review the report's recommendation, then archive this change.
- After archiving, plan `easytpp-drift-predictor`, which depends on the predictor contract, labeled timeline and evaluation harness from this change.
