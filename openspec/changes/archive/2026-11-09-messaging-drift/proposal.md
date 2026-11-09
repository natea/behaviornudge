# Proposal

## Why

Feeds aren't the only shiny object. Opening Slack, Discord, WhatsApp or Signal and working through unread messages is another way to procrastinate, and today behaviornudge can't see it. Messaging is labeled *reactive*, and every drift nudge looks for *consuming* activity, so an hour of hopping between channels during a work block never triggers a nudge.

Messaging is also often the task itself. Replying to one person is a discrete task; working through every unread channel is scrolling by another name. The rules need to tell those two apart and only nudge on the second.

## What Changes

- **A new *messaging* nudge** fires during a known task, when the current messaging activity isn't the task, in either of two cases:
  - **Hopping:** the user has opened 4 or more different conversations in one messaging stretch, and spent at least 60 s on it. This is the "checking the unreads" pattern.
  - **Lingering:** the stretch has lasted 5 minutes.
- **What counts as a messaging stretch:** time in Slack, Discord, WhatsApp, Signal, Messages or Telegram, native or web. Moving between messaging apps continues the stretch; 60 s back at other work ends it.
- **A quick task stays quiet:** opening one conversation, replying and leaving never triggers it.
- **Exemptions:** no nudge when the task at hand is about messaging, which already counts as on-task (e.g. "Reply to Chris", anything with "message", "slack", "inbox", "re …"). Clicking "Legit task" suppresses it for that app during that task. During a call (e.g. a Slack huddle) the call rules apply instead.
- **How conversations are counted:** for web messaging, from the browser tab title. For native apps, from the window title that jhammant Retrace records with each capture, read from its local status file. No new macOS permission is needed.
  - Titles are normalized: unread counters such as "3 new items" or "(2)" are stripped.
  - Titles are held only in memory, as hashes, for the current stretch. They're never stored, shown or sent.
  - Apps whose window title doesn't name the conversation (WhatsApp and Signal desktop) still get the lingering rule.
- **Notification copy:**
  - Hopping: "Browsing Slack unreads?"
  - Lingering: "Still in Slack?"
  - Both name the task at hand and use their own gentle default sound, Purr.
- **New `[nudge]` settings:** `messaging_apps`, `messaging_dwell_min` (5), `messaging_hop_conversations` (4) and `messaging_hop_min_s` (60), plus `sounds.messaging`. "Too soon" lengthens the lingering wait the same way it does for other nudges.
- **Out of scope:**
  - Counting messaging drift in the backtest's drift episodes and report. Retrace has historical window titles, so this is a possible follow-up.
  - Reading message content or detecting typing.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `nudge-rules`: adds the messaging nudge (hopping and lingering, with its exemptions) and its place among the other moments.
- `live-activity-tracking`: adds conversation identity for messaging apps, taken from tab titles and Retrace's latest window title, kept in memory only.
- `nudge-delivery`: messaging becomes a nudge type with its own configurable sound (default Purr).

## Impact

- **Code:** `nudge/rules.py` (new moment), `nudge/settings.py` (new settings and sound), `nudge/content.py` (copy), and `nudge/engine.py` (feeds conversation titles to the rules each tick). The CLI's `nudge test` gains `messaging`.
- **Config and docs:** `behaviornudge.example.toml`, the README's live-nudges table, and the configuration page (regenerated).
- **No new permissions or outbound data.** Conversation titles never leave the process. The activity key stays the app name (e.g. `slack`), as it is today.
