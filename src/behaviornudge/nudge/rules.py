"""Nudge rules (design D5): when a nudge fires, decided in code.

Moments: arrival, purpose-done, off-task, in-call, agent-working. Each fires at most once per
occurrence (visit, stretch, call, agent run), whether it was delivered or suppressed by a gate.
Gates, in order: global switch -> quiet hours -> snooze -> legit suppression -> cooldown -> budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from ..episodes import PURPOSEFUL, site_of
from ..intent import Intent
from ..labels import rule_alignment
from .settings import NudgeSettings
from .store import NudgeStore

PRECEDENCE = ["in_call", "purpose_done", "arrival", "agent_working", "off_task"]
PURPOSE_WINDOW = timedelta(minutes=15)
RETURN_S = 60
AGENT_MIN_WORKING_S = 30


@dataclass
class Decision:
    type: str
    fired: bool
    reason: str | None
    key: str | None
    site: str | None
    intent: Intent
    stretch_s: float = 0
    call_name: str | None = None
    agent_project: str | None = None
    title: str | None = None


@dataclass
class _Visit:
    start: datetime
    key: str
    site: str
    handled: set = field(default_factory=set)  # moment types already decided for this visit


def alignment(s, intent: Intent) -> str | None:
    """Session alignment: the user's own label wins; otherwise decided in code from labels + intent."""
    lab = s.label
    if lab.sources.get("alignment") == "user":
        return lab.alignment
    return rule_alignment(s.row(), lab, intent)


def is_drift(s, intent: Intent) -> bool:
    return s.label.mode == "consuming" and alignment(s, intent) in ("off-task", "no-intent")


def is_work(s, intent: Intent) -> bool:
    return (s.label.mode == "creative" or alignment(s, intent) == "on-task") and s.seconds >= RETURN_S


