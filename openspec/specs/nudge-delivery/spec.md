# nudge-delivery Specification

## Purpose

Delivers nudges as native macOS notifications from a small helper app. Each one names the task at hand, plays a sound chosen for that kind of nudge, offers feedback buttons, and reports the user's choice back to the engine.

## Requirements

### Requirement: Native notification with the task at hand
Each nudge SHALL appear as a macOS notification from an app named "behaviornudge". The title SHALL state what happened ("8 min on X") and the body SHALL name the task at hand and its source (Sunsama timer, Sunsama timebox, calendar block, call, or agent project).

#### Scenario: Timer task named
- **WHEN** a purpose-done nudge fires with intent "Client tests" from the Sunsama timer
- **THEN** the notification body names "Client tests" and says it is the current Sunsama task

### Requirement: Feedback buttons
Each notification SHALL offer actions: **Back to task**, **Too soon**, **Legit task**, and **Snooze 30 min**. Clicking one SHALL report the nudge id and action to the engine. Clicking the notification body SHALL count as acknowledging it.

#### Scenario: Button clicked
- **WHEN** the user clicks **Too soon** on nudge 42
- **THEN** the engine receives `action: too_soon` for nudge 42 within a few seconds

#### Scenario: Notification dismissed
- **WHEN** the user dismisses the notification without choosing an action
- **THEN** the engine records the nudge as dismissed

### Requirement: Sounds per nudge type
Each nudge type (arrival, purpose-done, off-task, in-call, agent-working, messaging) SHALL play a configurable sound: a macOS system sound name, a sound file, or none. In-call nudges SHALL default to no sound so other participants don't hear them. Messaging nudges SHALL default to a gentle sound (Purr).

#### Scenario: Silent in calls
- **WHEN** an in-call nudge fires with default settings
- **THEN** the notification appears without a sound

#### Scenario: Custom sound
- **WHEN** the user sets the off-task sound to `Sosumi`
- **THEN** off-task nudges play Sosumi

#### Scenario: Gentle messaging sound
- **WHEN** a messaging nudge fires with default settings
- **THEN** it plays Purr

### Requirement: System-level control
Because nudges come from their own app, the user SHALL be able to turn them off, change their style, or silence them in System Settings → Notifications → behaviornudge, and macOS Focus modes SHALL apply. The engine SHALL detect when notification permission is denied and report it rather than failing silently.

#### Scenario: Permission denied
- **WHEN** notification permission for behaviornudge is off
- **THEN** the engine logs that nudges cannot be shown and `behaviornudge nudge status` reports it

### Requirement: Engine–helper protocol
The engine and helper SHALL exchange newline-delimited JSON over the helper's standard input and output: the engine sends `notify` and `withdraw` commands; the helper sends `ready`, `action`, `dismissed`, `front_app` and `calls` events. If the helper exits, the engine SHALL restart it and SHALL NOT crash.

#### Scenario: Helper restarted
- **WHEN** the helper process is killed
- **THEN** the engine restarts it within 10 seconds and logs the restart
