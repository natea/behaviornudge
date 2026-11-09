import plistlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from behaviornudge import retrace_agent as ra
from behaviornudge.config import load_config

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)


def iso(dt):
    return dt.isoformat()


def ago(**kw):
    return iso(NOW - timedelta(**kw))


# ---- config / plist ----------------------------------------------------------------------

def test_example_config_has_retrace_keys():
    src = load_config(ROOT / "behaviornudge.example.toml").section("sources")
    cfg = ra.AgentConfig.from_sources(src, home=Path("/Users/x"))
    assert cfg.api == "http://127.0.0.1:8765"
    assert cfg.repo == Path("~/Documents/code/retrace").expanduser()


def test_defaults_without_keys():
    cfg = ra.AgentConfig.from_sources({}, home=Path("/Users/x"))
    assert cfg.api == ra.DEFAULT_API and cfg.repo.name == "retrace"


def test_plist_round_trips_with_expected_keys(tmp_path):
    cfg = ra.AgentConfig(repo=tmp_path / "retrace", home=tmp_path)
    d = plistlib.loads(plistlib.dumps(ra.build_plist(cfg)))
    assert d["Label"] == ra.LABEL
    assert d["ProgramArguments"] == [str(tmp_path / "retrace/.venv/bin/python"), "-m", "retrace.cli", "serve"]
    assert d["WorkingDirectory"] == str(tmp_path / "retrace")
    assert d["RunAtLoad"] is True and d["KeepAlive"] is True and d["ThrottleInterval"] == 30
    assert d["StandardOutPath"] == d["StandardErrorPath"] == str(tmp_path / ".retrace/serve.log")
    assert "/opt/homebrew/bin" in d["EnvironmentVariables"]["PATH"]
    assert cfg.plist_path == tmp_path / "Library/LaunchAgents/com.behaviornudge.retrace.plist"


# ---- assess --------------------------------------------------------------------------------

HEALTH = {"ok": True, "enabled": True, "ledger_age_s": 3}


def test_down_when_api_silent():
    h = ra.assess({"last_capture_at": ago(minutes=1)}, None, NOW)
    assert h.state == "down" and h.warn and "WARNING" in h.line()


def test_stale_capture_with_permission_error():
    st = {"last_capture_at": ago(minutes=70), "last_error_at": ago(minutes=1), "updated_at": ago(seconds=2),
          "last_error": "capture failed: capture: The user declined TCCs for application, window, display capture"}
    h = ra.assess(st, HEALTH, NOW)
    assert h.state == "failing" and h.warn and h.permission_problem
    assert "70 min" in h.line() and "declined TCCs" in h.line()


def test_healthy():
    st = {"last_capture_at": ago(minutes=1), "updated_at": ago(seconds=2), "enabled": True,
          "last_error": "old problem", "last_error_at": ago(hours=10)}
    h = ra.assess(st, HEALTH, NOW)
    assert h.state == "ok" and not h.warn and h.last_error is None
    assert h.line().startswith("retrace: capturing") and "WARNING" not in h.line()


def test_nothing_new_on_screen_is_quiet_not_a_warning():
    st = {"last_capture_at": ago(minutes=20), "updated_at": ago(seconds=2), "enabled": True}
    h = ra.assess(st, HEALTH, NOW)
    assert h.state == "quiet" and not h.warn and "nothing new" in h.line()


def test_capture_loop_stopped():
    st = {"last_capture_at": ago(minutes=8), "updated_at": ago(minutes=6)}
    h = ra.assess(st, {"ok": True, "enabled": True, "ledger_age_s": 360}, NOW)
    assert h.state == "stale" and h.warn and "loop stopped" in h.line()


def test_heartbeat_falls_back_to_status_updated_at():
    st = {"last_capture_at": ago(minutes=8), "updated_at": ago(minutes=6)}
    assert ra.assess(st, {"ok": True}, NOW).state == "stale"


def test_paused():
    st = {"last_capture_at": ago(hours=2), "updated_at": ago(seconds=2), "enabled": False}
    h = ra.assess(st, HEALTH, NOW)
    assert h.state == "paused" and not h.warn


# ---- install / uninstall / status ------------------------------------------------------------

