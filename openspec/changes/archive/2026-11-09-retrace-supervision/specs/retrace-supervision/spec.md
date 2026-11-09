# Spec Delta

## Purpose

Keeps jhammant Retrace capturing without manual restarts: it starts at login and restarts after a crash. Supervision can be installed and removed safely, and the user is told when capture has stopped or is failing, so gaps are noticed in minutes rather than hours.

## ADDED Requirements

### Requirement: Start Retrace at login and restart it on exit
The system SHALL provide a per-user LaunchAgent that starts Retrace's server at login and restarts it within a minute whenever it exits, writing its output to a log file.

#### Scenario: Crash recovery
- **WHEN** the supervised Retrace server process is killed
- **THEN** launchd starts it again within a minute and the API answers on its port

#### Scenario: After reboot
- **WHEN** the user logs in after a restart
- **THEN** Retrace's server is running without any manual command

#### Scenario: Output is kept
- **WHEN** the supervised server writes output or crashes with an error
- **THEN** that output is appended to `~/.retrace/serve.log`

### Requirement: Install and uninstall safely
`behaviornudge retrace agent install` SHALL write and load the LaunchAgent and `uninstall` SHALL unload and remove it. If a Retrace server is already running outside the agent, install SHALL refuse unless `--replace` is given, in which case it stops that server first. Installing twice SHALL leave one agent.

#### Scenario: Server already running
- **WHEN** a manually started Retrace server is listening and the user runs `install` without `--replace`
- **THEN** nothing is changed and the command explains how to replace it

#### Scenario: Uninstall
- **WHEN** the user runs `uninstall`
- **THEN** the agent is unloaded, its plist removed, and the supervised server stopped

### Requirement: Verify capture after install
After loading the agent, install SHALL wait for a new capture (not just a healthy API) for up to two minutes. If Retrace reports a Screen Recording or other permission failure, install SHALL say which program needs the permission and where to grant it.

#### Scenario: Missing Screen Recording permission
- **WHEN** the supervised server starts but captures fail with a Screen Recording permission error
- **THEN** install reports the failure, names the Python interpreter launchd runs, and points to Privacy & Security → Screen & System Audio Recording

#### Scenario: Working capture
- **WHEN** a capture newer than the install time is saved
- **THEN** install reports that Retrace is supervised and capturing

### Requirement: Warn when capture stops
`behaviornudge nudge status` and `behaviornudge retrace agent status` SHALL warn when Retrace's API doesn't answer, when its last error is newer than its last capture, or when its capture loop hasn't updated its status for 5 minutes, showing the last capture's age and the last error. No new capture for 10+ minutes while the loop runs (duplicate or gated frames) SHALL be reported without a warning.

#### Scenario: Stale capture
- **WHEN** the last capture was 70 minutes ago and Retrace's last error is a permission failure
- **THEN** status shows a warning with the age of the last capture and that error

#### Scenario: Healthy
- **WHEN** a capture was saved a minute ago
- **THEN** status shows Retrace as capturing with no warning

#### Scenario: Nothing new on screen
- **WHEN** the last capture was 20 minutes ago but the capture loop updated its status seconds ago with no newer error
- **THEN** status says nothing new was saved (duplicate or gated frames) without a warning

#### Scenario: Capture loop stopped
- **WHEN** the API answers but Retrace's status hasn't been updated for 6 minutes
- **THEN** status shows a warning that the capture loop has stopped
