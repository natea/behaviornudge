"""Notification content per nudge type (design D6). Local only: never sent off the machine."""

from __future__ import annotations

from dataclasses import dataclass

SOURCE_LABEL = {
    "timer": "current Sunsama task",
    "planned": "Sunsama timebox",
    "calendar": "calendar",
    "recent-creative": "what you were working on",
    "call": "your call",
    "agent": "agent working",
}

SITE_NAMES = {"linkedin.com": "LinkedIn", "x.com": "X", "instagram.com": "Instagram", "youtube.com": "YouTube",
              "facebook.com": "Facebook", "reddit.com": "Reddit"}


@dataclass
class Content:
    title: str
    body: str


def site_name(site: str | None) -> str:
    if not site:
        return "this"
    return SITE_NAMES.get(site, site)


def _minutes(seconds: float) -> str:
    m = max(1, round(seconds / 60))
    return f"{m} min"


def build(nudge_type: str, *, site: str | None, intent_title: str | None, intent_source: str | None,
          stretch_s: float = 0, call_name: str | None = None, agent_project: str | None = None,
          feed: bool = False, app: str | None = None, hopping: bool = False) -> Content:
    task = (intent_title or "").strip()[:80]
    src = SOURCE_LABEL.get(intent_source or "", intent_source or "")
    task_line = f"Task at hand: {task} ({src})" if task else ""
    name = site_name(site)
    if nudge_type == "arrival":
        if feed:
            return Content(f"Scrolling {name}?", task_line or "This isn't what you planned to do.")
        return Content(f"On {name} for a reason?", task_line or "Do the one thing you came for, then head back.")
    if nudge_type == "purpose_done":
        return Content(f"Done on {name}? Back to it.", task_line or "That was the thing you came for.")
    if nudge_type == "off_task":
        return Content(f"{_minutes(stretch_s)} on {name}", task_line or "Time to head back.")
    if nudge_type == "in_call":
        meeting = (call_name or "your call").strip()[:80]
        return Content("Stay present", f"You're in {meeting}.")
    if nudge_type == "agent_working":
        proj = (agent_project or "your project").strip()[:60]
        body = f"The agent is working on {proj}. Review the plan or line up the next step."
        return Content("Agent's on it. Stay with it?", body)
    if nudge_type == "messaging":
        from .messaging import label
        name = label(app)
        if hopping:
            return Content(f"Browsing {name} unreads?", task_line or "Pick one thing to answer, then head back.")
        return Content(f"Still in {name}?", task_line or f"{_minutes(stretch_s)} so far. Time to head back.")
    raise ValueError(f"unknown nudge type {nudge_type!r}")


SAMPLES = {
    "arrival": dict(site="linkedin.com", intent_title="Client tests", intent_source="timer"),
    "purpose_done": dict(site="linkedin.com", intent_title="Client tests", intent_source="timer"),
    "off_task": dict(site="x.com", intent_title="Deep Work — Client Projects", intent_source="calendar", stretch_s=480),
    "in_call": dict(site="x.com", intent_title=None, intent_source="call", call_name="New England Music project"),
    "agent_working": dict(site="instagram.com", intent_title="behaviornudge", intent_source="agent", agent_project="behaviornudge"),
    "messaging": dict(site="slack", intent_title="Client tests", intent_source="timer", app="slack", hopping=True),
}
