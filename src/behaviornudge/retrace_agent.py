"""Keep jhammant Retrace running under launchd, and tell when its capture has stopped.

A per-user LaunchAgent starts `python -m retrace.cli serve` at login and restarts it when it
exits. launchd-started processes don't inherit a terminal's privacy permissions, so Screen
Recording has to be granted once to the Python interpreter the agent runs; `install` checks
for a real new capture and names that interpreter when the grant is missing.
"""

from __future__ import annotations

import json
import os
import plistlib
import signal
import subprocess
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LABEL = "com.behaviornudge.retrace"
DEFAULT_REPO = "~/Documents/code/retrace"
DEFAULT_API = "http://127.0.0.1:8765"
STALE_HEARTBEAT_S = 300
QUIET_CAPTURE_S = 600
VERIFY_TIMEOUT_S = 120
SCREEN_SETTINGS = "System Settings > Privacy & Security > Screen & System Audio Recording"
ACCESS_SETTINGS = "System Settings > Privacy & Security > Accessibility"
PERMISSION_MARKERS = ("tcc", "declined", "not authorized", "permission")


@dataclass
class AgentConfig:
    repo: Path
    api: str = DEFAULT_API
    home: Path = field(default_factory=Path.home)

    @classmethod
    def from_sources(cls, sources: dict[str, Any], home: Path | None = None) -> AgentConfig:
        home = home or Path.home()
        repo = Path(os.path.expanduser(sources.get("retrace_repo", DEFAULT_REPO)))
        return cls(repo=repo, api=sources.get("retrace_api", DEFAULT_API).rstrip("/"), home=home)

    @property
    def plist_path(self) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    @property
    def retrace_home(self) -> Path:
        return self.home / ".retrace"

    @property
    def log_path(self) -> Path:
        return self.retrace_home / "serve.log"

    @property
    def status_path(self) -> Path:
        return self.retrace_home / "status.json"

    @property
    def venv_python(self) -> Path:
        return self.repo / ".venv" / "bin" / "python"

    def interpreter(self) -> Path:
        """The real binary behind the venv's python: what macOS attributes permissions to."""
        return self.venv_python.resolve()


