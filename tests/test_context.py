import json
from datetime import UTC, datetime, timedelta

from behaviornudge.context import ContextLogger, FocusIndex, TimerPoller, agent_working_spans, read_log
from behaviornudge.projects import NameMatcher, make_resolver
from behaviornudge.sources import Record


def snap(focused="w0:p1", cwd="/Users/x/Developer/behaviornudge"):
    return {"focused_pane_id": focused, "panes": [
        {"pane_id": "w0:p1", "foreground_cwd": cwd, "agent": "claude", "agent_status": "working"},
        {"pane_id": "w3:p1", "foreground_cwd": "/Users/x/Documents/code/parental-controls", "agent": "claude", "agent_status": "idle"}]}


class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 8, 19, 0, tzinfo=UTC)

    def __call__(self):
        self.t += timedelta(seconds=5)
        return self.t.isoformat()


def logger(tmp_path, snapshot):
    return ContextLogger(tmp_path / "ctx.jsonl", lambda cwd: cwd.rstrip("/").split("/")[-1], snapshot=snapshot, clock=Clock())


def test_subscribes_globally_and_per_pane(tmp_path):
    lg = logger(tmp_path, lambda: snap())
    subs = lg.subscriptions(snap())
    assert {"type": "pane.focused"} in subs
    assert {"type": "pane.agent_status_changed", "pane_id": "w3:p1"} in subs


def test_focus_event_logs_focused_pane_project(tmp_path):
    state = {"s": snap()}
    lg = logger(tmp_path, lambda: state["s"])
    entries, _ = lg.handle(json.dumps({"event": "pane_focused", "data": {"pane_id": "w0:p1"}}))
    assert entries[0]["project"] == "behaviornudge" and entries[0]["type"] == "focus"
    # same pane and folder again: no duplicate entry
    assert lg.handle(json.dumps({"event": "pane_updated", "data": {}}))[0] == []
    state["s"] = snap(focused="w3:p1")
    entries, _ = lg.handle(json.dumps({"event": "pane_focused", "data": {}}))
    assert entries[0]["project"] == "parental-controls"


def test_agent_status_and_working_spans(tmp_path):
    lg = logger(tmp_path, lambda: snap())
    lg.handle(json.dumps({"event": "pane.agent_status_changed", "data": {"pane_id": "w0:p1", "workspace_id": "w0", "agent_status": "working", "agent": "claude"}}))
    lg.handle(json.dumps({"event": "pane.agent_status_changed", "data": {"pane_id": "w0:p1", "workspace_id": "w0", "agent_status": "done", "agent": "claude"}}))
    spans = agent_working_spans(tmp_path / "ctx.jsonl")
    assert len(spans) == 1 and spans[0][1] > spans[0][0]


def test_new_pane_gets_subscribed_and_ack_ignored(tmp_path):
    lg = logger(tmp_path, lambda: snap())
    assert lg.handle(json.dumps({"event": "pane_created", "data": {"pane_id": "w9:p2"}})) == ([], ["w9:p2"])
    assert lg.handle(json.dumps({"id": "sub0", "result": {"type": "subscription_started"}})) == ([], [])
    entries, _ = lg.handle(json.dumps({"id": "sub1", "error": {"code": "invalid_request"}}))
    assert entries[0]["type"] == "error"


def test_logger_reads_only(tmp_path):
    calls = []
    lg = logger(tmp_path, lambda: calls.append("snapshot") or snap())
    lg.record_focus()
    assert calls == ["snapshot"]  # the only herdr interaction is reading a snapshot


def test_log_overrides_misleading_window_title(tmp_path):
    lg = logger(tmp_path, lambda: snap())
    lg.record_focus()
    focus = FocusIndex(tmp_path / "ctx.jsonl")
    t = datetime(2026, 11, 8, 19, 1, tzinfo=UTC)
    rec = Record(t, t + timedelta(minutes=1), "capture", bundle_id="com.mitchellh.ghostty", title="backlit-2.local: parental-controls")

    class Cfg:  # minimal config for make_resolver
        data_dir = tmp_path
        def path(self, *_):
            return tmp_path / "absent.db"

    resolve = make_resolver(Cfg(), context_projects=focus.project_at, repo_map={"parental-controls": "parental-controls", "behaviornudge": "behaviornudge"})
    assert resolve(rec) == ("behaviornudge", "herdr")


def test_timer_poller_logs_changes_only(tmp_path):
    lg = logger(tmp_path, lambda: snap())
    p = TimerPoller(lg, ["printf", '{"hasActiveTimer":true,"activeTimer":{"taskId":"t1","taskTitle":"Working on BehaviorNudge","startTime":"x"}}'])
    assert p.poll_once()["task_id"] == "t1"
    assert p.poll_once() is None
    assert [e["type"] for e in read_log(tmp_path / "ctx.jsonl")] == ["sunsama_timer"]


def test_timer_poller_accepts_callable(tmp_path):
    lg = logger(tmp_path, lambda: snap())
    p = TimerPoller(lg, lambda: {"hasActiveTimer": False, "activeTimer": None})
    p.last = "previous-task"
    assert p.poll_once()["task_id"] is None  # timer stopped is logged


def test_sunsama_token_storage_and_parse(tmp_path):
    import asyncio, os, stat
    from types import SimpleNamespace
    from mcp.shared.auth import OAuthToken
    from behaviornudge.sunsama_mcp import FileTokenStorage, LoginRequired, parse_timer, read_active_timer
    st = FileTokenStorage(tmp_path / "tok.json")
    assert not st.has_tokens()
    with __import__("pytest").raises(LoginRequired):
        asyncio.run(read_active_timer(st, interactive=False))  # never opens a browser in background mode
    asyncio.run(st.set_tokens(OAuthToken(access_token="a", token_type="Bearer", refresh_token="r")))
    assert st.has_tokens() and asyncio.run(st.get_tokens()).refresh_token == "r"
    assert stat.S_IMODE(os.stat(tmp_path / "tok.json").st_mode) == 0o600
    res = SimpleNamespace(contents=[SimpleNamespace(text='{"hasActiveTimer":true,"activeTimer":{"taskId":"t"}}')])
    assert parse_timer(res)["activeTimer"]["taskId"] == "t"
