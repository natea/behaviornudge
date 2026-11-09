# Tasks

## 1. Settings, copy and sound

- [x] 1.1 Add `messaging` to nudge types with settings `messaging_apps`, `messaging_dwell_min` (5), `messaging_hop_conversations` (4), `messaging_hop_min_s` (60) and default sound Purr; update the example config; verify with the settings tests (defaults match the example)
- [x] 1.2 Add hopping and lingering copy to `content.build` and a `messaging` sample for `nudge test`; verify with content tests

## 2. Conversation identity

- [x] 2.1 Implement title normalization and hashing (strip app suffix, unread counters; ignore bare app names); verify with tests for Slack, Discord, Signal and web titles
- [x] 2.2 Engine: each tick, while the current session is messaging, note the conversation from the tab title (web) or Retrace's fresh `last_window` for the same app (native, ≤ 30 s); verify with a test using a fake status file, including stale and missing files

## 3. Messaging moment

- [x] 3.1 Rule engine: messaging stretch across apps (60 s return ends it), hopping and lingering triggers, once per stretch, precedence after arrival; verify with tests for every nudge-rules scenario (unreads, quick reply, lingering, cross-app, task is a reply, huddle, no intent)
- [x] 3.2 "Legit task" and "Too soon" work for messaging (suppression by key, multiplier on lingering); verify with tests

## 4. Docs and live check

- [x] 4.1 README live-nudges table and the configuration page (regenerate) include the messaging nudge
- [ ] 4.2 Live check: with a known task, hop through 4 Slack channels and confirm the nudge appears; run the full test suite
