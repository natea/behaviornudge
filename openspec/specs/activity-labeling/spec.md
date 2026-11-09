# activity-labeling Specification

## Purpose

Labels every timeline session with mode, purpose, project and alignment with the intended task, cheaply and explainably, so drift episodes can be identified and predictors can be scored.

## Requirements

### Requirement: Label every session on four dimensions
Every session SHALL receive a **mode** (creative / reactive / consuming), a **purpose** (lookup / reply / follow-link / browse, or n/a for non-web work such as coding), a **project** (a Sunsama channel or `none`), and an **alignment** with the intended task (on-task / supporting / off-task, or `no-intent` when there is none). Each label SHALL record its source (rule, cache, model or user) and, for model labels, its confidence.

#### Scenario: Labeled session
- **WHEN** labeling finishes
- **THEN** every session has all four labels, each with a recorded source

#### Scenario: Coding session purpose
- **WHEN** a session is in Zed or a terminal on a project
- **THEN** its purpose is `n/a`

#### Scenario: No intent
- **WHEN** a session has no intended task
- **THEN** its alignment is `no-intent`, not off-task

### Requirement: Apply labeling layers in order
The system SHALL try user-defined rules first, then previously stored labels for the same activity key, trimmed title and intended task, and only then the external classifier. A session labeled by an earlier layer SHALL NOT be sent to a later one.

#### Scenario: Rule match
- **WHEN** a session's activity key is `x.com/home` and a rule labels it consuming
- **THEN** the session is labeled from the rule and no external request is made for its mode

#### Scenario: Cache hit
- **WHEN** a session matches a previously labeled activity key, trimmed title and intended task
- **THEN** it reuses the stored labels without an external request

### Requirement: Use the user's own definitions of modes
The mode options sent to the classifier SHALL carry the user's definitions: creative = writing, building or making something; reactive = replying, communicating or looking something up; consuming = scrolling feeds and browsing for novelty.

#### Scenario: Definitions are user-editable
- **WHEN** the user edits the definition of "creative" in configuration
- **THEN** later classifier requests use the edited definition and the label cache distinguishes labels made under the old definition

### Requirement: Route by the probability of the chosen label
Model mode labels whose chosen option has probability ≥ 0.9 SHALL be accepted; 0.5 up to 0.9 SHALL be accepted and added to a review sample. Below 0.5, or when the classifier says nothing fits, the item SHALL go to the user's review queue and its mode SHALL stay unlabeled until answered. Thresholds SHALL be configurable.

#### Scenario: Low probability
- **WHEN** the classifier labels a YouTube session creative with probability 0.4
- **THEN** the session appears in the user's review queue and its mode stays unlabeled until the user answers

#### Scenario: User answer is reused
- **WHEN** the user labels a queued session as consuming
- **THEN** that answer is stored and applied to matching future sessions without another external request

### Requirement: Pin the classifier version
All model labels in one backtest SHALL come from a single, explicitly named classifier version, recorded with each label together with a fingerprint of the prompt and options used.

#### Scenario: Version recorded
- **WHEN** a session is labeled by the classifier
- **THEN** its label record includes the model version name and the prompt fingerprint

### Requirement: Limit what leaves the Mac
Requests to any external service SHALL contain only the allowed fields: activity key, path class, title trimmed to 80 characters, caption trimmed to 500 characters, previous activity key, Sunsama channel, and a trimmed task title. Screen text, full URLs, query strings, screenshots and HTML SHALL NEVER be sent. A channel-only mode SHALL omit task titles entirely.

#### Scenario: Disallowed field
- **WHEN** a request is built for a session whose source record contains full screen text and a URL with a query string
- **THEN** the outgoing request contains neither the screen text nor the full URL

#### Scenario: Channel-only mode
- **WHEN** channel-only mode is on
- **THEN** outgoing requests include the Sunsama channel but no task title

### Requirement: Audit outbound requests
Every external request SHALL be written to a local audit log with time, destination and the exact fields sent.

#### Scenario: Inspecting what was sent
- **WHEN** the user opens the audit log after a labeling run
- **THEN** each external request is listed with its destination and payload fields

### Requirement: Keep secrets out of code and output
API keys SHALL be read only from environment variables or a local, git-ignored environment file. They SHALL NOT appear in source files, logs, reports or the audit log.

#### Scenario: Key missing
- **WHEN** the classifier's API key is not set
- **THEN** labeling stops before any external request, reports which variable is missing, and rule and cache labels already made are kept
