"""Keep the live nudge engine running under launchd (openspec change engine-reliability, D3).

A per-user LaunchAgent runs `behaviornudge nudge run` at login and restarts it when it exits, so
nudges don't depend on a terminal window staying open. The engine runs as the same uv Python as
Retrace's agent, which already holds Screen Recording and Accessibility; macOS asks once whether it
may read Chrome's tabs (Automation).
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import retrace_agent as ra

LABEL = "com.behaviornudge.engine"
REPO = Path(__file__).resolve().parents[2]
VERIFY_TIMEOUT_S = 60
HEARTBEAT_FRESH_S = 30


@dataclass
class EngineAgentConfig:
    repo: Path
    data_dir: Path
    home: Path = field(default_factory=Path.home)

    @classmethod
    def from_config(cls, data_dir: Path, repo: Path | None = None, home: Path | None = None) -> EngineAgentConfig:
        repo = repo or REPO
        data_dir = data_dir if data_dir.is_absolute() else repo / data_dir
        return cls(repo=repo, data_dir=data_dir, home=home or Path.home())

    @property
    def plist_path(self) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    @property
    def program(self) -> Path:
        return self.repo / ".venv" / "bin" / "behaviornudge"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "engine.log"

    @property
    def status_path(self) -> Path:
        return self.data_dir / "nudge_status.json"


def build_plist(cfg: EngineAgentConfig) -> dict[str, Any]:
    path = ":".join([str(cfg.repo / ".venv" / "bin"), "/opt/homebrew/bin", str(cfg.home / ".local" / "bin"),
                     "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    return {
        "Label": LABEL,
        "ProgramArguments": [str(cfg.program), "nudge", "run"],
        "WorkingDirectory": str(cfg.repo),  # behaviornudge.toml and .env are read from here
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "ProcessType": "Interactive",
        "StandardOutPath": str(cfg.log_path),
        "StandardErrorPath": str(cfg.log_path),
        "EnvironmentVariables": {"PATH": path, "HOME": str(cfg.home), "PYTHONUNBUFFERED": "1"},
    }


class System(ra.System):
    """launchctl, processes and the engine's status heartbeat (injectable for tests)."""

    def __init__(self, cfg: EngineAgentConfig):
        super().__init__(cfg, label=LABEL)  # type: ignore[arg-type]

    def engine_pids(self) -> list[int]:
        r = subprocess.run(["pgrep", "-f", "behaviornudge nudge run"], capture_output=True, text=True)
        return [int(p) for p in r.stdout.split() if p.strip().isdigit() and int(p) != os.getpid()]

    def heartbeat(self) -> dict | None:
        try:
            return json.loads(self.cfg.status_path.read_text())
        except Exception:
            return None


class AgentError(RuntimeError):
    pass


def _beat_at(hb: dict | None) -> datetime | None:
    try:
        return datetime.fromisoformat(hb["updated"]) if hb else None
    except (KeyError, TypeError, ValueError):
        return None


def install(cfg: EngineAgentConfig, sys_: System, *, replace: bool = False, verify_timeout_s: float = VERIFY_TIMEOUT_S,
            out: Callable[[str], None] = print) -> bool:
    if not cfg.program.exists():
        raise AgentError(f"{cfg.program} not found; run `uv sync` in {cfg.repo} first.")
    already = sys_.agent_loaded()
    if not already:
        manual = sys_.engine_pids()
        if manual:
            if not replace:
                raise AgentError(f"The nudge engine is already running outside the agent (pids {', '.join(map(str, manual))}). "
                                 "Nothing was changed. Re-run with --replace to stop it and run the engine under launchd.")
            out(f"Stopping the running engine (pids {', '.join(map(str, manual))}) …")
            sys_.terminate(manual)
    cfg.plist_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.plist_path.write_bytes(plistlib.dumps(build_plist(cfg)))
    if already:
        sys_.launchctl("bootout", f"{sys_.domain}/{LABEL}")
        ra._wait(lambda: not sys_.agent_loaded(), sys_, 15)
    started = sys_.now()
    r = sys_.launchctl("bootstrap", sys_.domain, str(cfg.plist_path))
    if r.returncode != 0 and not sys_.agent_loaded():
        raise AgentError(f"launchctl bootstrap failed: {(r.stderr or r.stdout).strip()}")
    out(f"Agent {LABEL} loaded; the engine's log is {cfg.log_path}.")
    return verify(cfg, sys_, started, timeout_s=verify_timeout_s, out=out)


def verify(cfg: EngineAgentConfig, sys_: System, since: datetime, *, timeout_s: float = VERIFY_TIMEOUT_S,
           out: Callable[[str], None] = print) -> bool:
    """Wait for a status heartbeat written after `since` by the agent's process."""
    def fresh() -> bool:
        hb = sys_.heartbeat()
        at = _beat_at(hb)
        pid = sys_.agent_pid()
        return bool(at and at >= since and pid and hb.get("pid") == pid)
    if ra._wait(fresh, sys_, timeout_s):
        out(f"The nudge engine is running under launchd (pid {sys_.agent_pid()}).")
        out("If macOS asks whether python3.12 may control Google Chrome, click Allow: that's how tabs are read.")
        return True
    out(f"No heartbeat from the engine within {int(timeout_s)} s. See {cfg.log_path}.")
    return False


def uninstall(cfg: EngineAgentConfig, sys_: System, out: Callable[[str], None] = print) -> None:
    if sys_.agent_loaded():
        sys_.launchctl("bootout", f"{sys_.domain}/{LABEL}")
        ra._wait(lambda: not sys_.agent_loaded(), sys_, 15)
    if cfg.plist_path.exists():
        cfg.plist_path.unlink()
    out(f"Agent {LABEL} removed. Start the engine by hand with `uv run behaviornudge nudge run`.")


def agent_line(cfg: EngineAgentConfig, sys_: System) -> str:
    pid = sys_.agent_pid()
    if pid is None:
        if cfg.plist_path.exists():
            return "agent: plist present but not loaded (run `behaviornudge nudge agent install`)"
        return "agent: not installed (run `behaviornudge nudge agent install` to start the engine at login)"
    return f"agent: loaded ({'running, pid ' + str(pid) if pid else 'not running; launchd will retry'})"


def status(cfg: EngineAgentConfig, sys_: System) -> list[str]:
    hb = sys_.heartbeat()
    at = _beat_at(hb)
    age = (sys_.now() - at).total_seconds() if at else None
    engine = (f"engine: running (pid {hb.get('pid')}, heartbeat {int(age)} s ago)"
              if age is not None and age < HEARTBEAT_FRESH_S else "engine: not running")
    return [agent_line(cfg, sys_), engine, f"log: {cfg.log_path}"]
