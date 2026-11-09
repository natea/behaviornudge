## ADDED Requirements

### Requirement: Run the nudge engine at login and restart it on exit
The system SHALL provide a per-user LaunchAgent that runs `behaviornudge nudge run` at login and restarts it within a minute whenever it exits, appending its output to `data/engine.log`.

#### Scenario: After reboot
- **WHEN** the user logs in after a restart
- **THEN** the nudge engine is running without any terminal open

#### Scenario: Crash or closed terminal
- **WHEN** the supervised engine process exits
- **THEN** launchd starts it again within a minute

### Requirement: Install and uninstall the engine agent safely
`behaviornudge nudge agent install` SHALL write and load the LaunchAgent and `uninstall` SHALL unload and remove it. If an engine is already running outside the agent, install SHALL refuse unless `--replace` is given, in which case it stops that engine first. After loading, install SHALL wait up to a minute for the engine's status heartbeat and report whether it started.

#### Scenario: Engine already running in a terminal
- **WHEN** `nudge run` is running in a terminal and the user runs `install` without `--replace`
- **THEN** nothing is changed and the command explains how to replace it

#### Scenario: Install verifies the engine
- **WHEN** install loads the agent
- **THEN** it reports the engine as running once a fresh heartbeat appears, or points to `data/engine.log` if none appears

### Requirement: Show supervision in status
`behaviornudge nudge status` and `nudge agent status` SHALL say whether the engine agent is installed and loaded.

#### Scenario: Agent not installed
- **WHEN** the engine runs from a terminal without the agent
- **THEN** status says the agent is not installed and how to install it
