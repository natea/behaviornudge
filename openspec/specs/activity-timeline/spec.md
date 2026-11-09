# activity-timeline Specification

## Purpose

Combines the user's local activity sources into one normalized, time-ordered timeline of sessions, so labeling, drift detection and prediction all work from the same record of what the user was doing.

## Requirements

### Requirement: Read local sources without modifying them
The system SHALL build the timeline from the jhammant Retrace database, the retrace.to database, and previously exported Sunsama and herdr logs, opening every source read-only and never writing to them.

#### Scenario: Source databases are untouched
- **WHEN** a timeline build completes
- **THEN** the modification time and contents of both Retrace databases are unchanged

#### Scenario: Missing source
- **WHEN** one source is unavailable (e.g., the retrace.to database is absent)
- **THEN** the build completes from the remaining sources and the build summary lists the missing source and the time range it would have covered

### Requirement: Normalize records to activity keys
The system SHALL map every raw record to an activity key: domain plus path class for browser pages, the app for native apps, and app plus project for terminals and editors. Full URLs, query strings and screen text SHALL NOT appear in the activity key.

#### Scenario: Browser page
- **WHEN** a record has URL `https://www.linkedin.com/feed/?trk=nav`
- **THEN** its activity key is `linkedin.com/feed`

#### Scenario: Same site, different behavior
- **WHEN** one record is on `linkedin.com/messaging/thread/123` and another on `linkedin.com/feed`
- **THEN** they receive different activity keys (`linkedin.com/messaging` and `linkedin.com/feed`)

#### Scenario: Native app
- **WHEN** a record has bundle id `dev.zed.Zed` and no URL
- **THEN** its activity key identifies the app (`zed`) plus its project when one is resolved

### Requirement: Merge records into sessions
The system SHALL merge consecutive records with the same activity key into one session when the gap between them is under 30 seconds. Each session SHALL have a start time, end time, duration, activity key and contributing sources. Activity shorter than 3 seconds SHALL be kept as a transition, not a session.

#### Scenario: Rapid records collapse
- **WHEN** five records with key `x.com/home` occur 4–10 seconds apart
- **THEN** the timeline contains one `x.com/home` session spanning all five

#### Scenario: Brief blip
- **WHEN** a 2-second record of `slack` sits between two `zed` records
- **THEN** it is recorded as a transition and does not appear as a session

### Requirement: Resolve overlapping sources by precedence
When sources cover the same time span, the system SHALL keep a single session for that span, preferring sources with exact durations and then sources with more context fields. Visit-only sources without durations SHALL only refine which page a timed browser session was on.

#### Scenario: Both Retraces cover the same minute
- **WHEN** retrace.to and jhammant Retrace both record Chrome activity at the same time
- **THEN** the timeline contains one session for that time, not two, and total time for that minute is not double-counted

#### Scenario: Chrome history without durations
- **WHEN** a Chrome history visit to `x.com/home` falls inside a timed Chrome session with no URL
- **THEN** that session's activity key becomes `x.com/home`, and the visit itself adds no duration

### Requirement: Store times consistently in the user's timezone context
The system SHALL store all session times as UTC and SHALL record the user's timezone (America/New_York) so hour-of-day and weekday are computed in local time.

#### Scenario: Local hour derived correctly
- **WHEN** a session starts at 18:46 UTC on 2026-11-08
- **THEN** its local hour is 13 (1 PM, America/New_York)

### Requirement: Reproducible builds
Rebuilding the timeline from the same source data SHALL produce identical sessions.

#### Scenario: Rebuild is identical
- **WHEN** the timeline is built twice from unchanged sources
- **THEN** both builds contain the same sessions with the same keys, times and sources
