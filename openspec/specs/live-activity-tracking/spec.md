# live-activity-tracking Specification

## Purpose

Keeps a real-time picture of what the user is doing: the front app, the active browser page, the terminal project, call state and intent. It produces live sessions labeled with the same activity keys, labels and intent rules as the backtest, so nudge rules can react within seconds.

## Requirements

### Requirement: Observe the current activity within seconds
The system SHALL detect the front app on every app switch and the active browser tab's URL at least every 2 seconds while a supported browser is frontmost. It SHALL update the current activity key within 5 seconds of a change.

#### Scenario: Landing on LinkedIn messages
- **WHEN** the user switches Chrome tabs from Zed docs to `https://www.linkedin.com/messaging/thread/1/`
- **THEN** within 5 seconds the current activity key is `linkedin.com/messaging`

#### Scenario: Unsupported browser
- **WHEN** the front app is a browser whose URL cannot be read
- **THEN** the current activity key is the browser app (e.g. `firefox`) and no error interrupts tracking

### Requirement: Use the same keys, labels and intent as the backtest
Live sessions SHALL be keyed, merged and labeled with the same activity-key rules, session rules, labeling layers and intent resolution as the historical timeline, so live nudges and backtests agree.

#### Scenario: Live label matches backtest label
- **WHEN** a live session on `x.com/home` is labeled
- **THEN** it receives the same mode and purpose the backtest labeler would give that activity

### Requirement: Resolve live intent from current sources
Live intent SHALL use the Sunsama active timer (polled), the Sunsama timebox or allowlisted calendar event covering now, then recent creative work. The current intent and its source SHALL be available to nudge rules.

#### Scenario: Timer started mid-session
- **WHEN** the user starts a Sunsama timer for "Client tests"
- **THEN** within 30 seconds the live intent is "Client tests" with source `timer`

#### Scenario: Calendar block in progress
- **WHEN** no timer runs and the calendar block "Deep Work — Client Projects" covers now
- **THEN** the live intent is that block with source `calendar`

### Requirement: Label unknown activities without blocking
Activities with no rule, cached or user label SHALL be tracked immediately with no mode. When the Jev layer is enabled, they SHALL be labeled in the background through the egress gate, and the label SHALL apply once it arrives.

#### Scenario: New site
- **WHEN** the user opens a site never seen before
- **THEN** tracking continues at once, no nudge depends on its mode until a label exists, and a background label request is queued

### Requirement: Track only while the Mac is in use
The system SHALL pause tracking and rules while the screen is locked, asleep, or idle for more than 5 minutes, and resume on activity.

#### Scenario: Screen locked
- **WHEN** the screen locks with `x.com/home` frontmost
- **THEN** no off-task time accumulates while locked

### Requirement: Tell conversations apart in messaging apps
While a messaging app is frontmost, the system SHALL identify the current conversation from the browser tab title (web messaging) or from the latest window title recorded by jhammant Retrace for that app (native messaging, used only when recorded within the last 30 s). Titles SHALL be normalized to drop unread counters, held in memory only as hashes for the current stretch, and never stored, displayed or sent.

#### Scenario: Slack channels
- **WHEN** Retrace records "general (Channel) - Acme - 1 new item - Slack" and then "general (Channel) - Acme - Slack"
- **THEN** both count as the same conversation

#### Scenario: No conversation in the title
- **WHEN** the frontmost app is Signal and its window title is "Signal (2)"
- **THEN** no conversation is identified, and only the lingering rule can apply

#### Scenario: Retrace not running
- **WHEN** Retrace's status file is missing or older than 30 s
- **THEN** tracking continues without conversation identity for native apps

### Requirement: Read tabs from the browser window in front
When a browser's tab is read by script, the system SHALL check the tab title against the title of the frontmost window of the frontmost process. If they don't match, the answer came from another copy of that browser (for example an automation browser): the URL SHALL be ignored, and the user SHALL be told once per hour, in a notification without nudge buttons, that another copy of the browser is open.

#### Scenario: Automation browser open
- **WHEN** a second Google Chrome is running and the tab read returns a title that isn't in the front window's title
- **THEN** that URL is not recorded and one notice names the problem

#### Scenario: Normal browsing
- **WHEN** the front window's title contains the tab title that was read
- **THEN** the URL and title are used as before

#### Scenario: Window title unavailable
- **WHEN** the front window title can't be read (no Accessibility permission, no window)
- **THEN** the tab read is used as before