def build_plist(cfg: AgentConfig) -> dict[str, Any]:
    path = ":".join([str(cfg.repo / ".venv" / "bin"), "/opt/homebrew/bin", str(cfg.home / ".local" / "bin"),
                     "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    return {
        "Label": LABEL,
        "ProgramArguments": [str(cfg.venv_python), "-m", "retrace.cli", "serve"],
        "WorkingDirectory": str(cfg.repo),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "ProcessType": "Interactive",
        "StandardOutPath": str(cfg.log_path),
        "StandardErrorPath": str(cfg.log_path),
        "EnvironmentVariables": {"PATH": path, "HOME": str(cfg.home), "PYTHONUNBUFFERED": "1"},
    }


# ---- health -------------------------------------------------------------------------------

@dataclass
class Health:
    state: str  # ok | quiet | paused | stale | failing | down
    capture_age_s: float | None = None
    heartbeat_age_s: float | None = None
    last_error: str | None = None

    @property
    def warn(self) -> bool:
        return self.state in ("down", "failing", "stale")

    @property
    def permission_problem(self) -> bool:
        return self.state == "failing" and any(m in (self.last_error or "").lower() for m in PERMISSION_MARKERS)

    def line(self) -> str:
        age = _fmt_age(self.capture_age_s)
        if self.state == "down":
            return "retrace: WARNING server not answering (capture is not running)"
        if self.state == "failing":
            return f"retrace: WARNING captures failing; last good capture {age} ago; last error: {self.last_error}"
        if self.state == "stale":
            return (f"retrace: WARNING capture loop stopped (no status update for {_fmt_age(self.heartbeat_age_s)}); "
                    f"last capture {age} ago")
        if self.state == "paused":
            return f"retrace: paused (capture off or snoozed); last capture {age} ago"
        if self.state == "quiet":
            return f"retrace: running; nothing new saved for {age} (duplicate or gated frames)"
        return f"retrace: capturing (last capture {age} ago)"


def _fmt_age(s: float | None) -> str:
    if s is None:
        return "never"
    s = max(0, int(s))
    if s < 120:
        return f"{s} s"
    if s < 7200:
        return f"{s // 60} min"
    return f"{s / 3600:.1f} h"


def _ts(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def assess(status: dict | None, health: dict | None, now: datetime) -> Health:
    """Classify Retrace's capture health from its status ledger and /api/health response."""
    if health is None:
        return Health("down", last_error=(status or {}).get("last_error"))
    status = status or {}
    cap = _ts(status.get("last_capture_at") or health.get("last_capture_at"))
    err = _ts(status.get("last_error_at"))
    updated = _ts(status.get("updated_at"))
    cap_age = (now - cap).total_seconds() if cap else None
    hb_age = health.get("ledger_age_s")
    if hb_age is None and updated:
        hb_age = (now - updated).total_seconds()
    last_error = status.get("last_error") or health.get("last_error")
    h = Health("ok", cap_age, hb_age, last_error)
    if err and (cap is None or err > cap):
        h.state = "failing"
    elif hb_age is not None and hb_age > STALE_HEARTBEAT_S:
        h.state = "stale"
    elif status.get("enabled") is False or health.get("enabled") is False or _snoozed(status.get("snooze_until"), now):
        h.state = "paused"
    elif cap_age is None or cap_age > QUIET_CAPTURE_S:
        h.state = "quiet"
    if h.state in ("ok", "quiet", "paused"):
        h.last_error = None  # an error older than the last good capture is history, not a problem
    return h


def _snoozed(v: Any, now: datetime) -> bool:
    if not v:
        return False
    t = _ts(v) if isinstance(v, str) else None
    return t is None or t > now  # non-timestamp values mean "indefinitely"


# ---- system access (injectable for tests) ---------------------------------------------------

class System:
    """Thin wrapper over launchctl, HTTP, files and processes."""

    def __init__(self, cfg: AgentConfig):
        self.cfg = cfg
        self.uid = os.getuid()

    @property
    def domain(self) -> str:
        return f"gui/{self.uid}"

    def launchctl(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["launchctl", *args], capture_output=True, text=True)

    def agent_pid(self) -> int | None:
        """PID of the agent's running job, or None if not loaded/running. Raises nothing."""
        r = self.launchctl("print", f"{self.domain}/{LABEL}")
        if r.returncode != 0:
            return None
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("pid = "):
                return int(line.split("=", 1)[1])
        return 0  # loaded but not currently running

    def agent_loaded(self) -> bool:
        return self.agent_pid() is not None

    def health(self) -> dict | None:
        try:
            with urllib.request.urlopen(self.cfg.api + "/api/health", timeout=2) as r:
                return json.loads(r.read())
        except Exception:
            return None

    def tick(self) -> None:
        try:
            req = urllib.request.Request(self.cfg.api + "/capture/tick?force=true", method="POST")
            urllib.request.urlopen(req, timeout=30).read()
        except Exception:
            pass

    def status(self) -> dict | None:
        try:
            return json.loads(self.cfg.status_path.read_text())
        except Exception:
            return None

    def serve_pids(self) -> list[int]:
        r = subprocess.run(["pgrep", "-f", "retrace.cli serve"], capture_output=True, text=True)
        return [int(p) for p in r.stdout.split() if p.strip().isdigit() and int(p) != os.getpid()]

    def terminate(self, pids: list[int], wait_s: float = 10) -> list[int]:
        for p in pids:
            try:
                os.kill(p, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + wait_s
        alive = list(pids)
        while alive and time.monotonic() < deadline:
            time.sleep(0.25)
            alive = [p for p in alive if _alive(p)]
        for p in alive:
            try:
                os.kill(p, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return pids

    def now(self) -> datetime:
        return datetime.now(tz=UTC)

    def sleep(self, s: float) -> None:
        time.sleep(s)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


# ---- commands -------------------------------------------------------------------------------

class AgentError(RuntimeError):
    pass


def permission_help(cfg: AgentConfig) -> str:
    return (f"Retrace runs under launchd, so it needs its own Screen Recording permission.\n"
            f"  1. Open {SCREEN_SETTINGS}.\n"
            f"  2. Click +, press Cmd+Shift+G, paste this path and add it:\n"
            f"       {cfg.interpreter()}\n"
            f"  3. Do the same under {ACCESS_SETTINGS} (window titles and browser URLs).\n"
            f"  4. Restart the agent: launchctl kickstart -k gui/{os.getuid()}/{LABEL}\n"
            f"     then check with: behaviornudge retrace agent status")


def install(cfg: AgentConfig, sys_: System, *, replace: bool = False, verify_timeout_s: float = VERIFY_TIMEOUT_S,
            out: Callable[[str], None] = print) -> Health:
    if not cfg.venv_python.exists():
        raise AgentError(f"Retrace's Python not found at {cfg.venv_python}. Set sources.retrace_repo "
                         f"or run `uv sync` in the Retrace checkout.")
    already = sys_.agent_loaded()
    if not already:
        manual = sys_.serve_pids()
        if manual or sys_.health() is not None:
            if not replace:
                raise AgentError("A Retrace server is already running outside the agent "
                                 f"(pids {', '.join(map(str, manual)) or 'unknown'}). Nothing was changed. "
                                 "Re-run with --replace to stop it and start Retrace under launchd.")
            out(f"Stopping the running Retrace server (pids {', '.join(map(str, manual))}) …")
            sys_.terminate(manual)
    cfg.plist_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.retrace_home.mkdir(parents=True, exist_ok=True)
    cfg.plist_path.write_bytes(plistlib.dumps(build_plist(cfg)))
    if already:
        sys_.launchctl("bootout", f"{sys_.domain}/{LABEL}")
        _wait(lambda: not sys_.agent_loaded(), sys_, 15)
    r = sys_.launchctl("bootstrap", sys_.domain, str(cfg.plist_path))
    if r.returncode != 0 and not sys_.agent_loaded():
        raise AgentError(f"launchctl bootstrap failed: {(r.stderr or r.stdout).strip()}")
    out(f"Agent {LABEL} loaded; Retrace's log is {cfg.log_path}.")
    return verify(cfg, sys_, timeout_s=verify_timeout_s, out=out)


def verify(cfg: AgentConfig, sys_: System, *, timeout_s: float = VERIFY_TIMEOUT_S,
           out: Callable[[str], None] = print) -> Health:
    """Wait for a capture newer than now (not just a healthy API)."""
    started = sys_.now()
    if not _wait(lambda: sys_.health() is not None, sys_, min(60, timeout_s)):
        h = Health("down")
        out(h.line())
        out(f"See {cfg.log_path} for why the server didn't start.")
        return h
    out("Server is up; asking for a capture …")
    deadline = _after(sys_, timeout_s)
    ticked_at: datetime | None = None
    while True:
        if ticked_at is None or (sys_.now() - ticked_at).total_seconds() > 20:
            sys_.tick()
            ticked_at = sys_.now()
        st = sys_.status() or {}
        cap, err = _ts(st.get("last_capture_at")), _ts(st.get("last_error_at"))
        if cap and cap >= started:
            h = assess(st, sys_.health(), sys_.now())
            out(f"Retrace is supervised by launchd and capturing (new capture at {cap.astimezone():%H:%M:%S}).")
            return h
        if err and err >= started:
            h = assess(st, sys_.health(), sys_.now())
            out(f"Capture failed under launchd: {st.get('last_error')}")
            if h.permission_problem:
                out(permission_help(cfg))
            return h
        if sys_.now() >= deadline:
            h = assess(st, sys_.health(), sys_.now())
            out(f"No new capture within {int(timeout_s)} s (the screen may be locked or idle). {h.line()}")
            return h
        sys_.sleep(2)


def uninstall(cfg: AgentConfig, sys_: System, out: Callable[[str], None] = print) -> None:
    if sys_.agent_loaded():
        sys_.launchctl("bootout", f"{sys_.domain}/{LABEL}")
        _wait(lambda: not sys_.agent_loaded(), sys_, 15)
    if cfg.plist_path.exists():
        cfg.plist_path.unlink()
    out(f"Agent {LABEL} removed. Retrace is no longer supervised; start it by hand with `uv run retrace menubar`.")


def status(cfg: AgentConfig, sys_: System) -> list[str]:
    pid = sys_.agent_pid()
    if pid is None:
        agent = "agent: not installed" if not cfg.plist_path.exists() else "agent: plist present but not loaded"
    else:
        agent = f"agent: loaded ({'running, pid ' + str(pid) if pid else 'not running; launchd will retry'})"
    h = assess(sys_.status(), sys_.health(), sys_.now())
    lines = [agent, h.line(), f"interpreter: {cfg.interpreter()}", f"log: {cfg.log_path}"]
    if h.permission_problem:
        lines.append(permission_help(cfg))
    return lines


def _after(sys_: System, seconds: float) -> datetime:
    from datetime import timedelta
    return sys_.now() + timedelta(seconds=seconds)


def _wait(cond: Callable[[], bool], sys_: System, timeout_s: float) -> bool:
    deadline = _after(sys_, timeout_s)
    while True:
        if cond():
            return True
        if sys_.now() >= deadline:
            return False
        sys_.sleep(0.5)
