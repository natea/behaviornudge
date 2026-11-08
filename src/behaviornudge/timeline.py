"""Build the normalized activity timeline: records -> activity keys -> sessions."""

from __future__ import annotations

import bisect
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from .keys import KeyInfo, activity_key, is_browser
from .sources import Record, Visit

# (record) -> (project, source). Plugged in by intent-context for terminals/editors.
ProjectResolver = Callable[[Record], "tuple[str | None, str | None]"]


@dataclass
class Piece:
    """A keyed stretch of time from one source, after overlap resolution."""

    start: datetime
    end: datetime
    info: KeyInfo
    source: str
    title: str | None = None
    caption: str | None = None
    project: str | None = None
    project_source: str | None = None

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()


@dataclass
class Session:
    start: datetime
    end: datetime
    key: str
    app: str
    domain: str | None
    path_class: str | None
    sources: set[str] = field(default_factory=set)
    title: str | None = None
    caption: str | None = None
    project: str | None = None
    project_source: str | None = None
    _title_weight: Counter = field(default_factory=Counter, repr=False)

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()


# ---------------------------------------------------------------------------
# Overlap resolution (D2): higher-precedence sources win; lower ones fill gaps.
# ---------------------------------------------------------------------------

def _merge_intervals(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _subtract(rec: Record, covered: list[tuple[datetime, datetime]], starts: list[datetime]) -> list[tuple[datetime, datetime]]:
    pieces = []
    cur = rec.start
    i = max(bisect.bisect_right(starts, rec.start) - 1, 0)
    while i < len(covered) and covered[i][0] < rec.end:
        cs, ce = covered[i]
        if ce > cur:
            if cs > cur:
                pieces.append((cur, min(cs, rec.end)))
            cur = max(cur, ce)
            if cur >= rec.end:
                break
        i += 1
    if cur < rec.end:
        pieces.append((cur, rec.end))
    return [(s, e) for s, e in pieces if (e - s).total_seconds() >= 1.0]


def resolve_overlaps(records: Iterable[Record]) -> list[Record]:
    """Return non-overlapping records: each moment is owned by the best source covering it."""
    by_prio: dict[int, list[Record]] = {}
    for r in records:
        by_prio.setdefault(r.priority, []).append(r)
    covered: list[tuple[datetime, datetime]] = []
    out: list[Record] = []
    for prio in sorted(by_prio):
        recs = sorted(by_prio[prio], key=lambda r: (r.start, r.end))
        starts = [c[0] for c in covered]
        level: list[tuple[datetime, datetime]] = []
        for r in recs:
            for s, e in _subtract(r, covered, starts) if covered else [(r.start, r.end)]:
                out.append(Record(s, e, r.source, r.bundle_id, r.app_name, r.url, r.title, r.caption, r.meta))
                level.append((s, e))
        covered = _merge_intervals(covered + level)
    out.sort(key=lambda r: (r.start, r.end))
    # Within one source, records can overlap each other; clip to keep the timeline linear.
    linear: list[Record] = []
    for r in out:
        if linear and r.start < linear[-1].end:
            if r.end <= linear[-1].end:
                continue
            r = Record(linear[-1].end, r.end, r.source, r.bundle_id, r.app_name, r.url, r.title, r.caption, r.meta)
        linear.append(r)
    return linear


# ---------------------------------------------------------------------------
# Keys and visit refinement
# ---------------------------------------------------------------------------

def to_pieces(records: list[Record], visits: list[Visit], resolver: ProjectResolver | None = None) -> list[Piece]:
    """Assign activity keys; browser time without a URL is refined by history visits inside it."""
    vtimes = [v.time for v in visits]
    pieces: list[Piece] = []
    for r in records:
        project, psrc = resolver(r) if resolver else (None, None)
        browser = is_browser(r.bundle_id, r.app_name)
        if browser and not r.url and visits:
            lo = bisect.bisect_left(vtimes, r.start)
            hi = bisect.bisect_left(vtimes, r.end)
            inside = visits[lo:hi]
            if inside:
                cuts = [r.start] + [v.time for v in inside] + [r.end]
                for j in range(len(cuts) - 1):
                    s, e = cuts[j], cuts[j + 1]
                    if e <= s:
                        continue
                    url = inside[j - 1].url if j > 0 else None
                    title = inside[j - 1].title if j > 0 else r.title
                    info = activity_key(r.bundle_id, r.app_name, url)
                    pieces.append(Piece(s, e, info, r.source, title, r.caption, info.project_hint or project, "url" if info.project_hint else psrc))
                continue
        info = activity_key(r.bundle_id, r.app_name, r.url, project)
        pieces.append(Piece(r.start, r.end, info, r.source, r.title, r.caption, info.project_hint or project, "url" if info.project_hint else psrc))
    return pieces


# ---------------------------------------------------------------------------
# Sessionization (D1)
# ---------------------------------------------------------------------------

def _merge_same_key(items: list[Session], gap_s: float) -> list[Session]:
    out: list[Session] = []
    for s in items:
        last = out[-1] if out else None
        if last and last.key == s.key and (s.start - last.end).total_seconds() < gap_s:
            last.end = max(last.end, s.end)
            last.sources |= s.sources
            last._title_weight.update(s._title_weight)
            last.caption = s.caption or last.caption
            last.project = last.project or s.project
            last.project_source = last.project_source or s.project_source
        else:
            out.append(s)
    return out


def sessionize(pieces: list[Piece], merge_gap_s: float = 30.0, transition_max_s: float = 3.0) -> tuple[list[Session], list[Session]]:
    """Merge same-key pieces into sessions; return (sessions, transitions)."""
    items = []
    for p in sorted(pieces, key=lambda p: (p.start, p.end)):
        tw = Counter({p.title: p.seconds}) if p.title else Counter()
        items.append(Session(p.start, p.end, p.info.key, p.info.app, p.info.domain, p.info.path_class,
                             {p.source}, None, p.caption, p.project, p.project_source, tw))
    merged = _merge_same_key(items, merge_gap_s)
    sessions = [s for s in merged if s.seconds >= transition_max_s]
    transitions = [s for s in merged if s.seconds < transition_max_s]
    sessions = _merge_same_key(sessions, merge_gap_s)
    for s in sessions + transitions:
        s.title = s._title_weight.most_common(1)[0][0] if s._title_weight else None
    return sessions, transitions


# ---------------------------------------------------------------------------
# Storage (3.4)
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE sessions (
  id INTEGER PRIMARY KEY, start_utc TEXT NOT NULL, end_utc TEXT NOT NULL, seconds REAL NOT NULL,
  key TEXT NOT NULL, app TEXT, domain TEXT, path_class TEXT, title TEXT, caption TEXT,
  project TEXT, project_source TEXT, sources TEXT,
  local_date TEXT, local_hour INTEGER, local_weekday INTEGER
);
CREATE INDEX ix_sessions_start ON sessions(start_utc);
CREATE TABLE transitions (id INTEGER PRIMARY KEY, start_utc TEXT, end_utc TEXT, key TEXT);
CREATE TABLE build_info (k TEXT PRIMARY KEY, v TEXT);
"""


def local_parts(dt: datetime, tz: str) -> tuple[str, int, int]:
    loc = dt.astimezone(ZoneInfo(tz))
    return loc.strftime("%Y-%m-%d"), loc.hour, loc.weekday()


def write_timeline(path: Path, sessions: list[Session], transitions: list[Session], tz: str, info: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    rows = []
    for i, s in enumerate(sessions, 1):
        d, h, w = local_parts(s.start, tz)
        rows.append((i, s.start.isoformat(), s.end.isoformat(), round(s.seconds, 3), s.key, s.app, s.domain, s.path_class,
                     s.title, s.caption, s.project, s.project_source, ",".join(sorted(s.sources)), d, h, w))
    con.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO transitions VALUES (?,?,?,?)",
                    [(i, t.start.isoformat(), t.end.isoformat(), t.key) for i, t in enumerate(transitions, 1)])
    con.executemany("INSERT INTO build_info VALUES (?,?)", [(k, json.dumps(v, default=str)) for k, v in sorted(info.items())])
    con.commit()
    con.close()
    tmp.replace(path)


def read_sessions(path: Path) -> list[dict]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM sessions ORDER BY start_utc")]
    finally:
        con.close()


def parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(UTC)


def window(sessions: list[dict], start: datetime, end: datetime) -> list[dict]:
    return [s for s in sessions if parse_utc(s["end_utc"]) > start and parse_utc(s["start_utc"]) < end]


_ = timedelta  # re-exported convenience
