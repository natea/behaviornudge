from datetime import UTC, datetime, timedelta

import pytest

from behaviornudge.nudge.store import NudgeStore

T = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)


def store(tmp_path):
    return NudgeStore(tmp_path / "nudges.sqlite")


def test_insert_and_update_outcome(tmp_path):
    s = store(tmp_path)
    nid = s.add_nudge(time=T, type="off_task", fired=True, activity_key="x.com/home", site="x.com",
                      intent_title="Client tests", intent_source="timer", intent_ref="t1", context={"stretch_s": 480})
    s.update_nudge(nid, action="too_soon", action_time=T.isoformat(), outcome_5="returned", shadow_score=0.4)
    n = s.get_nudge(nid)
    assert (n["type"], n["fired"], n["action"], n["outcome_5"], n["shadow_score"]) == ("off_task", 1, "too_soon", "returned", 0.4)
    with pytest.raises(ValueError):
        s.update_nudge(nid, type="hack")


def test_budget_and_cooldown_queries(tmp_path):
    s = store(tmp_path)
    s.add_nudge(time=T, type="arrival", fired=True, site="linkedin.com")
    s.add_nudge(time=T + timedelta(minutes=1), type="arrival", fired=False, reason="cooldown", site="linkedin.com")
    assert s.fired_count_since(T - timedelta(hours=1)) == 1
    assert s.last_fired("arrival", "linkedin.com") == T
    assert s.last_fired("arrival", "x.com") is None


def test_adjustments_and_suppressions(tmp_path):
    s = store(tmp_path)
    assert s.multiplier("off_task", "youtube.com", "calendar") == 1.0
    s.set_multiplier("off_task", "youtube.com", "calendar", 1.5)
    assert s.multiplier("off_task", "youtube.com", "calendar") == 1.5
    s.add_suppression("linkedin.com/feed", "task-1", "Prepare outreach list", "ck1")
    assert s.is_suppressed("linkedin.com/feed", "task-1") and not s.is_suppressed("linkedin.com/feed", "task-2")
    sid = s.suppressions()[0]["id"]
    assert s.delete_suppression(sid)["label_ck"] == "ck1"
    assert not s.is_suppressed("linkedin.com/feed", "task-1")


def test_state_round_trip(tmp_path):
    s = store(tmp_path)
    assert s.get_state("enabled", True) is True
    s.set_state("snooze_until", (T + timedelta(minutes=30)).isoformat())
    s.set_state("enabled", False)
    assert s.get_state("enabled") is False
    assert datetime.fromisoformat(s.get_state("snooze_until")) == T + timedelta(minutes=30)
