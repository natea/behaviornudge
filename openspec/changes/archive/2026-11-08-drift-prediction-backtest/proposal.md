# Proposal

## Why

I lose focus in a predictable way. I leave creative work (writing, building) to do something specific on LinkedIn or X, such as replying to a message, looking someone up, or opening a link, and then I drift into the feed. A blocklist can't fix this, because many of my planned tasks actually happen on those sites. The long-term goal is a nudge that catches the drift *before* it happens. First I need to know whether a behavior model (Unbox BehaviorGPT) predicts these drifts well enough to time a nudge, and whether it beats a simple model built from my own history. This change answers that question offline, using data I already have, before any live nudging is built.

## What Changes

- Build one combined activity timeline from local sources: jhammant Retrace (`~/.retrace/retrace.db`: captures and activity events from Chrome, Safari and Screen Time), retrace.to (`~/Library/Application Support/Retrace/retrace.db`: segments with exact durations), Sunsama (planned tasks, time slots, active timer), and herdr (focused terminal folder, agent working/idle/done).
- Label each stretch of activity with **mode** (creative / reactive / consuming), **purpose** (lookup / reply / follow-link / browse), **project** (from Sunsama channels), and **alignment** with the intended task (on-task / supporting / off-task). Labels come from layers: simple rules, then a cache, then TypeSafe Jev with confidence scores, then my own answers for anything still unclear.
- Find **drift episodes**: a purposeful visit that turns into consuming activity unrelated to the intended task.
- Compare two predictors of drift on the past weeks: BehaviorGPT and a local baseline ("what usually follows what, at this hour"). Measure how early and how accurately each would have warned.
- Write a report that decides whether BehaviorGPT goes into the live nudge engine.
- Out of scope: live nudges, menu-bar changes, any change to the Retrace codebase, and writing anything to Sunsama.

## Capabilities

### New Capabilities
- `activity-timeline`: combine the local activity sources into one normalized, time-ordered record of what I was doing, merging duplicates and splitting it into sessions.
- `intent-context`: work out what I was *supposed* to be doing at any moment (active Sunsama timer, then the planned time slot, then my last creative project) and which project a terminal belongs to (herdr's focused folder).
- `activity-labeling`: assign mode, purpose, project and alignment labels through the layered labeler, with confidence-based routing and caching.
- `drift-detection`: find drift episodes and their triggers (meeting ended, notification, agent working) in a labeled timeline.
- `drift-prediction-evaluation`: run the predictors over historical timelines without letting them see the future, and report accuracy, warning lead time and false-alarm rate.

### Modified Capabilities
(none, since no specs exist yet)

## Impact

- **New code in this repo only**, as a Python project managed with `uv`. That matches both the `behaviorgpt` and TypeSafe Python SDKs.
- **External services:** Unbox BehaviorGPT (`UNBOXAI_API_KEY`) and TypeSafe Jev (API key; env var name to be confirmed from the SDK docs). Only trimmed labels, URL paths, titles and captions are sent. Screenshots, page text and HTML never leave the Mac.
- **Read-only access** to both Retrace databases, the Sunsama MCP/API, and `herdr api snapshot`. Nothing is written to any of them.
- **Depends on** Retrace retention staying at 365 days (already set), because retention purges activity rows as well as screenshots.
