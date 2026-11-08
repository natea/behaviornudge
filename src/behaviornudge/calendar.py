"""Calendar history via jhammant Retrace's existing EventKit helper (Apple Calendar,
which the user's Google calendar syncs into). Only allowlisted calendars are kept."""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import Config


@dataclass(frozen=True)
class CalEvent:
    start: datetime
    end: datetime
    title: str
    calendar: str
    id: str


def parse_helper_output(raw: dict, allowlist: list[str]) -> list[CalEvent]:
    """Keep timed events from allowlisted calendars; drop all-day events."""
    out = []
    allowed = set(allowlist)
    for e in raw.get("events", []):
        if e.get("allDay"):
            continue
        if e.get("calendar") not in allowed:
            continue
        s, en = e.get("start"), e.get("end")
        if s is None or en is None or en <= s:
            continue
        out.append(CalEvent(datetime.fromtimestamp(s, UTC), datetime.fromtimestamp(en, UTC),
                            e.get("title") or "", e.get("calendar"), str(e.get("id"))))
    out.sort(key=lambda c: (c.start, c.end, c.id))
    return out


def run_helper(helper: Path, days_back: int) -> dict:
    res = subprocess.run([str(helper), str(days_back)], capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"calendar helper failed: {res.stderr.strip()[:200]}")
    raw = json.loads(res.stdout)
    if not raw.get("ok") or not raw.get("available", True):
        raise RuntimeError("calendar helper reported no access (grant Calendar permission to Retrace)")
    return raw


def load_calendar(cfg: Config, since: datetime) -> tuple[list[CalEvent], dict]:
    days = math.ceil((datetime.now(tz=UTC) - since).total_seconds() / 86400) + 1
    raw = run_helper(cfg.path("sources", "retrace_calendar_helper"), days)
    allow = cfg.section("intent").get("calendar_allowlist", [])
    events = parse_helper_output(raw, allow)
    out = cfg.data_dir / "calendar" / "events.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([{"start": e.start.isoformat(), "end": e.end.isoformat(), "title": e.title,
                                "calendar": e.calendar, "id": e.id} for e in events], indent=1))
    summary = {
        "days_back": days, "raw_events": len(raw.get("events", [])), "kept": len(events),
        "first": events[0].start.isoformat() if events else None,
        "last": events[-1].end.isoformat() if events else None,
        "calendars_kept": sorted({e.calendar for e in events}), "path": str(out),
        "declined_filtering": "unavailable (EventKit helper does not report declined status)",
    }
    return events, summary


def read_events(path: Path) -> list[CalEvent]:
    if not Path(path).exists():
        return []
    return [CalEvent(datetime.fromisoformat(e["start"]), datetime.fromisoformat(e["end"]), e["title"], e["calendar"], e["id"])
            for e in json.loads(Path(path).read_text())]
