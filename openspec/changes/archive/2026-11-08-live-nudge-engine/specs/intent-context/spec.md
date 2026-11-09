# Spec Delta

## MODIFIED Requirements

### Requirement: Log live context going forward
The system SHALL provide a read-only logger that records, with timestamps, herdr focus changes, folder changes and agent status changes (working / idle / done) as they happen, the Sunsama active timer at least every 30 seconds, and call start and end events from call detection, to a local file.

#### Scenario: Agent starts working
- **WHEN** a herdr agent's status changes from idle to working
- **THEN** the log gains an entry with the time, pane, folder and new status within a few seconds

#### Scenario: Logger never changes anything
- **WHEN** the logger runs
- **THEN** it makes no change to herdr, Sunsama or Retrace state

#### Scenario: Call recorded in the log
- **WHEN** a Zoom call starts and ends
- **THEN** the log gains a call-start entry with the app and meeting name, and a call-end entry, each with its time
