from datetime import UTC, datetime, timedelta

from behaviornudge.nudge.calls import CallTracker

T = datetime(2026, 11, 9, 18, 0, tzinfo=UTC)
ZOOM = [{"bundle_id": "us.zoom.xos", "name": "zoom.us"}]


def tracker(meeting=None):
    log = []
    return CallTracker(45, lambda t: meeting, log.append), log


def test_call_named_from_calendar_and_logged():
    tr, log = tracker("New England Music project")
    tr.on_calls(ZOOM, T)
    assert tr.state.active and tr.state.name == "New England Music project" and tr.state.app == "Zoom"
    assert log[0]["type"] == "call_start" and log[0]["name"] == "New England Music project"


def test_call_without_calendar_named_after_app():
    tr, _ = tracker(None)
    tr.on_calls(ZOOM, T)
    assert tr.state.name == "your Zoom call"


def test_brief_mute_does_not_split_call():
    tr, log = tracker()
    tr.on_calls(ZOOM, T)
    tr.on_calls([], T + timedelta(seconds=10))            # muted
    tr.tick(T + timedelta(seconds=30))                     # 20 s into the mute: still in the call
    assert tr.state.active
    tr.on_calls(ZOOM, T + timedelta(seconds=40))           # unmuted
    assert [e["type"] for e in log] == ["call_start"]


def test_call_ends_after_grace():
    tr, log = tracker()
    tr.on_calls(ZOOM, T)
    tr.on_calls([], T + timedelta(minutes=30))
    tr.tick(T + timedelta(minutes=30, seconds=44))
    assert tr.state.active
    tr.tick(T + timedelta(minutes=30, seconds=46))
    assert not tr.state.active and log[-1]["type"] == "call_end" and log[-1]["started"] == T.isoformat()


def test_call_name_follows_current_calendar_event():
    meeting = {"name": "Family dinner"}
    log = []
    tr = CallTracker(45, lambda t: meeting["name"], log.append)
    tr.on_calls(ZOOM, T)
    assert tr.state.name == "Family dinner"
    meeting["name"] = "Prep for Sundai demo"
    tr.tick(T + timedelta(minutes=5))
    assert tr.state.name == "Prep for Sundai demo"
