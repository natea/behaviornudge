"""Call state from the helper's mic-in-use reports, with an end grace so brief mutes don't
split one call. Writes call_start / call_end entries to the live context log."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

APP_NAMES = {"us.zoom.xos": "Zoom", "com.microsoft.teams": "Teams", "com.tinyspeck.slackmacgap": "Slack",
             "com.apple.FaceTime": "FaceTime", "com.apple.avconferenced": "FaceTime", "net.whatsapp.WhatsApp": "WhatsApp",
             "com.webex": "Webex", "com.cisco.webexmeetings": "Webex", "com.hnc.Discord": "Discord",
             "com.google.Chrome": "a browser call", "company.thebrowser.Browser": "a browser call",
             "com.brave.Browser": "a browser call", "com.apple.Safari": "a browser call",
             "com.apple.WebKit": "a browser call", "com.microsoft.edgemac": "a browser call"}


@dataclass
class CallState:
    active: bool = False
    started: datetime | None = None
    app: str | None = None
    name: str | None = None


class CallTracker:
    def __init__(self, grace_s: float, meeting_at: Callable[[datetime], str | None],
                 write: Callable[[dict], None]):
        self.grace = timedelta(seconds=grace_s)
        self.meeting_at = meeting_at
        self.write = write
        self.state = CallState()
        self._mic_in_use = False
        self._released_at: datetime | None = None

    def on_calls(self, active: list[dict], now: datetime) -> None:
        """Helper `calls` event (sent only on change): the allowlisted apps capturing the mic."""
        if active:
            self._mic_in_use = True
            self._released_at = None
            if not self.state.active:
                bid = active[0].get("bundle_id", "")
                app = APP_NAMES.get(bid, active[0].get("name") or bid)
                name = self.meeting_at(now) or f"your {app} call".replace("your a browser call call", "your browser call")
                self.state = CallState(True, now, app, name)
                self.write({"time": now.isoformat(), "type": "call_start", "app": app, "bundle_id": bid, "name": name})
        else:
            if self._mic_in_use:
                self._released_at = now
            self._mic_in_use = False
        self.tick(now)

    def tick(self, now: datetime) -> None:
        """Keep the call named after the calendar event overlapping it now; end the call once no
        call app has used the mic for the grace period."""
        if self.state.active:
            current = self.meeting_at(now)
            if current and current != self.state.name:
                self.state.name = current
        if self.state.active and not self._mic_in_use and self._released_at is not None and now - self._released_at >= self.grace:
            self.write({"time": now.isoformat(), "type": "call_end", "app": self.state.app, "name": self.state.name,
                        "started": self.state.started.isoformat() if self.state.started else None})
            self.state = CallState()
