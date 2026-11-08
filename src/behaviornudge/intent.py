"""Intent context: what the user was *supposed* to be doing at a moment.

Fallback order: Sunsama timer (capped) -> Sunsama planned timebox -> calendar event
(shortest covering event wins) -> recent creative project within a window -> none.
"""

from __future__ import annotations

import bisect
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Sequence
from zoneinfo import ZoneInfo

SOURCES = ("timer", "planned", "calendar", "recent-creative", "none")


@dataclass(frozen=True)
class Span:
    start: datetime
    end: datetime
    title: str = ""
    channel: str | None = None
    ref: str | None = None  # task id / event id


@dataclass(frozen=True)
class Intent:
    source: str
    title: str | None = None
    channel: str | None = None
    project: str | None = None
    ref: str | None = None

    @property
    def known(self) -> bool:
        return self.source != "none"


NO_INTENT = Intent("none")


class _SpanIndex:
    """Find spans covering a time; when several cover it, the shortest wins."""

    def __init__(self, spans: Iterable[Span]):
        self.spans = sorted((s for s in spans if s.end > s.start), key=lambda s: (s.start, s.end))
        self.starts = [s.start for s in self.spans]
        self.maxdur = max((s.end - s.start for s in self.spans), default=timedelta(0))

    def covering(self, t: datetime) -> Span | None:
        hi = bisect.bisect_right(self.starts, t)
        lo = bisect.bisect_left(self.starts, t - self.maxdur)
        best = None
        for s in self.spans[lo:hi]:
            if s.start <= t < s.end and (best is None or (s.end - s.start) < (best.end - best.start)):
                best = s
        return best


@dataclass
class ActivitySpan:
    """Minimal view of a timeline session for intent purposes."""

    start: datetime
    end: datetime
    key: str
    project: str | None
    mode: str | None  # creative / reactive / consuming / None


class IntentResolver:
    def __init__(
        self,
        timers: Sequence[Span] = (),
        planned: Sequence[Span] = (),
        calendar: Sequence[Span] = (),
        activity: Sequence[ActivitySpan] = (),
        recent_window_min: float = 30,
        channel_to_project: dict[str, str] | None = None,
    ):
        self.timers = _SpanIndex(timers)
        self.planned = _SpanIndex(planned)
        self.calendar = _SpanIndex(calendar)
        self.window = timedelta(minutes=recent_window_min)
        self.ch2proj = channel_to_project or {}
        creative = sorted((a for a in activity if a.mode == "creative"), key=lambda a: a.start)
        self.creative = creative
        self.creative_starts = [a.start for a in creative]

    def _project_for(self, channel: str | None) -> str | None:
        return self.ch2proj.get(channel) if channel else None

    def recent_creative(self, t: datetime, window: timedelta | None = None) -> ActivitySpan | None:
        window = window if window is not None else self.window
        i = bisect.bisect_right(self.creative_starts, t) - 1
        while i >= 0:
            a = self.creative[i]
            if a.end >= t - window:
                if a.start < t:
                    return a
            else:
                # sessions are non-overlapping and sorted, so earlier ones end even earlier
                return None
            i -= 1
        return None

    def resolve(self, t: datetime, window: timedelta | None = None) -> Intent:
        for source, idx in (("timer", self.timers), ("planned", self.planned), ("calendar", self.calendar)):
            s = idx.covering(t)
            if s:
                return Intent(source, s.title, s.channel, self._project_for(s.channel), s.ref)
        a = self.recent_creative(t, window)
        if a:
            return Intent("recent-creative", a.project or a.key, None, a.project or a.key, None)
        return NO_INTENT

    def coverage(self, times: Sequence[datetime], windows_min: Sequence[int] = (15, 30, 60, 120)) -> dict:
        """Share of moments resolved by each source, for several recent-creative windows."""
        out = {}
        for w in windows_min:
            c = Counter(self.resolve(t, timedelta(minutes=w)).source for t in times)
            n = max(len(times), 1)
            out[f"{w}min"] = {s: round(c.get(s, 0) / n, 4) for s in SOURCES}
        return out

    def coverage_by_day(self, times: Sequence[datetime], tz: str) -> dict[str, dict[str, int]]:
        z = ZoneInfo(tz)
        days: dict[str, Counter] = defaultdict(Counter)
        for t in times:
            days[t.astimezone(z).strftime("%Y-%m-%d")][self.resolve(t).source] += 1
        return {d: dict(c) for d, c in sorted(days.items())}


