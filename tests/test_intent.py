from datetime import UTC, datetime, timedelta

from behaviornudge.intent import (
    ActivitySpan,
    IntentResolver,
    Span,
    cap_timers,
    corrected_time_report,
    default_related,
    parse_local,
)

TZ = "America/New_York"


def L(h, m=0, day="2026-11-08"):
    """Local New York time -> UTC."""
    ap = "AM" if h < 12 else "PM"
    return parse_local(day, f"{(h - 1) % 12 + 1}:{m:02d} {ap}", TZ)


def creative(s, e, project="behaviornudge"):
    return ActivitySpan(s, e, f"zed:{project}", project, "creative")


def test_parse_local_new_york():
    assert parse_local("2026-11-08", "1:46 PM", TZ) == datetime(2026, 11, 8, 18, 46, tzinfo=UTC)
    assert parse_local("2026-11-08", "12:05 AM", TZ) == datetime(2026, 11, 8, 5, 5, tzinfo=UTC)


def test_timer_running():
    r = IntentResolver(timers=[Span(L(13, 46), L(15), "Working on BehaviorNudge at Sundai", None, "t1")])
    i = r.resolve(L(13, 50))
    assert (i.source, i.title) == ("timer", "Working on BehaviorNudge at Sundai")


def test_planned_slot_when_no_timer():
    r = IntentResolver(planned=[Span(L(14, 10), L(14, 30), "Jordan re product feedback", "Mentoring/Advising")])
    i = r.resolve(L(14, 15))
    assert (i.source, i.title) == ("planned", "Jordan re product feedback")


def test_calendar_event_no_sunsama():
    r = IntentResolver(calendar=[Span(L(11), L(11, 30), "Client sync")])
    i = r.resolve(L(11, 10))
    assert (i.source, i.title) == ("calendar", "Client sync")


def test_shortest_calendar_event_wins():
    block = Span(L(9, 30), L(12), "Deep Work — Client Projects")
    meeting = Span(L(9, 30), L(10, 15), "Engineering convo")
    r = IntentResolver(calendar=[block, meeting])
    assert r.resolve(L(9, 45)).title == "Engineering convo"
    assert r.resolve(L(11, 0)).title == "Deep Work — Client Projects"


def test_recent_creative_fallback():
    r = IntentResolver(activity=[creative(L(13, 0), L(13, 40))])
    i = r.resolve(L(13, 50))
    assert (i.source, i.project) == ("recent-creative", "behaviornudge")


def test_window_is_configurable():
    acts = [creative(L(13, 0), L(13, 15))]  # ended 45 min before 14:00
    assert IntentResolver(activity=acts, recent_window_min=30).resolve(L(14)).source == "none"
    i = IntentResolver(activity=acts, recent_window_min=60).resolve(L(14))
    assert (i.source, i.project) == ("recent-creative", "behaviornudge")


def test_sunsama_not_used_that_day():
    r = IntentResolver(calendar=[Span(L(11), L(11, 30), "Client sync")], activity=[creative(L(13), L(13, 30))])
    times = [L(11, 10), L(13, 40), L(20)]
    assert [r.resolve(t).source for t in times] == ["calendar", "recent-creative", "none"]
    by_day = r.coverage_by_day(times, TZ)
    assert by_day["2026-11-08"].get("timer", 0) == 0 and by_day["2026-11-08"].get("planned", 0) == 0


def test_coverage_by_window():
    r = IntentResolver(activity=[creative(L(13, 0), L(13, 15))])
    cov = r.coverage([L(14)])
    assert cov["30min"]["none"] == 1.0
    assert cov["60min"]["recent-creative"] == 1.0


def test_forgotten_timer_capped():
    timer = Span(L(15), L(8, 11, day="2026-11-09"), "Alex re LI post", "Networking", "t1")
    acts = [ActivitySpan(L(15), L(15, 40), "linkedin.com/messaging", None, "reactive")]
    capped = cap_timers([timer], acts, default_related({}), cap_min=45)[0]
    assert capped.capped_end == L(16, 25)
    assert round(capped.corrected_min) == 40
    assert round(capped.recorded_min) == 1031
    report = corrected_time_report([capped])
    assert report[0]["recorded_min"] == 1031.0 and report[0]["corrected_min"] == 40.0


def test_capped_timer_stops_counting_as_intent():
    timer = Span(L(15), L(8, 11, day="2026-11-09"), "Alex re LI post", "Networking", "t1")
    acts = [ActivitySpan(L(15), L(15, 40), "linkedin.com/messaging", None, "reactive")]
    c = cap_timers([timer], acts, default_related({}), cap_min=45)[0]
    r = IntentResolver(timers=[Span(c.span.start, c.capped_end, c.span.title, c.span.channel, c.span.ref)])
    assert r.resolve(L(16, 0)).source == "timer"
    assert r.resolve(L(17, 0)).source == "none"


def test_related_by_channel_mapping():
    rel = default_related({"NE Music": "newenglandmusic"})
    timer = Span(L(15), L(16), "Fix spec", "NE Music")
    assert rel(ActivitySpan(L(15), L(15, 10), "zed:newenglandmusic", "newenglandmusic", "creative"), timer)
    assert not rel(ActivitySpan(L(15), L(15, 10), "zed:clientco", "clientco", "creative"), timer)
