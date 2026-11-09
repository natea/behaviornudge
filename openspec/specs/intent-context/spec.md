# intent-context Specification

## Purpose

Works out, for any moment, what the user was supposed to be doing (their intended task and project) and which project a terminal session belongs to, so actual activity can be compared with stated intent.

## Requirements

### Requirement: Resolve the intended task with a fallback order
For any timestamp, the system SHALL resolve intent from the first available source: a Sunsama timer session covering that time, then a Sunsama task planned for that slot, then a calendar event covering that time, then the most recent creative project within a configurable window (default 30 minutes), otherwise "no intent". Each result SHALL record its source.

#### Scenario: Timer running
- **WHEN** a Sunsama timer for "Working on BehaviorNudge at Sundai" covers 13:50 local time
- **THEN** the intended task at 13:50 is that task, with source `timer`

#### Scenario: No timer, planned slot
- **WHEN** no timer is running at 14:15 and "Jordan re product feedback" is planned for 14:10–14:30
- **THEN** the intended task at 14:15 is that task, with source `planned`

#### Scenario: Calendar event, no Sunsama
- **WHEN** there is no timer or planned task at 11:10, and the user's calendar has "Client sync" from 11:00 to 11:30
- **THEN** the intended activity at 11:10 is "Client sync", with source `calendar`

#### Scenario: Nothing planned
- **WHEN** there is no timer, planned task or calendar event, and the user was in a creative session on project `behaviornudge` 10 minutes earlier
- **THEN** the intended project is `behaviornudge`, with source `recent-creative`

#### Scenario: Window is configurable
- **WHEN** the recent-creative window is set to 60 minutes and the last creative session ended 45 minutes earlier
- **THEN** that session's project is the intended project, whereas with the default 30-minute window the result is "no intent"

#### Scenario: Sunsama not used that day
- **WHEN** a day has no Sunsama timer sessions or planned tasks
- **THEN** intent for that day is resolved from calendar and recent-creative sources without error, and the intent-source coverage report shows zero Sunsama coverage for that day

### Requirement: Calendar data is provider-independent
The calendar source SHALL read timed events through a read-only source and SHALL use only calendars on a configurable allowlist, initially "Nate Aune (natejaune)". Events from other calendars and all-day events SHALL be ignored for intent and triggers. When the source reports declined events, they SHALL be ignored too.

#### Scenario: All-day event ignored
- **WHEN** an all-day "Conference" event covers a whole day
- **THEN** it does not become the intended activity for moments on that day

#### Scenario: Calendar not on the allowlist
- **WHEN** an event on the "Meditation" calendar covers 07:00–07:20 and there is no timer or planned task
- **THEN** that event is ignored, and intent at 07:10 falls through to recent creative work or "no intent"

#### Scenario: Allowlisted calendar
- **WHEN** an event on "Nate Aune (natejaune)" covers 11:00–11:30
- **THEN** it is used as the intended activity for that time, with source `calendar`

#### Scenario: Source without declined status
- **WHEN** the calendar source does not report whether an event was declined
- **THEN** the event is used as intent, and the intent-source coverage report notes that declined events could not be filtered

### Requirement: Cap forgotten timers
A timer session SHALL count as intent only while activity related to that task's project has been seen within the previous 45 minutes. After that, the moment SHALL resolve as if no timer were running.

#### Scenario: Timer left running overnight
- **WHEN** a timer runs from 15:00 to 08:11 the next day, and no related activity occurs after 15:40
- **THEN** the timer counts as intent only until 16:25 (45 minutes after the last related activity)

### Requirement: Report corrected time per task
The system SHALL report, for each timed Sunsama task, both the time Sunsama recorded and the capped time during which related activity was actually seen.

#### Scenario: Inflated timer
- **WHEN** Sunsama records 1,031 minutes on a task but related activity was seen for 25 of those minutes
- **THEN** the report lists 1,031 recorded minutes and 25 corrected minutes for that task

### Requirement: Resolve terminal project from the focused herdr pane
For live and logged terminal activity, the system SHALL take the project from the folder of herdr's focused pane, only while the terminal hosting herdr is the frontmost app. Terminal window titles SHALL NOT be used when herdr data is available.

#### Scenario: Window title disagrees with herdr
- **WHEN** Ghostty is frontmost, its window title says `parental-controls`, and herdr's focused pane is in `~/Developer/behaviornudge`
- **THEN** the terminal project is `behaviornudge`

#### Scenario: Terminal not frontmost
- **WHEN** Chrome is frontmost while herdr still reports a focused pane in `behaviornudge`
- **THEN** the current project is not taken from herdr

### Requirement: Map folders to projects
The system SHALL map a git worktree folder to its main repository, and a repository to a Sunsama channel through a user-editable mapping. Unmapped repositories SHALL resolve to their repository name.

#### Scenario: Worktree folder
- **WHEN** the focused folder is a worktree named `worktree-lucky-meadow-8c6c` of repository `personal-ai-os`
- **THEN** the project resolves to `personal-ai-os` (or its mapped Sunsama channel)

### Requirement: Resolve historical terminal projects best-effort
For terminal activity recorded before herdr logs exist, the system SHALL infer the project from captured screen text and captions matched against known repository names, and SHALL mark such projects as inferred.

#### Scenario: Old terminal session
- **WHEN** a terminal session from October has no herdr log, and its captured text names `newenglandmusic`
- **THEN** its project is `newenglandmusic`, marked as inferred

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
