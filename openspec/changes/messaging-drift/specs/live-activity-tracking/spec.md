# Spec Delta

## ADDED Requirements

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
