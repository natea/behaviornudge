"""Live context log: herdr focus/folder/agent-status events (and, when configured, the
Sunsama active timer), appended to data/context-log.jsonl. Read-only: the logger only
subscribes and reads; it never changes herdr, Sunsama or Retrace state.
"""

from __future__ import annotations

import bisect
import json
import os
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable

FOCUS_EVENTS = {"pane_focused", "tab_focused", "workspace_focused", "pane_updated",
                "pane.focused", "tab.focused", "workspace.focused", "pane.updated"}
AGENT_EVENTS = {"pane.agent_status_changed", "pane_agent_status_changed"}
CREATED_EVENTS = {"pane_created", "pane.created"}
GLOBAL_SUBS = ["pane.focused", "tab.focused", "workspace.focused", "pane.updated", "pane.created"]


# ---------------------------------------------------------------------------
# reading the log
# ---------------------------------------------------------------------------

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


class FocusIndex:
    """Which project herdr's focused pane was in at a time (from logged focus entries)."""

    def __init__(self, path: Path):
        entries = [e for e in read_log(path) if e.get("type") == "focus" and e.get("project")]
        entries.sort(key=lambda e: e["time"])
        self.times = [datetime.fromisoformat(e["time"]) for e in entries]
        self.projects = [e["project"] for e in entries]

    def project_at(self, app: str, t: datetime) -> str | None:
        i = bisect.bisect_right(self.times, t) - 1
        return self.projects[i] if i >= 0 else None

    def __bool__(self) -> bool:
        return bool(self.times)


# ---------------------------------------------------------------------------
# the logger
# ---------------------------------------------------------------------------

def herdr_socket_path() -> str:
    return os.environ.get("HERDR_SOCKET_PATH") or os.path.expanduser("~/.config/herdr/herdr.sock")


def herdr_snapshot() -> dict:
    res = subprocess.run(["herdr", "api", "snapshot"], capture_output=True, text=True, timeout=10)
    return json.loads(res.stdout)["result"]["snapshot"]


def focused_pane(snap: dict) -> dict | None:
    fid = snap.get("focused_pane_id")
    return next((p for p in snap.get("panes", []) if p.get("pane_id") == fid), None)


def now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


class ContextLogger:
    def __init__(self, log_path: Path, project_for: Callable[[str], str | None],
                 snapshot: Callable[[], dict] = herdr_snapshot, clock: Callable[[], str] = now_iso):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.project_for = project_for
        self.snapshot = snapshot
        self.clock = clock
        self.last_focus: tuple | None = None
        self._lock = threading.Lock()

    def write(self, entry: dict) -> None:
        with self._lock, self.log_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def subscriptions(self, snap: dict) -> list[dict]:
        subs = [{"type": t} for t in GLOBAL_SUBS]
        subs += [{"type": "pane.agent_status_changed", "pane_id": p["pane_id"]} for p in snap.get("panes", [])]
        return subs

    def record_focus(self, snap: dict | None = None) -> dict | None:
        snap = snap or self.snapshot()
        p = focused_pane(snap)
        if not p:
            return None
        cwd = p.get("foreground_cwd") or p.get("cwd")
        key = (p.get("pane_id"), cwd)
        if key == self.last_focus:
            return None
        self.last_focus = key
        entry = {"time": self.clock(), "type": "focus", "pane_id": p.get("pane_id"), "cwd": cwd,
                 "project": self.project_for(cwd) if cwd else None, "agent": p.get("agent"),
                 "agent_status": p.get("agent_status")}
        self.write(entry)
        return entry

    def handle(self, line: str) -> tuple[list[dict], list[str]]:
        """Process one socket line. Returns (log entries written, new pane ids to subscribe)."""
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return [], []
        if "id" in msg:  # request acknowledgement or error
            if "error" in msg:
                entry = {"time": self.clock(), "type": "error", "detail": msg["error"]}
                self.write(entry)
                return [entry], []
            return [], []
        kind, data = msg.get("event"), msg.get("data") or {}
        if kind in AGENT_EVENTS:
            entry = {"time": self.clock(), "type": "agent_status", "pane_id": data.get("pane_id"),
                     "status": data.get("agent_status"), "agent": data.get("agent")}
            self.write(entry)
            return [entry], []
        if kind in CREATED_EVENTS:
            pid = data.get("pane_id") or (data.get("pane") or {}).get("pane_id")
            return [], [pid] if pid else []
        if kind in FOCUS_EVENTS:
            e = self.record_focus()
            return ([e] if e else []), []
        return [], []

    def run(self, stop: threading.Event | None = None) -> None:  # pragma: no cover - live loop
        stop = stop or threading.Event()
        while not stop.is_set():
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(herdr_socket_path())
                s.settimeout(1.0)
                snap = self.snapshot()
                self._send(s, "sub0", self.subscriptions(snap))
                self.record_focus(snap)
                buf, n = b"", 0
                while not stop.is_set():
                    try:
                        chunk = s.recv(65536)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        _, new_panes = self.handle(line.decode("utf-8", "replace"))
                        for pid in new_panes:
                            n += 1
                            self._send(s, f"sub{n}", [{"type": "pane.agent_status_changed", "pane_id": pid}])
            except (OSError, json.JSONDecodeError, subprocess.SubprocessError) as e:
                self.write({"time": self.clock(), "type": "error", "detail": f"{type(e).__name__}: {e}"})
            stop.wait(5.0)  # reconnect after herdr restarts

    @staticmethod
    def _send(s: socket.socket, rid: str, subs: list[dict]) -> None:
        s.sendall((json.dumps({"id": rid, "method": "events.subscribe", "params": {"subscriptions": subs}}) + "\n").encode())


class TimerPoller:
    """Polls the Sunsama active timer (`{"hasActiveTimer":..,"activeTimer":..}`) and logs changes.
    `source` is either a command (list of args printing that JSON) or a zero-argument callable."""

    def __init__(self, logger: ContextLogger, source, every_s: float = 30):
        self.logger, self.source, self.every = logger, source, every_s
        self.last = None

    def poll_once(self) -> dict | None:
        if callable(self.source):
            data = self.source()
        else:
            res = subprocess.run(self.source, capture_output=True, text=True, timeout=20)
            data = json.loads(res.stdout)
        timer = data.get("activeTimer") if data.get("hasActiveTimer") else None
        ident = (timer or {}).get("taskId")
        if ident == self.last:
            return None
        self.last = ident
        entry = {"time": self.logger.clock(), "type": "sunsama_timer", "task_id": ident,
                 "title": (timer or {}).get("taskTitle"), "started": (timer or {}).get("startTime")}
        self.logger.write(entry)
        return entry

    def run(self, stop: threading.Event) -> None:  # pragma: no cover - live loop
        while not stop.is_set():
            try:
                self.poll_once()
            except Exception as e:
                self.logger.write({"time": self.logger.clock(), "type": "error", "detail": f"sunsama: {e}"})
            stop.wait(self.every)