class RuleEngine:
    def __init__(self, settings: NudgeSettings, store: NudgeStore, prone_sites: list[str], tz: str,
                 clock_today: Callable[[datetime], datetime] | None = None):
        self.st = settings
        self.store = store
        self.prone = prone_sites
        self.tz = ZoneInfo(tz)
        self.visit: _Visit | None = None
        self.stretch_handled: dict[str, datetime] = {}  # moment -> stretch start already decided
        self.call_handled: datetime | None = None
        self.feed_handled: datetime | None = None  # start of the same-site consuming run already decided
        self.agent_handled: datetime | None = None

    # --- helpers --------------------------------------------------------------
    def delay(self, nudge_type: str, site: str | None, intent: Intent) -> float:
        return self.st.base_delay_s(nudge_type) * self.store.multiplier(nudge_type, site, intent.source)

    def _drift_stretch(self, sessions, intent: Intent, now: datetime) -> tuple[float, datetime | None]:
        """Seconds of off-task consuming since the last >=60 s return to work, and the stretch start."""
        total, start = 0.0, None
        for s in reversed(sessions):
            if is_work(s, intent):
                break
            if is_drift(s, intent):
                total += (min(s.end, now) - s.start).total_seconds()
                start = s.start
        return total, start

    def _away_from_call(self, s) -> bool:
        """During a call: consuming anything, or reactive on a drift-prone site (LinkedIn messages).
        Call-app chat, team chat and work tools don't count."""
        if s.label.mode == "consuming":
            return True
        return s.label.mode == "reactive" and site_of(s.domain, self.prone) is not None

    def _away_run(self, sessions, now: datetime) -> tuple[float, datetime | None]:
        """Trailing time away from the call; 60 s back in the call or at work ends the stretch."""
        total, start = 0.0, None
        for s in reversed(sessions):
            if self._away_from_call(s):
                total += (min(s.end, now) - s.start).total_seconds()
                start = s.start
            elif s.seconds >= RETURN_S:
                break
        return total, start

    def _site_consuming_run(self, sessions, site: str, now: datetime) -> tuple[float, datetime | None]:
        """Trailing consuming time on one drift-prone site (feed/post hops add up), and its start."""
        total, start = 0.0, None
        for s in reversed(sessions):
            if site_of(s.domain, self.prone) == site and s.label.mode == "consuming":
                total += (min(s.end, now) - s.start).total_seconds()
                start = s.start
            elif s.seconds >= RETURN_S or site_of(s.domain, self.prone) != site:
                break
        return total, start

    def _consuming_run(self, sessions, now: datetime, intent: Intent | None = None, drift_only: bool = False) -> float:
        """Trailing contiguous consuming time (gaps of other activity < 60 s don't break it)."""
        total = 0.0
        for s in reversed(sessions):
            consuming = s.label.mode == "consuming" and (not drift_only or is_drift(s, intent))
            if consuming:
                total += (min(s.end, now) - s.start).total_seconds()
            elif s.seconds >= RETURN_S:
                break
        return total

    # --- gates ----------------------------------------------------------------
    def _quiet(self, now: datetime) -> bool:
        loc = now.astimezone(self.tz).time()
        for a, b in self.st.quiet_hours:
            ta, tb = time.fromisoformat(a), time.fromisoformat(b)
            if (ta <= tb and ta <= loc < tb) or (ta > tb and (loc >= ta or loc < tb)):
                return True
        return False

    def gate(self, nudge_type: str, key: str | None, site: str | None, intent: Intent, now: datetime) -> str | None:
        if not self.st.enabled or not self.store.get_state("enabled", True):
            return "off"
        if self._quiet(now):
            return "quiet_hours"
        snooze = self.store.get_state("snooze_until")
        if snooze and datetime.fromisoformat(snooze) > now:
            return "snoozed"
        if key and self.store.is_suppressed(key, intent.ref or intent.title):
            return "legit"
        last = self.store.last_fired(nudge_type, site)
        if last and now - last < timedelta(minutes=self.st.cooldown_min):
            return "cooldown"
        midnight = now.astimezone(self.tz).replace(hour=0, minute=0, second=0, microsecond=0)
        if self.store.fired_count_since(midnight) >= self.st.daily_budget:
            return "budget"
        return None

    # --- evaluation -------------------------------------------------------------
    def evaluate(self, now: datetime, sessions: list, intent: Intent, call=None,
                 agent_working_since: datetime | None = None, agent_project: str | None = None) -> Decision | None:
        """Return at most one decision (fired or suppressed) for this tick."""
        cur = sessions[-1] if sessions and not sessions[-1].closed else None
        candidates: dict[str, Decision] = {}

        # visit tracking: a purposeful session on a drift-prone site starts a visit (unless one is
        # active); a visit lasts 15 minutes, like the backtest's episode window
        if self.visit and now - self.visit.start > PURPOSE_WINDOW:
            self.visit = None
        if cur and self.visit is None:
            site = site_of(cur.domain, self.prone)
            prev = next((x for x in reversed(sessions[:-1]) if x.seconds >= 3), None)
            # a post opened from the same site's feed is part of the scroll, not a purposeful visit
            from_same_feed = prev is not None and site_of(prev.domain, self.prone) == site and prev.label.mode == "consuming" \
                and (cur.start - prev.end).total_seconds() < 30
            if site and cur.label.purpose in PURPOSEFUL and not from_same_feed:
                self.visit = _Visit(cur.start, cur.key, site)

        drift_s, stretch_start = self._drift_stretch(sessions, intent, now)

        # arrival
        if (cur and self.visit and cur.key == self.visit.key and "arrival" not in self.visit.handled and intent.known
                and alignment(cur, intent) != "on-task"
                and (now - cur.start).total_seconds() >= self.delay("arrival", self.visit.site, intent)):
            candidates["arrival"] = Decision("arrival", True, None, cur.key, self.visit.site, intent, title=cur.title)

        # feed landing: straight onto a drift-prone site's consuming page (no purposeful visit)
        if cur and "arrival" not in candidates and intent.known and cur.label.mode == "consuming":
            site = site_of(cur.domain, self.prone)
            run, run_start = self._site_consuming_run(sessions, site, now) if site else (0.0, None)
            if (site and run_start and self.feed_handled != run_start and not (self.visit and self.visit.site == site)
                    and alignment(cur, intent) != "on-task"
                    and run >= self.st.feed_arrival_delay_s * self.store.multiplier("arrival", site, intent.source)):
                candidates["arrival"] = Decision("arrival", True, None, cur.key, site, intent, stretch_s=run, title=cur.title)
                feed_landing = run_start
            else:
                feed_landing = None
        else:
            feed_landing = None

        # purpose done: after the visit, consuming off-task / no-intent for the delay
        if cur and self.visit and "purpose_done" not in self.visit.handled and cur.key != self.visit.key and is_drift(cur, intent):
            run = self._consuming_run(sessions, now, intent, drift_only=True)
            if run >= self.delay("purpose_done", site_of(cur.domain, self.prone) or self.visit.site, intent):
                candidates["purpose_done"] = Decision("purpose_done", True, None, cur.key, self.visit.site, intent,
                                                      stretch_s=run, title=cur.title)

        # off-task: stretch of off-task consuming while an intent exists
        if cur and intent.known and stretch_start and self.stretch_handled.get("off_task") != stretch_start:
            site = site_of(cur.domain, self.prone) or cur.domain
            if drift_s >= self.delay("off_task", site, intent):
                candidates["off_task"] = Decision("off_task", True, None, cur.key, site, intent, stretch_s=drift_s, title=cur.title)

        # in a call and away from it: consuming, or reactive on a drift-prone site (e.g. LinkedIn messages)
        if cur and call is not None and getattr(call, "active", False) and self._away_from_call(cur):
            run, run_start = self._away_run(sessions, now)
            site = site_of(cur.domain, self.prone) or cur.domain
            call_intent = Intent("call", call.name, None, None, f"call:{call.started.isoformat()}")
            if run_start and self.call_handled != run_start and run >= self.delay("in_call", site, call_intent):
                candidates["in_call"] = Decision("in_call", True, None, cur.key, site, call_intent, stretch_s=run,
                                                 call_name=call.name, title=cur.title)
                in_call_stretch = run_start
            else:
                in_call_stretch = None
        else:
            in_call_stretch = None

        # agent working and drifting
        if (cur and agent_working_since and self.agent_handled != agent_working_since
                and (now - agent_working_since).total_seconds() >= AGENT_MIN_WORKING_S and cur.label.mode == "consuming"):
            run = self._consuming_run(sessions, now, intent, drift_only=True)
            site = site_of(cur.domain, self.prone) or cur.domain
            agent_intent = Intent("agent", agent_project, None, agent_project, f"agent:{agent_working_since.isoformat()}")
            if run >= self.delay("agent_working", site, agent_intent):
                candidates["agent_working"] = Decision("agent_working", True, None, cur.key, site, agent_intent,
                                                       stretch_s=run, agent_project=agent_project, title=cur.title)

        if call is not None and getattr(call, "active", False) and self.st.calls_silence_others:
            # during a call the call is the intent: only the (silent) in-call nudge may appear
            candidates = {k: v for k, v in candidates.items() if k == "in_call"}
        if not candidates:
            return None
        nudge_type = next(t for t in PRECEDENCE if t in candidates)
        d = candidates[nudge_type]
        # mark the occurrence as decided (fired or suppressed) so it isn't re-evaluated every tick
        if nudge_type == "arrival" and feed_landing is not None:
            self.feed_handled = feed_landing
        elif nudge_type in ("arrival", "purpose_done") and self.visit:
            self.visit.handled.add(nudge_type)
        elif nudge_type == "off_task":
            self.stretch_handled["off_task"] = stretch_start
        elif nudge_type == "in_call":
            self.call_handled = in_call_stretch  # once per away-stretch, not once per call
        elif nudge_type == "agent_working":
            self.agent_handled = agent_working_since
        reason = self.gate(nudge_type, d.key, d.site, d.intent, now)
        d.fired, d.reason = reason is None, reason
        return d
