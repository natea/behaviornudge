# Spec Delta

## MODIFIED Requirements

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
