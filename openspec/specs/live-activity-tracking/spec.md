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
