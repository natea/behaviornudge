"""engine-reliability: run the nudge engine under launchd."""

import json
import plistlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from behaviornudge import engine_agent as ea

NOW = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)


class FakeSystem:
    """launchctl + processes + heartbeat; bootstrap 'starts' an engine that writes a heartbeat."""

    def __init__(self, cfg, manual=(), starts=True):
        self.cfg, self.manual, self.starts = cfg, list(manual), starts
        self.loaded, self.pid, self.t = False, None, NOW
        self.calls, self.killed = [], []
        self.domain = "gui/501"

    def launchctl(self, *args):
        self.calls.append(args)
        if args[0] == "bootstrap":
            self.loaded, self.pid = True, 4242
            if self.starts:
                self.cfg.status_path.write_text(json.dumps({"pid": 4242, "updated": (self.t + timedelta(seconds=3)).isoformat()}))
        elif args[0] == "bootout":
            self.loaded, self.pid = False, None
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def agent_pid(self):
        return self.pid if self.loaded else None

    def agent_loaded(self):
        return self.loaded

    def engine_pids(self):
        return list(self.manual)

    def terminate(self, pids):
        self.killed += pids
        self.manual = []
        return pids

    def heartbeat(self):
        try:
            return json.loads(self.cfg.status_path.read_text())
        except Exception:
            return None

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += timedelta(seconds=s)


@pytest.fixture
def cfg(tmp_path):
    c = ea.EngineAgentConfig(repo=tmp_path / "repo", data_dir=tmp_path / "repo/data", home=tmp_path)
    c.program.parent.mkdir(parents=True)
    c.program.write_text("#!/bin/sh\n")
    return c


def test_plist(cfg, tmp_path):
    d = plistlib.loads(plistlib.dumps(ea.build_plist(cfg)))
    assert d["Label"] == "com.behaviornudge.engine"
    assert d["ProgramArguments"] == [str(cfg.program), "nudge", "run"]
    assert d["WorkingDirectory"] == str(cfg.repo)
    assert d["RunAtLoad"] is True and d["KeepAlive"] is True and d["ThrottleInterval"] == 30
    assert d["StandardOutPath"] == d["StandardErrorPath"] == str(cfg.data_dir / "engine.log")
    assert "/usr/bin" in d["EnvironmentVariables"]["PATH"]  # swiftc rebuilds the helper
    assert cfg.plist_path == tmp_path / "Library/LaunchAgents/com.behaviornudge.engine.plist"


def test_relative_data_dir_resolves_against_repo(tmp_path):
    c = ea.EngineAgentConfig.from_config(ea.Path("data"), repo=tmp_path)
    assert c.data_dir == tmp_path / "data"


def test_install_verifies_heartbeat(cfg):
    sys_ = FakeSystem(cfg)
    out = []
    assert ea.install(cfg, sys_, out=out.append)
    assert cfg.plist_path.exists() and sys_.loaded
    assert any("running under launchd" in line for line in out)


def test_refuses_when_engine_runs_in_a_terminal(cfg):
    sys_ = FakeSystem(cfg, manual=[46061])
    with pytest.raises(ea.AgentError, match="--replace"):
        ea.install(cfg, sys_)
    assert not cfg.plist_path.exists() and not sys_.calls


def test_replace_stops_the_terminal_engine(cfg):
    sys_ = FakeSystem(cfg, manual=[46060, 46061])
    assert ea.install(cfg, sys_, replace=True, out=lambda _: None)
    assert sys_.killed == [46060, 46061] and sys_.loaded


def test_reinstall_leaves_one_agent(cfg):
    sys_ = FakeSystem(cfg)
    ea.install(cfg, sys_, out=lambda _: None)
    ea.install(cfg, sys_, out=lambda _: None)
    assert [c[0] for c in sys_.calls] == ["bootstrap", "bootout", "bootstrap"]


def test_no_heartbeat_points_to_log(cfg):
    sys_ = FakeSystem(cfg, starts=False)
    out = []
    assert not ea.install(cfg, sys_, verify_timeout_s=5, out=out.append)
    assert any("engine.log" in line for line in out)


def test_stale_heartbeat_from_old_engine_is_not_success(cfg):
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.status_path.write_text(json.dumps({"pid": 46061, "updated": (NOW - timedelta(minutes=5)).isoformat()}))
    sys_ = FakeSystem(cfg, starts=False)
    assert not ea.install(cfg, sys_, verify_timeout_s=5, out=lambda _: None)


def test_uninstall_and_status(cfg):
    sys_ = FakeSystem(cfg)
    ea.install(cfg, sys_, out=lambda _: None)
    lines = ea.status(cfg, sys_)
    assert lines[0].startswith("agent: loaded (running, pid 4242") and lines[1].startswith("engine: running")
    ea.uninstall(cfg, sys_, out=lambda _: None)
    assert not cfg.plist_path.exists() and not sys_.loaded
    assert ea.agent_line(cfg, sys_).startswith("agent: not installed")
