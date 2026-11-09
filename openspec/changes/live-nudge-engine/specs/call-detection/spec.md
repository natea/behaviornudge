# Spec Delta

## Purpose

Tells the engine when the user is in a call, from which call apps are using the microphone, without recording or listening to any audio. Meetings can then be treated as the intent and losing presence in them can be nudged.

## ADDED Requirements

### Requirement: Detect calls from microphone use by call apps
The system SHALL consider a call active when a process on the call-app allowlist is capturing microphone input, checking at least every 2 seconds. A call SHALL be considered ended 45 seconds after the last such process stops, so brief mutes don't split one call.

#### Scenario: Zoom call starts
- **WHEN** Zoom starts capturing the microphone
- **THEN** within 5 seconds the call state is active with app Zoom

#### Scenario: Muting briefly
- **WHEN** the user mutes for 20 seconds during a call
- **THEN** the call stays active

#### Scenario: Dictation is not a call
- **WHEN** a non-allowlisted app (e.g. a dictation tool) uses the microphone
- **THEN** no call is detected

### Requirement: Never record audio
Call detection SHALL only read which processes are capturing input. It SHALL NOT open, record, store or transmit any audio.

#### Scenario: Detection only
- **WHEN** a call is detected
- **THEN** no audio stream is opened and no audio data exists on disk

### Requirement: Configurable call-app allowlist
The allowlist SHALL default to Zoom, Teams, Slack, FaceTime, WhatsApp, Webex, Discord and the supported browsers (for Meet and similar), matched by bundle-id prefix. The user SHALL be able to add or remove entries.

#### Scenario: Adding an app
- **WHEN** the user adds `com.example.callapp` to the allowlist
- **THEN** microphone use by that app counts as a call

### Requirement: Name the meeting when possible
While a call is active, the system SHALL attach the overlapping allowlisted calendar event's title, if any, as the call's name.

#### Scenario: Scheduled meeting
- **WHEN** a call is active during the calendar event "New England Music project"
- **THEN** the call's name is "New England Music project"
