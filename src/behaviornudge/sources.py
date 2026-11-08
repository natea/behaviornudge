"""Read-only readers for local activity sources.

Every database is opened with SQLite's ``mode=ro`` URI so this code can never write
to Retrace's data. Times are returned as timezone-aware UTC datetimes.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

# Lower number = higher precedence when sources overlap (exact durations first).
PRIORITY = {"retrace_to": 0, "knowledgec": 1, "capture": 2}
CAPTURE_MAX_S = 90.0  # a point capture stands in for at most this long


@dataclass
class Record:
    start: datetime
    end: datetime
    source: str
    bundle_id: str | None = None
    app_name: str | None = None
    url: str | None = None
    title: str | None = None
    caption: str | None = None
    meta: dict = field(default_factory=dict)

    @property
    def priority(self) -> int:
        return PRIORITY.get(self.source, 9)

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()


@dataclass
class Visit:
    """A browser history visit: a point in time with a URL, no duration."""

    time: datetime
    url: str
    browser: str  # bundle id
    title: str | None = None


def connect_ro(path: Path) -> sqlite3.Connection:
    """Open a SQLite database strictly read-only."""
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(path)
    uri = f"file:{quote(str(path))}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.execute("PRAGMA query_only = ON")
    return con


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def read_retrace_to(path: Path, since: datetime | None = None) -> list[Record]:
    """retrace.to `segment` rows: exact start/end in ms, app bundle id, window title, URL."""
    con = connect_ro(path)
    try:
        q = "SELECT bundleID, startDate, endDate, windowName, browserUrl FROM segment"
        args: tuple = ()
        if since:
            q += " WHERE endDate >= ?"
            args = (int(since.timestamp() * 1000),)
        q += " ORDER BY startDate"
        out = []
        for bundle, s, e, title, url in con.execute(q, args):
            if e is None or s is None or e <= s:
                continue
            out.append(Record(_ms(s), _ms(e), "retrace_to", bundle_id=bundle, url=url or None, title=title or None))
        return out
    finally:
        con.close()


def read_knowledgec(path: Path, since: datetime | None = None) -> list[Record]:
    """jhammant Retrace `activity_events` from macOS Screen Time: app-level exact durations."""
    con = connect_ro(path)
    try:
        q = "SELECT app, start_at, end_at, seconds FROM activity_events WHERE source='knowledgec'"
        args: tuple = ()
        if since:
            q += " AND start_at >= ?"
            args = (since.strftime("%Y-%m-%d %H:%M:%S"),)
        out = []
        for app, s, e, secs in con.execute(q + " ORDER BY start_at", args):
            start = _parse_ts(s)
            end = _parse_ts(e) or (start + timedelta(seconds=secs or 0) if start else None)
            if start and end and end > start:
                out.append(Record(start, end, "knowledgec", bundle_id=app))
        return out
    finally:
        con.close()


def read_visits(path: Path, since: datetime | None = None) -> list[Visit]:
    """Chrome/Safari history visits (no durations)."""
    con = connect_ro(path)
    try:
        q = "SELECT source, app, start_at, url, title FROM activity_events WHERE source IN ('chrome','safari') AND url IS NOT NULL"
        args: tuple = ()
        if since:
            q += " AND start_at >= ?"
            args = (since.strftime("%Y-%m-%d %H:%M:%S"),)
        out = []
        for _src, app, s, url, title in con.execute(q + " ORDER BY start_at", args):
            t = _parse_ts(s)
            if t:
                out.append(Visit(t, url, app, title))
        return out
    finally:
        con.close()


def read_captures(path: Path, since: datetime | None = None) -> list[Record]:
    """jhammant Retrace point captures. Each stands in until the next capture, capped.

    Screen text is NOT returned here (it stays in Retrace); only the short caption.
    """
    con = connect_ro(path)
    try:
        q = "SELECT captured_at, app_name, bundle_id, window_title, url, caption FROM captures"
        args: tuple = ()
        if since:
            q += " WHERE captured_at >= ?"
            args = (since.strftime("%Y-%m-%d %H:%M:%S"),)
        rows = list(con.execute(q + " ORDER BY captured_at", args))
    finally:
        con.close()
    out = []
    for i, (ts, app, bundle, title, url, caption) in enumerate(rows):
        start = _parse_ts(ts)
        if not start:
            continue
        nxt = _parse_ts(rows[i + 1][0]) if i + 1 < len(rows) else None
        end = min(nxt, start + timedelta(seconds=CAPTURE_MAX_S)) if nxt else start + timedelta(seconds=CAPTURE_MAX_S)
        if end <= start:
            continue
        out.append(Record(start, end, "capture", bundle_id=bundle, app_name=app, url=url or None, title=title or None, caption=caption or None))
    return out


def capture_texts(path: Path, start: datetime, end: datetime) -> list[tuple[datetime, str]]:
    """Screen text of captures in a window, for local-only project inference."""
    con = connect_ro(path)
    try:
        rows = con.execute(
            "SELECT captured_at, text FROM captures WHERE captured_at >= ? AND captured_at < ? ORDER BY captured_at",
            (start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchall()
    finally:
        con.close()
    return [(_parse_ts(t), txt or "") for t, txt in rows]


def read_calendar_json(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    return json.loads(Path(path).read_text())
