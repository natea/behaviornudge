# Design

## Context

- **Why messaging is invisible today:** messaging apps (`slack`, `discord`, `whatsapp`, `signal`, `messages`) are labeled *reactive* by rule (`labels.REACTIVE_APPS`). Every existing drift moment keys on *consuming* activity: `is_drift`, the off-task stretch, feed landing. Arrival and purpose-done only consider drift-prone websites.
- **Messaging tasks are already handled:** alignment marks reactive activity *on-task* when the task's title contains comms words (`labels.COMMS_WORDS`: reply, message, slack, inbox, re, …).
- **What the live tracker sees:** the front app and, for browsers, the tab URL and title. The helper doesn't read native window titles.
- **What Retrace sees:** jhammant Retrace records the front window's title with each capture. It captures on window changes and every 45 s, and keeps the latest in `~/.retrace/status.json` (`last_app`, `last_window`, `last_hash_at`). Slack and Discord titles name the channel or DM. WhatsApp and Signal desktop show only the app name and an unread count.

## Goals / Non-Goals

**Goals:** catch inbox-browsing and long, aimless messaging during a task; never nudge a quick reply or a task that is about messaging; add no new permissions or outbound data.

**Non-Goals:** reading message content, detecting typing, changing labels or the backtest's drift definition (a later follow-up).

## Decisions

### D1. A separate nudge type, not relabeling messaging as consuming
- Relabeling would ripple into the backtest, the BehaviorGPT catalog and the off-task stretch, and would wrongly mark quick replies as drift.
- A dedicated `messaging` moment keeps the existing semantics and gets its own sound, copy, cooldown and "Too soon" multiplier.
- Precedence: `in_call > purpose_done > arrival > messaging > agent_working > off_task`. Messaging sessions are reactive, so they rarely compete with the consuming moments.

### D2. The messaging stretch
- **What counts:** a session is messaging if its app short name is in `messaging_apps`, or its domain matches one of the web entries (`app.slack.com`, `discord.com`, `web.whatsapp.com`, `web.telegram.org`).
- **The stretch:** trailing messaging time, walking back through sessions. Non-messaging sessions shorter than 60 s are skipped without counting; one of 60 s or more ends the stretch. That's the same return rule as the off-task stretch.
- **Why it spans apps:** hopping from Slack to WhatsApp to Discord is the pattern, not three separate visits.

### D3. Two triggers
- **Hopping:** the number of distinct conversations noted since the stretch started is at least `messaging_hop_conversations` (4), and the stretch has lasted at least `messaging_hop_min_s` (60 s).
- **Lingering:** the stretch has lasted `messaging_dwell_min` (5 min) × the "Too soon" multiplier for (messaging, app, intent source).
- **Once per stretch:** each stretch fires at most once, tracked by its start time, like the off-task stretch. Cooldown is per type and app.

### D4. Conversation identity
- **Where titles come from:** the engine notes a conversation each tick while the current session is messaging.
  - Web: the session's tab title.
  - Native: `last_window` from Retrace's status file, used only if `last_app` maps to the current app and `last_hash_at` is at most 30 s old.
- **Normalization:** strip trailing app names (" - Slack", " - Discord"), "N new item(s)", "(N)" unread counters, and whitespace; lowercase. A title that is then empty or just the app name is ignored.
- **Storage:** stored as a short SHA-256 prefix together with when it was first seen. The rule engine keeps these only for the current stretch and drops them when a new stretch starts.
- *Why Retrace rather than reading titles in the helper:* the helper would need Accessibility or Screen Recording, a new grant. Retrace already has both and records titles anyway.

### D5. Exemptions reuse existing machinery
- **Messaging tasks:** `alignment(cur, intent) == "on-task"` → no nudge. This covers comms-word tasks and user labels.
- **Legit task:** suppression is keyed by activity key and task, so the existing gate handles it.
- **No intent:** no nudge, as with off-task.
- **Calls:** with `calls_silence_others` on, only the in-call nudge may fire. Messaging during a huddle counts as team chat, which the in-call rule ignores, so nothing fires.

### D6. Copy and sound
- **Hopping:** title "Browsing {App} unreads?"; body is the task line, or "Pick one thing to answer, then head back."
- **Lingering:** title "Still in {App}?"; body is the task line, or "{N} min so far. Time to head back."
- **Sound:** `Purr` by default (`sounds.messaging`).
- **App names:** Slack, Discord, WhatsApp, Signal, Messages, Telegram.

## Risks / Trade-offs

- **"re" in task titles exempts messaging.** COMMS_WORDS includes "re", so a task like "Tom re LI post" exempts messaging during it. That's acceptable, since such tasks usually are conversations. Users can mark specific cases with feedback.
- **WhatsApp and Signal desktop can't be counted for hopping.** Their titles don't name the chat, so only lingering applies to them. Their web versions can be counted.
- **Coverage depends on Retrace.** Without Retrace, native hopping isn't detected. Lingering still works, and the retrace-supervision agent keeps Retrace running.
- **A fast hop can be missed.** Retrace captures on window change, but a very fast hop (under ~2 s per conversation) may skip a title. Hopping is then detected a little later, or lingering covers it.

## Migration Plan

New settings have defaults, so existing configs keep working. Restart `behaviornudge nudge run` to pick it up. To turn it off, set `messaging_apps = []`.
