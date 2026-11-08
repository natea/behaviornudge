"""Drift detection: purposeful visits to drift-prone sites that turn into off-task consuming."""

from __future__ import annotations

import bisect
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .timeline import parse_utc

PURPOSEFUL = {"lookup", "reply", "follow-link"}


@dataclass
class Episode:
    entry_time: datetime
    entry_session_id: int
    site: str
    key: str
    purpose: str
    intent_source: str | None
    intent_title: str | None
    outcome: str  # drift / clean
    drift_min: float
    to_first_drift_min: float | None
    to_return_min: float | None
    triggers: list[str] = field(default_factory=list)
    local_hour: int | None = None
    local_weekday: int | None = None

    def row(self) -> dict:
        d = asdict(self)
        d["entry_time"] = self.entry_time.isoformat()
        return d


def site_of(domain: str | None, prone: list[str]) -> str | None:
    if not domain:
        return None
    for p in prone:
        if domain == p or domain.endswith("." + p):
            return p
    return None


def is_drift_time(s: dict) -> bool:
    return s.get("mode") == "consuming" and s.get("alignment") in ("off-task", "no-intent")


def is_return(s: dict, min_s: float = 0) -> bool:
    """A return to work: an on-task or creative session that lasts at least `min_s` seconds
    (brief glances at the editor while still scrolling don't count)."""
    return (s.get("alignment") == "on-task" or s.get("mode") == "creative") and s.get("seconds", min_s) >= min_s


class TriggerIndex:
    """Trigger sources: calendar event ends, notifications, terminal agents working."""

    def __init__(self, calendar_ends=(), notifications=(), agent_working=()):
        self.cal = sorted(calendar_ends)
        self.notif = sorted(notifications)
        self.agent = sorted(agent_working)  # (start, end) spans when an agent was working

    @staticmethod
    def _any_in(times: list[datetime], lo: datetime, hi: datetime) -> bool:
        i = bisect.bisect_left(times, lo)
        return i < len(times) and times[i] <= hi

    def at(self, t: datetime, meeting_min: float = 5, notif_min: float = 2) -> list[str]:
        out = []
        if self._any_in(self.cal, t - timedelta(minutes=meeting_min), t):
            out.append("meeting-ended")
        if self._any_in(self.notif, t - timedelta(minutes=notif_min), t):
            out.append("notification")
        if any(s <= t < e for s, e in self.agent):
            out.append("agent-working")
        return out or ["none"]


def detect(sessions: list[dict], prone: list[str], window_min: float = 15, threshold_min: float = 3,
           triggers: TriggerIndex | None = None, tz: str = "America/New_York", entry_mode: str = "purposeful",
           return_min_s: float = 60) -> list[Episode]:
    """Find episodes. `entry_mode='any'` widens entries to every session on a drift-prone site."""
    win = timedelta(minutes=window_min)
    z = ZoneInfo(tz)
    eps: list[Episode] = []
    active_until: datetime | None = None
    for i, s in enumerate(sessions):
        site = site_of(s.get("domain"), prone)
        if not site:
            continue
        purposeful = s.get("purpose") in PURPOSEFUL
        if entry_mode == "purposeful" and not purposeful:
            continue
        if entry_mode == "any" and s.get("alignment") == "on-task":
            continue
        t0 = parse_utc(s["start_utc"])
        if active_until and t0 < active_until:
            continue  # inside an earlier episode's window
        horizon = t0 + win
        drift_s = 0.0
        first_drift = None
        returned = None
        for nxt in sessions[i + 1:]:
            ns, ne = parse_utc(nxt["start_utc"]), parse_utc(nxt["end_utc"])
            if ns >= horizon:
                break
            if is_return(nxt, return_min_s):
                returned = ns
                break
            if is_drift_time(nxt):
                drift_s += (min(ne, horizon) - ns).total_seconds()
                first_drift = first_drift or ns
        outcome = "drift" if drift_s >= threshold_min * 60 else "clean"
        loc = t0.astimezone(z)
        eps.append(Episode(
            entry_time=t0, entry_session_id=s["id"], site=site, key=s["key"], purpose=s.get("purpose") or "unknown",
            intent_source=s.get("intent_source"), intent_title=s.get("intent_title"), outcome=outcome,
            drift_min=round(drift_s / 60, 2),
            to_first_drift_min=round((first_drift - t0).total_seconds() / 60, 2) if first_drift else None,
            to_return_min=round((returned - t0).total_seconds() / 60, 2) if returned else None,
            triggers=triggers.at(t0) if triggers else ["none"], local_hour=loc.hour, local_weekday=loc.weekday(),
        ))
        active_until = horizon
    return eps


def summarize(eps: list[Episode]) -> dict:
    def group(fn):
        g: dict = defaultdict(lambda: [0, 0, 0.0])
        for e in eps:
            for k in fn(e):
                g[k][0] += 1
                g[k][1] += e.outcome == "drift"
                g[k][2] += e.drift_min
        return {str(k): {"episodes": n, "drifts": d, "drift_rate": round(d / n, 3) if n else 0, "drift_min": round(m, 1)}
                for k, (n, d, m) in sorted(g.items(), key=lambda kv: str(kv[0]))}

    n = len(eps)
    drifts = sum(e.outcome == "drift" for e in eps)
    return {
        "episodes": n, "drifts": drifts, "drift_rate": round(drifts / n, 3) if n else 0,
        "drift_minutes": round(sum(e.drift_min for e in eps), 1),
        "by_site": group(lambda e: [e.site]), "by_purpose": group(lambda e: [e.purpose]),
        "by_trigger": group(lambda e: e.triggers), "by_hour": group(lambda e: [e.local_hour]),
        "by_weekday": group(lambda e: [e.local_weekday]), "by_intent_source": group(lambda e: [e.intent_source]),
    }


def save(eps: list[Episode], path: Path) -> None:
    path.write_text(json.dumps([e.row() for e in eps], indent=1))


def load(path: Path) -> list[Episode]:
    out = []
    for d in json.loads(Path(path).read_text()):
        d["entry_time"] = datetime.fromisoformat(d["entry_time"])
        out.append(Episode(**d))
    return out
