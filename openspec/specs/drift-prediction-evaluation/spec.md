# drift-prediction-evaluation Specification

## Purpose

Compares swappable drift predictors on the user's historical drift episodes, under identical conditions and without access to future data. It produces a report that decides which predictor the live nudge engine should use.

## Requirements

### Requirement: Common predictor contract
Every predictor SHALL accept the sessions that ended before a decision time, the decision time, the intended task, and the activity key of the session just starting at that time. It SHALL return a drift probability for the drift window, a ranked list of likely next activity keys with scores, a predictor identifier and the prediction latency. Predictors SHALL be selectable by name from configuration.

#### Scenario: Arrival site is known
- **WHEN** a predictor is queried at a LinkedIn messaging entry that started at 14:02 and has not ended
- **THEN** it receives `linkedin.com/messaging` as the current activity key, and that session is not in its history

#### Scenario: New predictor added
- **WHEN** a new predictor that meets the contract is registered
- **THEN** it can be evaluated with the same report and episodes, with no change to timeline, labeling or detection behavior

### Requirement: Provide two predictors
The evaluation SHALL include BehaviorGPT and a local baseline that predicts from the user's own history using current activity, previous activity, hour of day and weekday.

#### Scenario: Both predictors scored
- **WHEN** an evaluation run completes
- **THEN** the report contains results for both BehaviorGPT and the baseline on the same episodes

### Requirement: No look-ahead
At each decision point, a predictor SHALL receive only sessions that ended before the decision time. Predictors trained on history SHALL use only data from weeks before the test week.

#### Scenario: Future session withheld
- **WHEN** a predictor is queried at an episode entry at 14:02
- **THEN** the history it receives has no session that ends after 14:02

#### Scenario: Rolling weeks
- **WHEN** week 6 is being tested
- **THEN** the baseline has been trained only on weeks 1–5

### Requirement: Score at episode entries
Each predictor SHALL be queried at every episode entry. Its drift probability SHALL be compared with the episode outcome.

#### Scenario: Every episode scored
- **WHEN** the test period contains 120 episodes
- **THEN** each predictor produces 120 scored predictions, or the report lists the failed ones with reasons

### Requirement: Report comparable metrics
The report SHALL include, per predictor and per test week:
- ROC-AUC and PR-AUC;
- precision and recall at a threshold that yields at most 5 nudges per working day;
- median warning lead time;
- false alarms per day;
- mean prediction latency;
- results by trigger type, hour of day, and intent source (timer / planned / calendar / recent-creative / none).

#### Scenario: Breakdown by intent source
- **WHEN** most episodes have intent source `recent-creative` or `none` and few have `timer`
- **THEN** the report shows each predictor's metrics separately for each intent source, with the episode count for each

#### Scenario: Threshold respects nudge budget
- **WHEN** the report gives precision and recall for a predictor
- **THEN** the threshold used produces no more than 5 predicted drifts per working day in the test period

### Requirement: State a decision
The report SHALL recommend BehaviorGPT for live use only if it beats the baseline on PR-AUC in most test weeks with latency suitable for live nudges. Otherwise it SHALL recommend the baseline. Either way it SHALL state the reason.

#### Scenario: Baseline wins
- **WHEN** the baseline has higher PR-AUC in most test weeks
- **THEN** the report recommends the baseline and records BehaviorGPT's results and the gap

### Requirement: Evaluate prediction at trigger moments
The evaluation SHALL also score, separately, whether each predictor's drift probability at trigger moments (meeting ended, notification, agent working) anticipates a drift-prone visit before entry.

#### Scenario: Pre-entry prediction
- **WHEN** a meeting ends at 15:00 and the user enters LinkedIn at 15:02 and drifts
- **THEN** the separate pre-entry analysis includes the prediction made at 15:00 and its outcome

### Requirement: Degrade gracefully on predictor failure
When a cloud predictor fails or times out for a decision point, the run SHALL record the failure and continue. Failures SHALL be reported per predictor and excluded from metrics, not counted as zeros.

#### Scenario: BehaviorGPT outage
- **WHEN** BehaviorGPT returns errors for 10 of 120 decision points
- **THEN** the report shows 110 scored predictions for BehaviorGPT, lists 10 failures, and the baseline is still scored on all 120

### Requirement: Same privacy limits for predictors
Data sent to any cloud predictor SHALL follow the same allowed-fields limits and audit logging as labeling.

#### Scenario: BehaviorGPT request contents
- **WHEN** the BehaviorGPT predictor sends a history
- **THEN** it contains only activity keys and timestamps, and the request appears in the audit log
