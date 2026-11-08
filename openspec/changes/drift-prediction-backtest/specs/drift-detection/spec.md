# Spec Delta

## Purpose

Finds drift episodes in a labeled timeline: purposeful visits to drift-prone sites that turn into off-task consuming activity. It also records what triggered each visit. These episodes are the ground truth that predictors are scored against.

## ADDED Requirements

### Requirement: Identify episode entries
An episode entry SHALL be any session on a drift-prone site whose purpose is lookup, reply or follow-link. The set of drift-prone sites SHALL be configurable and SHALL initially be LinkedIn, X and Instagram.

#### Scenario: LinkedIn message reply
- **WHEN** a session on `linkedin.com/messaging` has purpose `reply`
- **THEN** an episode entry is created at that session's start

#### Scenario: Aimless entry
- **WHEN** a session on `x.com/home` has purpose `browse`
- **THEN** no episode entry is created for it (it's consuming activity, not a purposeful visit)

#### Scenario: Site added by user
- **WHEN** the user adds `youtube.com` to the drift-prone list
- **THEN** purposeful YouTube sessions become episode entries on the next run

### Requirement: Classify episodes as drift or clean
An episode SHALL be a drift when, within 15 minutes of entry, at least 3 minutes of activity labeled both consuming and off-task (or consuming with no intent) occur before any on-task or creative session. Otherwise it SHALL be a clean visit. The window and threshold SHALL be configurable.

#### Scenario: Reply then feed
- **WHEN** the user replies on LinkedIn messaging for 2 minutes, then spends 6 minutes on `linkedin.com/feed` while the intended task is "Client tests"
- **THEN** the episode is a drift

#### Scenario: Reply then back to work
- **WHEN** the user replies on LinkedIn for 2 minutes, then returns to Zed on the intended project
- **THEN** the episode is a clean visit

#### Scenario: Feed is the task
- **WHEN** the user browses the LinkedIn feed while the intended task is "Alex re LI post" and the feed session is labeled on-task
- **THEN** that time does not count toward drift

### Requirement: Record episode details
Each episode SHALL record its entry time, site, purpose, intended task and source, outcome (drift or clean), minutes of drift, time from entry to the first drift session, and time until return to on-task work if any.

#### Scenario: Drift episode record
- **WHEN** an episode is classified as drift
- **THEN** its record includes drift minutes and the time from entry to the first consuming session

### Requirement: Record triggers
Each episode SHALL list the triggers present at entry: a calendar meeting that ended within the previous 5 minutes, a notification within the previous 2 minutes, a terminal agent in working state, or none. Triggers SHALL be stored as features and SHALL NOT affect the drift or clean outcome.

#### Scenario: After a meeting
- **WHEN** an entry happens 3 minutes after a calendar meeting ended
- **THEN** the episode lists the trigger `meeting-ended`

#### Scenario: Waiting on an agent
- **WHEN** an entry happens while the herdr log shows an agent in working state
- **THEN** the episode lists the trigger `agent-working`

### Requirement: Summarize episodes
The system SHALL produce a summary of episode counts, drift rate and total drift minutes, broken down by site, purpose, trigger, hour of day and weekday.

#### Scenario: Summary by site
- **WHEN** detection finishes
- **THEN** the summary shows the drift rate for each drift-prone site
