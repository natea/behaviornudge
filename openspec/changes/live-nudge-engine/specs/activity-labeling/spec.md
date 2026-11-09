# Spec Delta

## MODIFIED Requirements

### Requirement: Apply labeling layers in order
The system SHALL apply the user's own labels first (review-queue answers and feedback such as "Legit task"), then rules, then stored labels for the same activity key, trimmed title and intended task, and only then the external classifier. A session labeled by an earlier layer SHALL NOT be sent to a later one.

#### Scenario: Rule match
- **WHEN** a session's activity key is `x.com/home` and a rule labels it consuming
- **THEN** the session is labeled from the rule and no external request is made for its mode

#### Scenario: Cache hit
- **WHEN** a session matches a previously labeled activity key, trimmed title and intended task
- **THEN** it reuses the stored labels without an external request

#### Scenario: User label overrides a rule
- **WHEN** a rule would label `linkedin.com/feed` off-task during "Prepare outreach list", but the user marked it **Legit task** for that intent
- **THEN** the session is labeled on-task from the user's label
