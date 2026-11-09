# Spec Delta

## Purpose

Decides, in transparent code, when a nudge fires: on arrival at a drift-prone site, when a visit's purpose is done, when off-task time grows, and at trigger moments (in a call, or while an agent works). It also enforces the user's controls: on/off, snooze, quiet hours, cooldowns and a daily budget.

## ADDED Requirements

### Requirement: Arrival nudge
An arrival nudge SHALL fire when a known intent exists that the current activity is not aligned with, and either the user has been on a drift-prone site for at least the arrival delay (default 20 s) for a purpose (lookup, reply, follow-link), or has landed directly on a drift-prone site's consuming page (e.g. a feed) for at least the feed-landing delay (default 45 s).

#### Scenario: Replying during a work block
- **WHEN** the intent is "Client tests" and the user has been on `linkedin.com/messaging` for 20 s
- **THEN** an arrival nudge fires naming "Client tests"

#### Scenario: Site is the task
- **WHEN** the intent is "Alex re LI post" and the user is on `linkedin.com/messaging`
- **THEN** no arrival nudge fires

#### Scenario: Landing straight on the feed
- **WHEN** the intent is "Prep for Sundai demo" and the user opens `linkedin.com/feed` directly and stays 45 s
- **THEN** an arrival nudge fires naming "Prep for Sundai demo", without waiting for the off-task threshold

#### Scenario: Feed hopping on the same site counts together
- **WHEN** the user moves between `linkedin.com/feed` and `linkedin.com/post` for a combined 45 s during a known task
- **THEN** one arrival nudge fires

### Requirement: Purpose-done nudge
A purpose-done nudge SHALL fire when, within 15 minutes after a purposeful visit, the user has been on consuming, off-task or no-intent activity for at least the purpose-done delay (default 30 s).

#### Scenario: Message sent, then the feed
- **WHEN** the user replies on LinkedIn messaging, then spends 30 s on `linkedin.com/feed` with intent "Client tests"
- **THEN** a purpose-done nudge fires offering a return to "Client tests"

### Requirement: Off-task nudge
An off-task nudge SHALL fire when consuming, off-task time within one stretch reaches the off-task threshold (default 8 minutes) while a known intent exists, whether or not the stretch began with a purposeful visit. Returning to work for at least 60 s ends the stretch.

#### Scenario: Aimless scrolling
- **WHEN** the user opens `x.com/home` directly and stays 8 minutes during a calendar block "Deep Work — Client Projects"
- **THEN** an off-task nudge fires naming that block

#### Scenario: No intent
- **WHEN** the user scrolls for 20 minutes with no known intent
- **THEN** no off-task nudge fires

### Requirement: In-call nudge
An in-call nudge SHALL fire when a call is active and the user has been away from it for the in-call delay (default 30 s): on consuming activity, or reactive activity on a drift-prone site (e.g. LinkedIn messages). Call-app chat, team chat and work tools SHALL NOT count. It fires once per stretch and can fire again after 60 s back in the call or at work, subject to the cooldown.

#### Scenario: Browsing during a meeting
- **WHEN** a Zoom call is active, the calendar event is "New England Music project", and the user has browsed `x.com/home` for 30 s
- **THEN** an in-call nudge fires naming "New England Music project"

#### Scenario: Messaging on LinkedIn during a meeting
- **WHEN** a Zoom call is active and the user has been on `linkedin.com/messaging` for 30 s
- **THEN** an in-call nudge fires

#### Scenario: Other nudges allowed during calls, silently
- **WHEN** `calls_silence_others` is off, a call is active, and the user has been on `linkedin.com/messaging` for 20 s with a known task
- **THEN** the arrival nudge fires, and it plays no sound

#### Scenario: Team chat during a call stays quiet
- **WHEN** a call is active and the user is in Slack or the call app's chat
- **THEN** no in-call nudge fires

#### Scenario: A second drift in the same call
- **WHEN** an in-call nudge fired, the user returned to the call for 60 s, and 15 minutes later starts scrolling Instagram for 30 s in the same call
- **THEN** another in-call nudge fires

#### Scenario: Call with no calendar event
- **WHEN** a call is active with no matching calendar event
- **THEN** the in-call nudge names the call app (e.g. "your Zoom call")

### Requirement: Calls silence the other nudges
While a call is active, the call SHALL be the intent: arrival, purpose-done, off-task and agent-working nudges SHALL NOT fire, so only the silent in-call nudge can appear. A setting (`calls_silence_others`, default on) SHALL allow them during calls; any nudge shown during a call SHALL play no sound.

#### Scenario: No audible nudges during a call
- **WHEN** a call is active and the user lands on `linkedin.com/messaging`, which would otherwise trigger a 20 s arrival nudge
- **THEN** no arrival nudge fires, and after 30 s the silent in-call nudge fires instead

### Requirement: Agent-working nudge
An agent-working nudge SHALL fire when a herdr agent has been working for at least 30 s and the user has been on consuming, off-task or no-intent activity for at least the agent delay (default 2 minutes). The nudge SHALL name the agent's project.

#### Scenario: Waiting on Claude
- **WHEN** a Claude agent in `behaviornudge` starts working and the user scrolls Instagram for 2 minutes
- **THEN** an agent-working nudge fires naming `behaviornudge`

### Requirement: Respect controls and limits
No nudge SHALL fire while nudges are switched off, snoozed, within quiet hours, after the daily budget (default 8) is used, or within a moment's cooldown (default 15 minutes per moment and site). Suppressed nudges SHALL be logged with the reason. All delays, thresholds, budgets, cooldowns and quiet hours SHALL be configurable.

#### Scenario: Budget used
- **WHEN** 8 nudges have fired today and a ninth moment occurs
- **THEN** no notification is shown and the log records `suppressed: budget`

#### Scenario: Snoozed
- **WHEN** the user snoozed for 30 minutes 10 minutes ago
- **THEN** no nudge fires for the next 20 minutes

### Requirement: Score each moment in shadow mode
At each nudge moment, fired or suppressed, the configured predictor SHALL be asked for a drift probability through the predictor contract and egress gate. The score SHALL be logged and SHALL NOT affect whether the nudge fires. A predictor failure SHALL NOT delay or block the nudge.

#### Scenario: Predictor unavailable
- **WHEN** BehaviorGPT times out at an arrival moment
- **THEN** the nudge fires as the rules decided, and the log records the predictor failure
