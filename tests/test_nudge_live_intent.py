from datetime import UTC, datetime, timedelta

from behaviornudge.intent import ActivitySpan, Span
from behaviornudge.nudge.live_intent import LiveIntent, timebox_spans, timer_span

NOW = datetime(2026, 11, 9, 19, 0, tzinfo=UTC)


def m(x):
    return NOW + timedelta(minutes=x)


def timer(title, started):
    return {"hasActiveTimer": True, "activeTimer": {"taskId": "t1", "taskTitle": title, "startTime": started.isoformat().replace("+00:00", "Z")}}


def li(calendar=(), timeboxes=()):
    return LiveIntent(lambda: list(calendar), lambda: list(timeboxes))


def test_timer_started_mid_session():
    x = li()
    assert x.resolve(NOW, []).source == "none"
    x.on_timer(timer("Client tests", m(-1)))
    i = x.resolve(NOW, [])
    assert (i.source, i.title) == ("timer", "Client tests")


def test_calendar_block_in_progress():
    x = li(calendar=[Span(m(-30), m(120), "Deep Work — Client Projects", None, "ev1")])
    i = x.resolve(NOW, [])
    assert (i.source, i.title, i.ref) == ("calendar", "Deep Work — Client Projects", "ev1")


def test_timebox_beats_calendar_and_timer_beats_both():
    x = li(calendar=[Span(m(-30), m(120), "Deep Work", None, "ev1")], timeboxes=[Span(m(-5), m(15), "Client tests", None, "tb1")])
    assert x.resolve(NOW, []).source == "planned"
    x.on_timer(timer("Invoice ClientCo", m(-2)))
    assert x.resolve(NOW, []).title == "Invoice ClientCo"


def test_forgotten_timer_stops_counting():
    x = li()
    x.on_timer(timer("Alex re LI post", m(-300)))  # started 5 h ago
    related_long_ago = [ActivitySpan(m(-300), m(-280), "linkedin.com/messaging", None, "reactive")]
    assert x.resolve(NOW, related_long_ago).source == "none"


def test_recent_creative_fallback():
    x = li()
    acts = [ActivitySpan(m(-20), m(-5), "zed:behaviornudge", "behaviornudge", "creative")]
    i = x.resolve(NOW, acts)
    assert (i.source, i.project) == ("recent-creative", "behaviornudge")


def test_refresh_failure_keeps_last_values():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("helper unavailable")
        return [Span(m(-30), m(60), "Deep Work", None, "ev1")]

    x = LiveIntent(flaky, None, clock=iter(range(0, 10_000, 400)).__next__)
    assert x.resolve(NOW, []).title == "Deep Work"
    assert x.resolve(NOW, []).title == "Deep Work"  # second refresh failed; previous calendar kept


def test_payload_parsers():
    assert timer_span({"hasActiveTimer": False, "activeTimer": None}, NOW) is None
    ev = {"events": [{"calendarName": "Sunsama Calendar", "title": "Client tests", "eventId": "e",
                      "start": {"dateTime": "2026-11-09T13:50:00-05:00"}, "end": {"dateTime": "2026-11-09T14:10:00-05:00"}},
                     {"calendarName": "natejaune@gmail.com", "title": "Meeting", "start": {"dateTime": "2026-11-09T14:00:00-05:00"},
                      "end": {"dateTime": "2026-11-09T15:00:00-05:00"}}]}
    spans = timebox_spans(ev)
    assert len(spans) == 1 and spans[0].start == datetime(2026, 11, 9, 18, 50, tzinfo=UTC)
