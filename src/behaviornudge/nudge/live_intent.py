"""Live intent: what the user is supposed to be doing right now.

Sunsama timer (polled, with the forgotten-timer cap) -> today's Sunsama timebox -> allowlisted
calendar block -> recent creative live session -> none. Calendar and timeboxes refresh every 5 min.
"""

from __future__ import annotations

import threading
import time as _time
from datetime import UTC, datetime, timedelta
from typing import Callable

from ..intent import ActivitySpan, Intent, IntentResolver, NO_INTENT, Span, cap_timers, default_related

REFRESH_S = 300


def timer_span(timer: dict | None, now: datetime) -> Span | None:
    """Sunsama active-timer payload -> a span from its start until now."""
    if not timer or not timer.get("hasActiveTimer") or not timer.get("activeTimer"):
        return None
    t = timer["activeTimer"]
    try:
        start = datetime.fromisoformat(t["startTime"].replace("Z", "+00:00")).astimezone(UTC)
    except (KeyError, ValueError):
        return None
    return Span(start, now + timedelta(seconds=1), t.get("taskTitle", ""), t.get("channel"), t.get("taskId"))


def timebox_spans(sunsama_events: dict) -> list[Span]:
    out = []
    for e in sunsama_events.get("events", []):
        if e.get("calendarName") != "Sunsama Calendar":
            continue
        s, en = (e.get("start") or {}).get("dateTime"), (e.get("end") or {}).get("dateTime")
        if s and en:
            out.append(Span(datetime.fromisoformat(s).astimezone(UTC), datetime.fromisoformat(en).astimezone(UTC),
                            e.get("title", ""), None, e.get("eventId")))
    return out


class LiveIntent:
    def __init__(self, calendar_source: Callable[[], list[Span]], timebox_source: Callable[[], list[Span]] | None = None,
                 recent_window_min: float = 30, timer_cap_min: float = 45, channel_to_project: dict | None = None,
                 log: Callable[[str], None] = lambda m: None, clock: Callable[[], float] = _time.monotonic):
        self.calendar_source = calendar_source
        self.timebox_source = timebox_source
        self.window = recent_window_min
        self.cap = timer_cap_min
        self.ch2proj = channel_to_project or {}
        self.log = log
        self.clock = clock
        self.timer: dict | None = None
        self.calendar: list[Span] = []
        self.timeboxes: list[Span] = []
        self._last_refresh = -1e9
        self._lock = threading.Lock()

    def on_timer(self, payload: dict) -> None:
        with self._lock:
            self.timer = payload

    def refresh(self, force: bool = False) -> None:
        if not force and self.clock() - self._last_refresh < REFRESH_S:
            return
        self._last_refresh = self.clock()
        try:
            cal = self.calendar_source()
            with self._lock:
                self.calendar = cal
        except Exception as e:
            self.log(f"calendar refresh failed: {e}")
        if self.timebox_source:
            try:
                tb = self.timebox_source()
                with self._lock:
                    self.timeboxes = tb
            except Exception as e:
                self.log(f"Sunsama timebox refresh failed: {e}")

    def resolve(self, now: datetime, activity: list[ActivitySpan]) -> Intent:
        self.refresh()
        with self._lock:
            ts = timer_span(self.timer, now)
            cal, tbs = list(self.calendar), list(self.timeboxes)
        timers = []
        if ts:
            capped = cap_timers([ts], activity, default_related(self.ch2proj), self.cap)[0]
            # a timer older than the cap with no related activity no longer counts (forgotten timer)
            if capped.capped_end >= now or now - ts.start < timedelta(minutes=self.cap):
                timers.append(ts)
        resolver = IntentResolver(timers, tbs, cal, activity, self.window, self.ch2proj)
        return resolver.resolve(now)


def retrace_calendar_source(helper_path, allowlist: list[str]) -> Callable[[], list[Span]]:
    """Today's allowlisted events (including the one in progress) from Retrace's EventKit helper."""
    from ..calendar import parse_helper_output, run_helper
    from ..intent import calendar_spans

    def load() -> list[Span]:
        return calendar_spans(parse_helper_output(run_helper(helper_path, 1), allowlist))

    return load


def sunsama_timebox_source(token_path) -> Callable[[], list[Span]]:
    from ..sunsama_mcp import fetch_resource

    def load() -> list[Span]:
        today = datetime.now().date().isoformat()
        return timebox_spans(fetch_resource(token_path, f"sunsama://calendar/events/{today}"))

    return load
