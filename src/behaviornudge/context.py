"""Live context log (herdr focus/agent events + Sunsama active timer). Reader side; the
logger itself is added in task group 11."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def read_log(path: Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def agent_working_spans(path: Path) -> list[tuple[datetime, datetime]]:
    """Spans during which any herdr agent was in `working` state."""
    working: dict[str, datetime] = {}
    spans: list[tuple[datetime, datetime]] = []
    for e in read_log(path):
        if e.get("type") != "agent_status":
            continue
        t = datetime.fromisoformat(e["time"])
        pane = e.get("pane_id", "?")
        if e.get("status") == "working":
            working.setdefault(pane, t)
        elif pane in working:
            spans.append((working.pop(pane), t))
    return sorted(spans)
