"""`behaviornudge nudge run`: the live nudge engine in one process (design D1).

Wires: helper (notifications, front app, calls, idle) + browser tab poller + herdr subscriber +
Sunsama timer poller + calendar/timebox refresh -> live tracker -> intent -> rules -> delivery,
plus feedback, outcomes and shadow predictions. Also writes the live context log.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..config import Config, MissingSecretError, get_secret
from ..context import ContextLogger, TimerPoller, herdr_snapshot
from ..intent import ActivitySpan, Intent
from ..keys import is_browser
from ..labels import LabelStore, cache_key, definitions_fp
from ..pipeline import channel_maps, jev_fingerprint, sunsama_channels
from ..projects import discover_repos, project_for_folder
from . import content
from .browser import on_battery, poll_interval, read_tab
from .calls import CallTracker
from .feedback import FeedbackHandler, record_outcomes
from .helper import HelperClient, app_paths, build_helper
from .messaging import conversation_id, messaging_app, retrace_window
from .live_intent import LiveIntent, retrace_calendar_source, sunsama_timebox_source
from .rules import RuleEngine
from .settings import load_settings
from .shadow import ShadowScorer
from .status import PERMISSION_HELP
from .store import NudgeStore
from .tracker import LiveTracker, store_lookup

TERMINALS = ("ghostty", "terminal", "iterm")


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


class NudgeEngine:
    def __init__(self, cfg: Config, log=print):
        self.cfg = cfg
        self._log_fn = log
        self.st = load_settings(cfg.section("nudge"))
        d = cfg.data_dir
        self.status_path = d / "nudge_status.json"
        self.store = NudgeStore(d / "nudges.sqlite")
        self.labels = LabelStore(d / "labels.sqlite")
        self.defs = definitions_fp(cfg.section("labeling").get("modes", {}))
        self.act_fp = jev_fingerprint(cfg)
        proj2ch, ch2proj = channel_maps(cfg)
        self.proj2ch = proj2ch
        self.prone = cfg.section("drift")["prone_sites"]
        self.stop_event = threading.Event()
        self._notices: dict[str, float] = {}  # notice kind -> monotonic time last shown
        self.intent = Intent("none")
        self.lock = threading.RLock()

        # context log + herdr + Sunsama timer
        repos = d / "repos.json"
        self.repo_map = json.loads(repos.read_text()) if repos.exists() else discover_repos()
        self.ctx = ContextLogger(cfg.path("sources", "context_log"), lambda cwd: project_for_folder(cwd, self.repo_map))
        self.agents: dict[str, tuple[datetime, str | None]] = {}  # pane -> (working since, project)
        _orig_write = self.ctx.write

        def write(entry: dict) -> None:
            _orig_write(entry)
            self._on_context(entry)
        self.ctx.write = write

        # intent
        icfg = cfg.section("intent")
        token = d / "sunsama_oauth.json"
        self.live_intent = LiveIntent(
            retrace_calendar_source(cfg.path("sources", "retrace_calendar_helper"), icfg.get("calendar_allowlist", [])),
            self._sunsama(sunsama_timebox_source(token)) if token.exists() else None,
            icfg.get("recent_creative_window_min", 30), icfg.get("timer_cap_min", 45), ch2proj, log=self.log)
        self.timer_source = None
        if cfg.section("sources").get("sunsama_timer") == "mcp" and token.exists():
            from ..sunsama_mcp import fetch

            def timer():
                payload = fetch(token)
                self.live_intent.on_timer(payload)
                return payload
            self.timer_source = self._sunsama(timer)

        # tracker with live labels
        self.jev_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="jev")
        self.jev = self._make_jev()
        self.tracker = LiveTracker(proj2ch, project_for=self._terminal_project,
                                   lookup=store_lookup(self.labels, self.act_fp, self._intent_key),
                                   jev_submit=self._jev_submit if self.jev else None)

        # calls, rules, feedback, shadow
        self.calls = CallTracker(self.st.call_end_grace_s, self._meeting_at, self.ctx.write)
        self.rules = RuleEngine(self.st, self.store, self.prone, cfg.timezone)
        from ..retrace_agent import AgentConfig
        self.retrace_status = AgentConfig.from_sources(cfg.section("sources")).status_path
        self.feedback = FeedbackHandler(self.store, self.st, self.labels, self.defs)
        self.shadow = ShadowScorer(self.store, *self._predictors(), log=self.log)

        # helper
        build_helper(d, self.st.sounds)
        _, exe = app_paths(d)
        self.helper = HelperClient([str(exe)], self._on_helper, log=self.log)

    # --- plumbing ----------------------------------------------------------------
    NOTICE_EVERY_S = 3600

    def notice(self, kind: str, title: str, body: str) -> bool:
        """A plain heads-up notification (no nudge buttons, not a nudge), at most once an hour per kind."""
        last = self._notices.get(kind)
        if last is not None and time.monotonic() - last < self.NOTICE_EVERY_S:
            return False
        self._notices[kind] = time.monotonic()
        self.log(f"notice [{kind}] {title} | {body}")
        return self.helper.notify(f"notice-{kind}-{int(time.time())}", title, body, None, "notice")

    def _sunsama(self, source):
        """Wrap a Sunsama call so an expired sign-in shows one notice (engine-reliability D5)."""
        from ..sunsama_mcp import needs_login

        def call():
            try:
                return source()
            except Exception as e:
                if needs_login(e):
                    self.notice("sunsama", "Sunsama sign-in expired",
                                "Timers aren't being read. Run: uv run behaviornudge sunsama-timer --login")
                raise
        return call

    def _foreign_tab(self, tab_title: str, window_title: str) -> None:
        self.notice("browser", "Can't see your browser tab",
                    "Another copy of the browser (an automation browser?) is answering instead of the window "
                    "in front. Close it so drift nudges work.")

    def log(self, msg: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        if self._log_fn is print:
            print(line, flush=True)  # flush so `nudge run > file` logs immediately
        else:
            self._log_fn(line)

    def _make_jev(self):
        if not self.st.live_jev:
            return None
        try:
            get_secret("TYPESAFE_API_KEY")
        except MissingSecretError:
            self.log("live Jev labeling off: TYPESAFE_API_KEY not set")
            return None
        from ..jev import JevLabeler
        return JevLabeler(self.cfg, sunsama_channels(self.cfg))

    def _jev_submit(self, row: dict, apply) -> None:
        from ..jev import activity_cache_key

        def job():
            try:
                lab, review = self.jev.label_one(row, None)
                if review == "queue":
                    lab.mode = None
                    lab.sources.pop("mode", None)
                self.labels.put(activity_cache_key(row["key"], row.get("title"), self.jev.fp), lab)
                self.labels.commit()
                apply(lab)
            except Exception as e:
                self.log(f"live Jev label failed for {row['key']}: {e}")
        self.jev_pool.submit(job)

    def _predictors(self):
        from ..predictors import create
        primary = fallback = None
        try:
            fallback = create("baseline", self.cfg)
            from ..episodes import load as load_episodes
            from ..pipeline import read_labeled
            ep = self.cfg.data_dir / "episodes.json"
            if ep.exists():
                fallback.fit(read_labeled(self.cfg), load_episodes(ep))
        except Exception as e:
            self.log(f"baseline predictor unavailable: {e}")
        enabled = self.cfg.section("predictors").get("enabled", [])
        if "behaviorgpt" in enabled:
            try:
                primary = create("behaviorgpt", self.cfg)
            except Exception as e:
                self.log(f"BehaviorGPT shadow predictor unavailable: {e}")
        return primary or fallback, fallback if primary else None

    def _intent_key(self, row: dict) -> str | None:
        with self.lock:
            it = self.intent
        return cache_key(row, it, self.defs) if it.known else None

    def _terminal_project(self, app: str, t: datetime) -> str | None:
        if app in TERMINALS and self.ctx.last_focus:
            _, cwd = self.ctx.last_focus
            return project_for_folder(cwd, self.repo_map) if cwd else None
        return None

    def _meeting_at(self, t: datetime) -> str | None:
        cal = self.live_intent.calendar
        hits = [s for s in cal if s.start <= t < s.end]
        return min(hits, key=lambda s: s.end - s.start).title if hits else None

    def _on_context(self, entry: dict) -> None:
        if entry.get("type") != "agent_status":
            return
        pane = entry.get("pane_id") or "?"
        if entry.get("status") == "working":
            if pane not in self.agents:
                project = None
                try:
                    snap = herdr_snapshot()
                    p = next((x for x in snap.get("panes", []) if x.get("pane_id") == pane), None)
                    cwd = p and (p.get("foreground_cwd") or p.get("cwd"))
                    project = project_for_folder(cwd, self.repo_map) if cwd else None
                except Exception:
                    pass
                self.agents[pane] = (datetime.fromisoformat(entry["time"]), project)
        else:
            self.agents.pop(pane, None)

    def _agent(self) -> tuple[datetime | None, str | None]:
        if not self.agents:
            return None, None
        since, project = min(self.agents.values(), key=lambda v: v[0])
        return since, project

    # --- helper events --------------------------------------------------------------
    def _on_helper(self, ev: dict) -> None:
        now = utcnow()
        kind = ev.get("event")
        if kind == "ready":
            if ev.get("authorized"):
                self.log("helper ready: notifications allowed")
            else:
                self.log("helper ready: notifications NOT allowed. " + PERMISSION_HELP)
            self.helper.configure(call_allowlist=self.st.call_allowlist, call_end_grace_s=self.st.call_end_grace_s)
        elif kind == "front_app":
            self.tracker.on_front_app(ev.get("bundle_id", ""), ev.get("name", ""), now)
            if is_browser(ev.get("bundle_id"), ev.get("name")):
                url, title = read_tab(ev.get("bundle_id", ""), on_foreign=self._foreign_tab)
                self.tracker.on_tab(url, title, utcnow())
        elif kind == "calls":
            self.calls.on_calls(ev.get("active") or [], now)
        elif kind == "idle":
            self.tracker.on_idle(float(ev.get("idle_s") or 0), bool(ev.get("locked")), now)
        elif kind in ("action", "dismissed"):
            action = ev.get("action") if kind == "action" else "dismissed"
            try:
                nid = int(ev.get("id"))
            except (TypeError, ValueError):
                return
            try:
                effect = self.feedback.apply(nid, action, now)
                self.log(f"feedback on nudge {nid}: {effect}")
            except (KeyError, ValueError) as e:
                self.log(f"feedback ignored: {e}")
        elif kind == "error":
            self.log(f"helper error: {ev.get('detail')}")

    # --- loops ------------------------------------------------------------------------
    def _browser_loop(self) -> None:
        battery_checked, battery = 0.0, False
        while not self.stop_event.is_set():
            if time.monotonic() - battery_checked > 60:
                battery, battery_checked = on_battery(), time.monotonic()
            bundle = self.tracker.front_bundle
            if bundle and is_browser(bundle, self.tracker.front_name):
                url, title = read_tab(bundle, on_foreign=self._foreign_tab)
                if (url, title) != (self.tracker.url, self.tracker.tab_title):
                    self.tracker.on_tab(url, title, utcnow())
            self.stop_event.wait(poll_interval(battery))

    def _activity_spans(self) -> list[ActivitySpan]:
        out = []
        for s in self.tracker.recent(utcnow() - timedelta(hours=2)):
            proj = s.label.project if s.label.project not in (None, "none") else s.project
            out.append(ActivitySpan(s.start, s.end, s.key, proj, s.label.mode))
        return out

    def tick(self, now: datetime) -> None:
        self.tracker.tick(now)
        self.calls.tick(now)
        intent = self.live_intent.resolve(now, self._activity_spans())
        with self.lock:
            self.intent = intent
        if self.tracker.paused:
            return
        sessions = self.tracker.recent(now - timedelta(hours=2))
        self._note_conversation(sessions, now)
        since, project = self._agent()
        d = self.rules.evaluate(now, sessions, intent, call=self.calls.state if self.calls.state.active else None,
                                agent_working_since=since, agent_project=project)
        if not d:
            return
        nid = self.store.add_nudge(time=now, type=d.type, fired=d.fired, reason=d.reason, activity_key=d.key,
                                   title=d.title, site=d.site, intent_title=d.intent.title, intent_source=d.intent.source,
                                   intent_ref=d.intent.ref or d.intent.title, context={"stretch_s": d.stretch_s})
        if d.fired:
            c = content.build(d.type, site=d.site, intent_title=d.intent.title, intent_source=d.intent.source,
                              stretch_s=d.stretch_s, call_name=d.call_name, agent_project=d.agent_project,
                              feed=(d.type == "arrival" and d.stretch_s > 0),
                              app=d.site if d.type == "messaging" else None, hopping=d.hopping)
            in_call = self.calls.state.active
            sound = None if in_call else self.st.sound(d.type)  # never play a sound into a call
            sent = self.helper.notify(nid, c.title, c.body, sound, d.type)
            self.log(f"nudge {nid} [{d.type}] {c.title} | {c.body}" + ("" if sent else " (helper unavailable)"))
        else:
            self.log(f"nudge moment {d.type} suppressed: {d.reason}")
        history = [s.row() for s in sessions if s.closed and s.end <= now][-50:]
        self.shadow.submit(nid, history, now, asdict(d.intent), d.key or "")

    def _note_conversation(self, sessions, now: datetime) -> None:
        """While messaging, tell the rules which conversation is open (tab title or Retrace's window title)."""
        cur = sessions[-1] if sessions and not sessions[-1].closed else None
        app = messaging_app(cur, self.st.messaging_apps) if cur and self.st.messaging_apps else None
        if not app:
            return
        title = cur.title if cur.domain else retrace_window(self.retrace_status, app, now)
        cid = conversation_id(title, app)
        if cid:
            self.rules.note_conversation(cid, now)

    def _write_status(self) -> None:
        with self.lock:
            it = self.intent
        cur = self.tracker.current()
        current = None
        if cur:
            from .rules import alignment
            current = {"key": cur.key, "mode": cur.label.mode, "alignment": alignment(cur, it), "seconds": round(cur.seconds)}
        recent = [{"key": x.key, "mode": x.label.mode, "seconds": round(x.seconds)}
                  for x in self.tracker.recent(utcnow() - timedelta(minutes=30))[-6:]]
        data = {"pid": os.getpid(), "updated": utcnow().isoformat(), "authorized": self.helper.authorized,
                "current": current, "recent": recent,
                "intent": {"title": it.title, "source": it.source}, "helper_restarts": self.helper.restarts,
                "call": asdict(self.calls.state) if self.calls.state.active else None}
        self.status_path.write_text(json.dumps(data, default=str))

    def run(self) -> None:
        import signal

        def _stop(signum, frame):
            self.log(f"received signal {signum}; stopping")
            self.stop_event.set()
        # Stop cleanly on Ctrl-C and on SIGTERM (kill, launchd), even if started with SIGINT ignored.
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, _stop)
        self.helper.start()
        threading.Thread(target=self._browser_loop, daemon=True, name="browser").start()
        threading.Thread(target=self.ctx.run, args=(self.stop_event,), daemon=True, name="herdr").start()
        if self.timer_source:
            threading.Thread(target=TimerPoller(self.ctx, self.timer_source, 30).run, args=(self.stop_event,),
                             daemon=True, name="sunsama").start()
        self.log("nudge engine running (Ctrl-C to stop)")
        last_outcomes = last_status = 0.0
        try:
            while not self.stop_event.is_set():
                now = utcnow()
                try:
                    self.tick(now)
                except Exception as e:
                    self.log(f"tick error: {type(e).__name__}: {e}")
                if time.monotonic() - last_outcomes > 60:
                    record_outcomes(self.store, self.tracker.recent(now - timedelta(hours=4)), now)
                    last_outcomes = time.monotonic()
                if time.monotonic() - last_status > 2:
                    self._write_status()
                    last_status = time.monotonic()
                self.stop_event.wait(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        self.stop_event.set()
        self.helper.stop()
        self.shadow.shutdown()
        self.jev_pool.shutdown(wait=False, cancel_futures=True)
        try:
            self.status_path.unlink()
        except FileNotFoundError:
            pass
        self.log("nudge engine stopped")
