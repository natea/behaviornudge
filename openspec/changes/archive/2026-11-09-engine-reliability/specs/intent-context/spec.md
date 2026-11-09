## ADDED Requirements

### Requirement: Keep the Sunsama sign-in alive
Renewals of the Sunsama access token SHALL be serialized across threads and processes, and tokens SHALL be re-read from disk once the renewal lock is held, so that a refresh token is never used twice. When a sign-in is needed anyway, the running engine SHALL tell the user once in a notification without nudge buttons, naming the command to run.

#### Scenario: Two renewals at once
- **WHEN** the timer poller and the timebox refresher both find the access token expired
- **THEN** one renews it and the other uses the renewed token

#### Scenario: Sign-in expired
- **WHEN** Sunsama rejects the refresh token
- **THEN** the engine shows one notice with `behaviornudge sunsama-timer --login` and keeps running on calendar intent
