"""Nudge settings: defaults merged with the `[nudge]` section of behaviornudge.toml."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

TYPES = ("arrival", "purpose_done", "off_task", "in_call", "agent_working")

DEFAULT_SOUNDS = {"arrival": "Tink", "purpose_done": "Glass", "off_task": "Sosumi",
                  "agent_working": "Pop", "in_call": "none"}

DEFAULT_CALL_ALLOWLIST = [
    "us.zoom.xos", "com.microsoft.teams", "com.tinyspeck.slackmacgap", "com.apple.FaceTime",
    "com.apple.avconferenced", "net.whatsapp.WhatsApp", "com.webex", "com.cisco.webexmeetings",
    "com.hnc.Discord", "com.google.Chrome", "company.thebrowser.Browser", "com.brave.Browser",
    "com.apple.Safari", "com.apple.WebKit", "com.microsoft.edgemac",
]


@dataclass
class NudgeSettings:
    enabled: bool = True
    daily_budget: int = 8
    cooldown_min: float = 15
    snooze_min: float = 30
    quiet_hours: list = field(default_factory=list)  # [["21:30", "07:00"], ...] local time
    arrival_delay_s: float = 20
    feed_arrival_delay_s: float = 45      # landing straight on a drift-prone feed
    purpose_done_delay_s: float = 30
    off_task_threshold_min: float = 8
    in_call_delay_s: float = 30
    agent_delay_min: float = 2
    too_soon_factor: float = 1.5
    too_soon_cap: float = 4.0
    call_end_grace_s: float = 45
    live_jev: bool = True             # label unknown live activities with Jev in the background (needs TYPESAFE_API_KEY)
    call_allowlist: list = field(default_factory=lambda: list(DEFAULT_CALL_ALLOWLIST))
    sounds: dict = field(default_factory=lambda: dict(DEFAULT_SOUNDS))

    def base_delay_s(self, nudge_type: str) -> float:
        return {
            "arrival": self.arrival_delay_s,
            "purpose_done": self.purpose_done_delay_s,
            "off_task": self.off_task_threshold_min * 60,
            "in_call": self.in_call_delay_s,
            "agent_working": self.agent_delay_min * 60,
        }[nudge_type]

    def sound(self, nudge_type: str) -> str | None:
        s = self.sounds.get(nudge_type, "none")
        return None if not s or s == "none" else s


def load_settings(raw: dict[str, Any] | None) -> NudgeSettings:
    raw = dict(raw or {})
    sounds = {**DEFAULT_SOUNDS, **(raw.pop("sounds", None) or {})}
    known = {f.name for f in fields(NudgeSettings)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(f"unknown [nudge] settings: {', '.join(unknown)}")
    return NudgeSettings(**raw, sounds=sounds)
