## ADDED Requirements

### Requirement: Read tabs from the browser window in front
When a browser's tab is read by script, the system SHALL check the tab title against the title of the frontmost window of the frontmost process. If they don't match, the answer came from another copy of that browser (for example an automation browser): the URL SHALL be ignored, and the user SHALL be told once per hour, in a notification without nudge buttons, that another copy of the browser is open.

#### Scenario: Automation browser open
- **WHEN** a second Google Chrome is running and the tab read returns a title that isn't in the front window's title
- **THEN** that URL is not recorded and one notice names the problem

#### Scenario: Normal browsing
- **WHEN** the front window's title contains the tab title that was read
- **THEN** the URL and title are used as before

#### Scenario: Window title unavailable
- **WHEN** the front window title can't be read (no Accessibility permission, no window)
- **THEN** the tab read is used as before