# ---------------------------------------------------------------------------
# Forgotten-timer capping (4.4)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CappedTimer:
    span: Span
    capped_end: datetime
    recorded_min: float
    corrected_min: float


RelatedFn = Callable[[ActivitySpan, Span], bool]


def default_related(channel_to_project: dict[str, str]) -> RelatedFn:
    """Activity counts as related to a timer when its project matches the timer's channel,
    or its project name appears in the task title. If neither can be determined, any
    non-consuming activity counts."""

    def related(a: ActivitySpan, timer: Span) -> bool:
        proj = (a.project or "").lower()
        mapped = (channel_to_project.get(timer.channel) or "").lower() if timer.channel else ""
        if mapped:
            return proj == mapped
        if proj and len(proj) >= 4 and proj in (timer.title or "").lower():
            return True
        return a.mode in ("creative", "reactive")

    return related


def cap_timers(timers: Sequence[Span], activity: Sequence[ActivitySpan], related: RelatedFn, cap_min: float = 45) -> list[CappedTimer]:
    cap = timedelta(minutes=cap_min)
    acts = sorted(activity, key=lambda a: a.start)
    starts = [a.start for a in acts]
    out = []
    for tm in sorted(timers, key=lambda s: s.start):
        lo = max(bisect.bisect_left(starts, tm.start) - 1, 0)
        last_related = tm.start
        corrected = 0.0
        capped_end = None
        for a in acts[lo:]:
            if a.start >= tm.end:
                break
            if a.end <= tm.start or not related(a, tm):
                continue
            if a.start - last_related > cap:
                capped_end = last_related + cap
                break
            s, e = max(a.start, tm.start), min(a.end, tm.end)
            corrected += max((e - s).total_seconds(), 0)
            last_related = max(last_related, e)
        if capped_end is None:
            capped_end = min(tm.end, last_related + cap)
        capped_end = min(capped_end, tm.end)
        out.append(CappedTimer(tm, capped_end, (tm.end - tm.start).total_seconds() / 60, corrected / 60))
    return out


def corrected_time_report(capped: Sequence[CappedTimer]) -> list[dict]:
    by_task: dict[str, dict] = {}
    for c in capped:
        k = c.span.ref or c.span.title
        d = by_task.setdefault(k, {"task": c.span.title, "channel": c.span.channel, "recorded_min": 0.0, "corrected_min": 0.0, "sessions": 0})
        d["recorded_min"] += c.recorded_min
        d["corrected_min"] += c.corrected_min
        d["sessions"] += 1
    return sorted(({**d, "recorded_min": round(d["recorded_min"], 1), "corrected_min": round(d["corrected_min"], 1)}
                   for d in by_task.values()), key=lambda d: -d["recorded_min"])


# ---------------------------------------------------------------------------
# Loading exported Sunsama data
# ---------------------------------------------------------------------------

_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*([AP]M)\s*$", re.IGNORECASE)


def parse_local(day: str, clock: str, tz: str) -> datetime:
    """'2026-11-07' + '3:00 PM' in tz -> aware UTC datetime."""
    m = _TIME_RE.match(clock)
    if not m:
        raise ValueError(f"unrecognised time {clock!r}")
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3).upper()
    h = h % 12 + (12 if ap == "PM" else 0)
    d = datetime.strptime(day, "%Y-%m-%d").replace(hour=h, minute=mi, tzinfo=ZoneInfo(tz))
    return d.astimezone(UTC)


def load_timer_spans(path: Path, tz: str) -> list[Span]:
    if not Path(path).exists():
        return []
    spans = []
    for task in json.loads(Path(path).read_text()):
        for e in task.get("entries", []):
            if e.get("kind", "timed") != "timed" or not e.get("startTime") or not e.get("endTime"):
                continue
            s = parse_local(e["day"], e["startTime"], tz)
            en = parse_local(e.get("endDay") or e["day"], e["endTime"], tz)
            if en <= s:
                continue
            spans.append(Span(s, en, task.get("title", ""), task.get("channel"), task.get("taskId")))
    return spans


def load_timebox_spans(path: Path) -> list[Span]:
    if not Path(path).exists():
        return []
    out = []
    for e in json.loads(Path(path).read_text()):
        s, en = datetime.fromisoformat(e["start"]).astimezone(UTC), datetime.fromisoformat(e["end"]).astimezone(UTC)
        if en > s:
            out.append(Span(s, en, e.get("title", ""), e.get("channel"), e.get("eventId")))
    return out


def calendar_spans(events) -> list[Span]:
    return [Span(e.start, e.end, e.title, None, e.id) for e in events]