class FakeSystem(ra.System):
    def __init__(self, cfg, *, manual_pids=(), api_up=False, capture_result="ok"):
        super().__init__(cfg)
        self.uid = 501
        self.t = NOW
        self.loaded = False
        self.manual = list(manual_pids)
        self.api_up = api_up
        self.capture_result = capture_result
        self.calls = []
        self.st = {"last_capture_at": ago(hours=1), "updated_at": iso(NOW), "enabled": True}

    def launchctl(self, *args):
        self.calls.append(args)
        if args[0] == "bootstrap":
            self.loaded = True
            self.api_up = True
        elif args[0] == "bootout":
            self.loaded = False
            self.api_up = False
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def agent_pid(self):
        return 4242 if self.loaded else None

    def health(self):
        return {"ok": True, "enabled": True, "ledger_age_s": 1} if self.api_up else None

    def tick(self):
        self.calls.append(("tick",))
        if self.capture_result == "ok":
            self.st["last_capture_at"] = iso(self.t)
        elif self.capture_result == "tcc":
            self.st["last_error_at"] = iso(self.t)
            self.st["last_error"] = "capture failed: capture: The user declined TCCs for display capture"
        self.st["updated_at"] = iso(self.t)

    def status(self):
        return dict(self.st)

    def serve_pids(self):
        return list(self.manual)

    def terminate(self, pids, wait_s=10):
        self.calls.append(("terminate", tuple(pids)))
        self.manual = []
        self.api_up = False
        return pids

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += timedelta(seconds=s)


@pytest.fixture
def cfg(tmp_path):
    repo = tmp_path / "retrace"
    (repo / ".venv/bin").mkdir(parents=True)
    (repo / ".venv/bin/python").write_text("")
    return ra.AgentConfig(repo=repo, home=tmp_path)


def test_install_refuses_when_manual_server_running(cfg):
    s = FakeSystem(cfg, manual_pids=[123], api_up=True)
    with pytest.raises(ra.AgentError, match="--replace"):
        ra.install(cfg, s, out=lambda _: None)
    assert not cfg.plist_path.exists() and s.calls == []


def test_install_replace_stops_server_and_verifies_capture(cfg):
    s = FakeSystem(cfg, manual_pids=[123], api_up=True)
    out = []
    h = ra.install(cfg, s, replace=True, out=out.append)
    assert ("terminate", (123,)) in s.calls
    assert cfg.plist_path.exists() and s.loaded
    assert h.state == "ok" and any("supervised by launchd and capturing" in line for line in out)


def test_install_reports_permission_problem_and_interpreter(cfg):
    s = FakeSystem(cfg, capture_result="tcc")
    out = []
    h = ra.install(cfg, s, out=out.append)
    text = "\n".join(out)
    assert h.state == "failing" and h.permission_problem
    assert str(cfg.interpreter()) in text and "Screen & System Audio Recording" in text
    assert cfg.plist_path.exists()  # left installed so it picks up the grant on restart


def test_install_times_out_without_capture(cfg):
    s = FakeSystem(cfg, capture_result="none")
    out = []
    h = ra.install(cfg, s, verify_timeout_s=30, out=out.append)
    assert h.state == "quiet" and any("No new capture within 30 s" in line for line in out)


def test_install_twice_leaves_one_agent(cfg):
    s = FakeSystem(cfg)
    ra.install(cfg, s, out=lambda _: None)
    ra.install(cfg, s, out=lambda _: None)
    kinds = [c[0] for c in s.calls if c[0] in ("bootstrap", "bootout")]
    assert kinds == ["bootstrap", "bootout", "bootstrap"] and s.loaded
    assert len(list(cfg.plist_path.parent.iterdir())) == 1


def test_install_needs_retrace_python(tmp_path):
    cfg = ra.AgentConfig(repo=tmp_path / "missing", home=tmp_path)
    with pytest.raises(ra.AgentError, match="uv sync"):
        ra.install(cfg, FakeSystem(cfg), out=lambda _: None)


def test_uninstall_unloads_and_removes_plist(cfg):
    s = FakeSystem(cfg)
    ra.install(cfg, s, out=lambda _: None)
    ra.uninstall(cfg, s, out=lambda _: None)
    assert not s.loaded and not cfg.plist_path.exists() and s.health() is None


def test_status_prints_state(cfg):
    s = FakeSystem(cfg)
    assert ra.status(cfg, s)[0] == "agent: not installed"
    ra.install(cfg, s, out=lambda _: None)
    lines = ra.status(cfg, s)
    assert lines[0].startswith("agent: loaded (running, pid 4242)")
    assert lines[1].startswith("retrace: capturing")


def test_nudge_status_includes_retrace_line(monkeypatch, capsys, tmp_path):
    from behaviornudge import cli

    seen = {}

    def fake_assess(status, health, now):
        seen["called"] = True
        return ra.Health("failing", 4200, 2, "declined TCCs")

    monkeypatch.setattr(ra, "assess", fake_assess)
    monkeypatch.setattr(ra.System, "status", lambda self: {})
    monkeypatch.setattr(ra.System, "health", lambda self: {})
    conf = tmp_path / "behaviornudge.toml"
    conf.write_text(f'data_dir = "{tmp_path / "data"}"\n[sources]\n')
    cli.main(["--config", str(conf), "nudge", "status"])
    out = capsys.readouterr().out
    assert seen["called"] and "retrace: WARNING captures failing; last good capture 70 min ago" in out
