from datetime import timedelta

from behaviornudge.episodes import TriggerIndex, detect, summarize

from .conftest import at

PRONE = ["linkedin.com", "x.com", "instagram.com"]
_id = iter(range(1, 10_000))


def S(key, s, e, mode=None, purpose=None, alignment=None, intent=("planned", "Client tests")):
    dom = key.split("/")[0] if "." in key.split("/")[0] else None
    return {"id": next(_id), "seconds": (e - s).total_seconds(), "key": key, "domain": dom, "start_utc": s.isoformat(), "end_utc": e.isoformat(),
            "mode": mode, "purpose": purpose, "alignment": alignment, "intent_source": intent[0], "intent_title": intent[1]}


def test_reply_then_feed_is_drift():
    ss = [S("linkedin.com/messaging", at(0), at(2), "reactive", "reply", "off-task"),
          S("linkedin.com/feed", at(2), at(8), "consuming", "browse", "off-task")]
    eps = detect(ss, PRONE)
    assert len(eps) == 1
    e = eps[0]
    assert (e.outcome, e.site, e.purpose) == ("drift", "linkedin.com", "reply")
    assert e.drift_min == 6 and e.to_first_drift_min == 2


def test_reply_then_back_to_work_is_clean():
    ss = [S("linkedin.com/messaging", at(0), at(2), "reactive", "reply", "off-task"),
          S("zed:clientco", at(2), at(20), "creative", "n/a", "on-task")]
    e = detect(ss, PRONE)[0]
    assert e.outcome == "clean" and e.to_return_min == 2


def test_aimless_entry_is_not_an_episode():
    ss = [S("x.com/home", at(0), at(10), "consuming", "browse", "off-task")]
    assert detect(ss, PRONE) == []


def test_feed_is_the_task_does_not_count():
    ss = [S("linkedin.com/messaging", at(0), at(1), "reactive", "reply", "on-task", ("timer", "Alex re LI post")),
          S("linkedin.com/feed", at(1), at(10), "consuming", "browse", "on-task", ("timer", "Alex re LI post"))]
    e = detect(ss, PRONE)[0]
    assert e.outcome == "clean" and e.drift_min == 0


def test_user_added_site():
    ss = [S("youtube.com/watch", at(0), at(1), "consuming", "follow-link", "off-task"),
          S("youtube.com/home", at(1), at(9), "consuming", "browse", "off-task")]
    assert detect(ss, PRONE) == []
    assert detect(ss, PRONE + ["youtube.com"])[0].outcome == "drift"


def test_no_intent_consuming_counts():
    ss = [S("x.com/status", at(0), at(1), "consuming", "follow-link", "no-intent", ("none", None)),
          S("x.com/home", at(1), at(6), "consuming", "browse", "no-intent", ("none", None))]
    assert detect(ss, PRONE)[0].outcome == "drift"


def test_drift_window_limits_counting():
    ss = [S("linkedin.com/messaging", at(0), at(1), "reactive", "reply", "off-task"),
          S("linkedin.com/feed", at(14), at(30), "consuming", "browse", "off-task")]
    e = detect(ss, PRONE)[0]
    assert e.drift_min == 1 and e.outcome == "clean"  # only 1 min falls inside the 15-min window


def test_triggers_meeting_ended_and_agent_working():
    trig = TriggerIndex(calendar_ends=[at(-3)], agent_working=[(at(-10), at(5))])
    ss = [S("linkedin.com/messaging", at(0), at(2), "reactive", "reply", "off-task"),
          S("linkedin.com/feed", at(2), at(8), "consuming", "browse", "off-task")]
    e = detect(ss, PRONE, triggers=trig)[0]
    assert set(e.triggers) == {"meeting-ended", "agent-working"}
    assert detect(ss, PRONE, triggers=TriggerIndex())[0].triggers == ["none"]


def test_summary_by_site():
    ss = [S("linkedin.com/messaging", at(0), at(2), "reactive", "reply", "off-task"),
          S("linkedin.com/feed", at(2), at(8), "consuming", "browse", "off-task"),
          S("x.com/status", at(60), at(61), "consuming", "follow-link", "off-task"),
          S("zed:x", at(61), at(70), "creative", "n/a", "on-task")]
    sm = summarize(detect(ss, PRONE))
    assert sm["by_site"]["linkedin.com"]["drift_rate"] == 1.0
    assert sm["by_site"]["x.com"]["drift_rate"] == 0.0
    assert sm["episodes"] == 2


def test_brief_glance_at_editor_is_not_a_return():
    ss = [S("linkedin.com/messaging", at(0), at(1), "reactive", "reply", "off-task"),
          S("zed:clientco", at(1), at(1, 20), "creative", "n/a", "on-task"),  # 20-second glance
          S("linkedin.com/feed", at(1, 20), at(7), "consuming", "browse", "off-task")]
    assert detect(ss, PRONE, return_min_s=60)[0].outcome == "drift"
    assert detect(ss, PRONE, return_min_s=0)[0].outcome == "clean"
