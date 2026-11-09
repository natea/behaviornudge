"""Live activity tracking: front app + browser tab -> live sessions with keys, labels, project.

Uses the backtest's activity keys, session rules (30 s merge gap, 3 s transitions) and labeling
layers (user labels -> rules -> cache -> optional background Jev), so live and historical agree.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from ..keys import KeyInfo, activity_key, is_browser
from ..labels import Label, LabelStore, overlay_user, rule_label

MERGE_GAP = timedelta(seconds=30)
TRANSITION = timedelta(seconds=3)
KEEP = timedelta(hours=4)


@dataclass
class LiveSession:
    start: datetime
    end: datetime
    key: str
    app: str
    domain: str | None
    path_class: str | None
    title: str | None = None
    project: str | None = None
    label: Label = field(default_factory=Label)
    closed: bool = False

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    def row(self) -> dict:
        """Shape used by labels/predictors (same keys as timeline session rows)."""
        return {"key": self.key, "app": self.app, "domain": self.domain, "path_class": self.path_class,
                "title": self.title, "project": self.project, "start_utc": self.start.isoformat(),
                "end_utc": self.end.isoformat(), "seconds": self.seconds, "mode": self.label.mode,
                "purpose": self.label.purpose, "alignment": self.label.alignment}


ProjectFor = Callable[[str, datetime], "str | None"]     # (app, time) -> terminal/editor project
LabelLookup = Callable[[dict], "tuple[Label | None, Label | None]"]  # session row -> (user/intent label, activity label)
JevSubmit = Callable[[dict, Callable[[Label], None]], None]


class LiveTracker:
    def __init__(self, channel_map: dict[str, str], project_for: ProjectFor | None = None,
                 lookup: LabelLookup | None = None, jev_submit: JevSubmit | None = None):
        self.channel_map = channel_map
        self.project_for = project_for
        self.lookup = lookup
        self.jev_submit = jev_submit
        self.sessions: list[LiveSession] = []
        self.front_bundle: str | None = None
        self.front_name: str | None = None
        self.url: str | None = None
        self.tab_title: str | None = None
        self.paused = False
        self._lock = threading.RLock()
        self._jev_pending: set[tuple] = set()
        self._awaiting_tab = False

    # --- inputs ------------------------------------------------------------------
    def on_front_app(self, bundle_id: str, name: str, now: datetime) -> None:
        with self._lock:
            self.front_bundle, self.front_name = bundle_id, name
            self.url, self.tab_title = None, None
            if is_browser(bundle_id, name):
                # wait for the tab read (the engine does it right away); just end the previous session
                cur = self.sessions[-1] if self.sessions else None
                if cur and not cur.closed and not self.paused:
                    self._close(cur, now)
                self._awaiting_tab = True
                return
            self._awaiting_tab = False
            self._observe(now)

    def on_tab(self, url: str | None, title: str | None, now: datetime) -> None:
        with self._lock:
            self.url, self.tab_title = url, title
            self._awaiting_tab = False
            self._observe(now)

    def on_idle(self, idle_s: float, locked: bool, now: datetime, idle_limit_s: float = 300) -> None:
        with self._lock:
            should_pause = locked or idle_s >= idle_limit_s
            if should_pause and not self.paused:
                self.paused = True
                if self.sessions and not self.sessions[-1].closed:
                    cut = now - timedelta(seconds=min(idle_s, (now - self.sessions[-1].start).total_seconds())) if not locked else now
                    self._close(self.sessions[-1], max(cut, self.sessions[-1].start))
            elif not should_pause and self.paused:
                self.paused = False
                self._observe(now)

    def tick(self, now: datetime) -> None:
        """Extend the open session to now (called by the engine loop)."""
        with self._lock:
            if self.paused or not self.sessions:
                return
            cur = self.sessions[-1]
            if not cur.closed:
                cur.end = max(cur.end, now)
            self._trim(now)

    # --- core --------------------------------------------------------------------
    def current(self) -> LiveSession | None:
        with self._lock:
            if self.sessions and not self.sessions[-1].closed:
                return self.sessions[-1]
            return None

    def recent(self, since: datetime) -> list[LiveSession]:
        with self._lock:
            return [s for s in self.sessions if s.end >= since]

    def _key(self, now: datetime) -> tuple[KeyInfo, str | None]:
        bundle, name = self.front_bundle, self.front_name
        info = activity_key(bundle, name, self.url)
        project = None
        if self.project_for and info.app in ("ghostty", "terminal", "iterm", "zed", "vscode", "cursor", "xcode"):
            project = self.project_for(info.app, now)
            if project:
                info = activity_key(bundle, name, self.url, project)
        return info, (info.project_hint or project)

    def _observe(self, now: datetime) -> None:
        if self.paused or not self.front_bundle or self._awaiting_tab:
            return
        info, project = self._key(now)
        title = self.tab_title if self.url else self.front_name
        cur = self.sessions[-1] if self.sessions else None
        if cur and not cur.closed and cur.key == info.key:
            cur.end = now
            cur.title = title or cur.title
            return
        if cur and not cur.closed:
            self._close(cur, now)
        prev = self.sessions[-1] if self.sessions else None
        if prev and prev.key == info.key and now - prev.end < MERGE_GAP:
            prev.closed, prev.end = False, now  # resume the same session across a short blip
            return
        s = LiveSession(now, now, info.key, info.app, info.domain, info.path_class, title, project)
        self._label(s)
        self.sessions.append(s)

    def _close(self, s: LiveSession, at: datetime) -> None:
        s.end, s.closed = at, True
        if s.end - s.start < TRANSITION:
            self.sessions.remove(s)  # a transition, not a session

    def _trim(self, now: datetime) -> None:
        self.sessions = [s for s in self.sessions if now - s.end < KEEP]

    # --- labels ------------------------------------------------------------------
    def _label(self, s: LiveSession) -> None:
        row = s.row()
        rule = rule_label(row, self.channel_map)
        user, act = self.lookup(row) if self.lookup else (None, None)
        lab = overlay_user(rule, user, act)
        if act:
            for d in ("mode", "purpose", "project"):
                lab.set(d, getattr(act, d), act.sources.get(d, "model"), act.confidence.get(d))
        s.label = lab
        if lab.mode is None and self.jev_submit:
            k = (s.key, (s.title or "")[:80])
            if k not in self._jev_pending:
                self._jev_pending.add(k)

                def apply(new: Label, key=k) -> None:
                    with self._lock:
                        for x in self.sessions:
                            if (x.key, (x.title or "")[:80]) == key:
                                for d in ("mode", "purpose", "project"):
                                    x.label.set(d, getattr(new, d), new.sources.get(d, "model"), new.confidence.get(d))
                        self._jev_pending.discard(key)

                self.jev_submit(row, apply)


def store_lookup(store: LabelStore, act_fp: str | None, intent_key: Callable[[dict], str | None]) -> LabelLookup:
    """Label lookup backed by the existing label store: intent-aware user labels + activity cache."""
    from ..jev import activity_cache_key

    def lookup(row: dict) -> tuple[Label | None, Label | None]:
        ik = intent_key(row)
        user = store.get(ik) if ik else None
        act = store.get(activity_cache_key(row["key"], row.get("title"), act_fp)) if act_fp else None
        return user, act

    return lookup
