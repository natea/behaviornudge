# Spec Delta

## ADDED Requirements

### Requirement: Messaging nudge
A messaging nudge SHALL fire during a known task the current messaging activity isn't aligned with, when the messaging stretch has spanned at least 4 conversations and lasted at least 60 s (hopping), or has lasted 5 minutes (lingering). A stretch covers time in messaging apps; switching apps continues it, and 60 s at other activity ends it. It fires once per stretch.

#### Scenario: Checking the unreads
- **WHEN** the intent is "Client tests" and the user opens 4 different Slack channels within 70 s
- **THEN** a messaging nudge fires asking whether they're browsing unreads, naming "Client tests"

#### Scenario: A quick reply
- **WHEN** the intent is "Client tests" and the user opens one WhatsApp chat, replies, and returns to their editor after 90 s
- **THEN** no messaging nudge fires

#### Scenario: Lingering in one app
- **WHEN** the intent is "Deep Work — Client Projects" and the user has been in Discord for 5 minutes
- **THEN** a messaging nudge fires asking whether they're still in Discord

#### Scenario: Hopping between apps
- **WHEN** during a known task the user spends 2 minutes in Slack, then 3 minutes in WhatsApp, with no 60 s back at other work
- **THEN** the stretch counts as 5 minutes and a messaging nudge fires

### Requirement: Messaging that is the task stays quiet
No messaging nudge SHALL fire when the current messaging activity is aligned with the task at hand (e.g. the task is about replying, messaging, Slack or a conversation with someone), when the user marked it "Legit task" for that task, or when there is no known intent. During an active call the call rules SHALL apply instead.

#### Scenario: The task is a reply
- **WHEN** the intent is "Reply to Chris re job hunt" and the user has been in Slack for 10 minutes across 6 conversations
- **THEN** no messaging nudge fires

#### Scenario: Slack huddle
- **WHEN** a Slack huddle is using the microphone and the user moves between Slack channels
- **THEN** no messaging nudge fires
