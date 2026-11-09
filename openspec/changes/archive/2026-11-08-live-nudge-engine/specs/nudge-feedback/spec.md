# Spec Delta

## Purpose

Turns the user's reactions to nudges into improvements: waits grow where nudges came too soon, legitimate activities stop being nudged and get labeled correctly, and every nudge's outcome becomes ground truth for measuring how well nudging works.

## ADDED Requirements

### Requirement: Record every nudge and its outcome
Every nudge moment SHALL be logged with its type, time, activity key, intent and source, whether it fired or was suppressed (and why), the shadow predictor score, the user's action if any, and what happened in the following 15 minutes (returned to work within N minutes, kept consuming, or switched to other work).

#### Scenario: Ignored nudge
- **WHEN** a nudge fires, the user takes no action, and keeps scrolling for 10 minutes
- **THEN** the log records the nudge as ignored with the outcome "kept consuming"

### Requirement: "Too soon" lengthens the wait in that context
A **Too soon** action SHALL multiply that moment's delay (or threshold) for that context, meaning moment type plus site plus intent source, by a factor (default 1.5, capped at 4× the default). **Back to task** SHALL move it 10% back toward the default.

#### Scenario: Off-task too soon
- **WHEN** the user clicks **Too soon** on an 8-minute off-task nudge on `youtube.com/watch` during a calendar block
- **THEN** the next off-task nudge in that context waits 12 minutes

### Requirement: "Legit task" corrects labels and suppresses nudges
A **Legit task** action SHALL store a user label marking that activity as on-task for that intent. It SHALL suppress future nudges for that activity and intent, and future backtests SHALL label that situation on-task.

#### Scenario: Feed research is legit
- **WHEN** the user clicks **Legit task** on a nudge for `linkedin.com/feed` during "Prepare outreach list"
- **THEN** no further nudges fire for `linkedin.com/feed` during that task, and the label store holds an on-task user label for it

### Requirement: Snooze
A **Snooze 30 min** action SHALL suppress all nudges for 30 minutes (configurable). The snooze SHALL be visible in `behaviornudge nudge status`.

#### Scenario: Snooze expires
- **WHEN** 30 minutes pass after a snooze
- **THEN** nudges can fire again

### Requirement: Feedback can be reviewed and undone
The user SHALL be able to list adjusted waits and legit-task suppressions and reset any of them from the command line.

#### Scenario: Undo a legit-task suppression
- **WHEN** the user resets the suppression for `linkedin.com/feed` during "Prepare outreach list"
- **THEN** nudges can fire there again and the user label is removed

### Requirement: Live nudge statistics
The system SHALL report, per nudge type and overall: nudges fired, actions taken (back to task / too soon / legit / snooze / ignored), return-to-work rate within 5 minutes, and the shadow predictor's ROC-AUC against outcomes.

#### Scenario: Weekly review
- **WHEN** the user runs the stats command after a week
- **THEN** they see, for example, that off-task nudges led to a return to work within 5 minutes 60% of the time
