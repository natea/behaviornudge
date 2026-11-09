from datetime import UTC, datetime, timedelta

import pytest

from behaviornudge.intent import Intent
from behaviornudge.labels import LabelStore, cache_key
from behaviornudge.nudge.feedback import FeedbackHandler, outcome, record_outcomes, stats
from behaviornudge.nudge.rules import RuleEngine
from behaviornudge.nudge.settings import load_settings
from behaviornudge.nudge.store import NudgeStore
from behaviornudge.nudge.tracker import LiveTracker

T = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)
CHROME = "com.google.Chrome"


def s(sec):
    return T + timedelta(seconds=sec)


def setup(tmp_path, **settings):
    store = NudgeStore(tmp_path / "n.sqlite")
    labels = LabelStore(tmp_path / "labels.sqlite")
    fb = FeedbackHandler(store, load_settings(settings), labels, "defs1")
    return store, labels, fb


def test_too_soon_off_task_8_to_12_minutes(tmp_path):
    store, _, fb = setup(tmp_path)
    nid = store.add_nudge(time=T, type="off_task", fired=True, site="youtube.com", activity_key="youtube.com/watch",
                          intent_source="calendar", intent_ref="ev1", intent_title="Deep Work")
    assert fb.apply(nid, "too_soon", s(1))["multiplier"] == 1.5
    eng = RuleEngine(load_settings(None), store, ["youtube.com"], "America/New_York")
    assert eng.delay("off_task", "youtube.com", Intent("calendar", ref="ev1")) == 12 * 60
    for _ in range(5):
        fb.apply(nid, "too_soon", s(2))
    assert store.multiplier("off_task", "youtube.com", "calendar") == 4.0  # capped


def test_back_to_task_moves_toward_default(tmp_path):
    store, _, fb = setup(tmp_path)
    store.set_multiplier("arrival", "x.com", "timer", 2.0)
    nid = store.add_nudge(time=T, type="arrival", fired=True, site="x.com", intent_source="timer")
    assert fb.apply(nid, "back_to_task", s(1))["multiplier"] == pytest.approx(1.9)


def test_legit_suppresses_and_writes_user_label(tmp_path):
    store, labels, fb = setup(tmp_path)
    nid = store.add_nudge(time=T, type="off_task", fired=True, site="linkedin.com", activity_key="linkedin.com/feed",
                          title="Feed | LinkedIn", intent_source="timer", intent_ref="t9", intent_title="Prepare outreach list")
    fb.apply(nid, "legit", s(1))
    assert store.is_suppressed("linkedin.com/feed", "t9")
    ck = cache_key({"key": "linkedin.com/feed", "title": "Feed | LinkedIn"}, Intent("timer", "Prepare outreach list", ref="t9"), "defs1")
    lab = labels.get(ck)
    assert lab.alignment == "on-task" and lab.sources["alignment"] == "user"
    # undo removes both the suppression and the user label
    sid = fb.list()["suppressions"][0]["id"]
    fb.reset_suppression(sid)
    assert not store.is_suppressed("linkedin.com/feed", "t9") and labels.get(ck) is None


def test_snooze_sets_expiry_and_expires(tmp_path):
    store, _, fb = setup(tmp_path)
    nid = store.add_nudge(time=T, type="arrival", fired=True)
    fb.apply(nid, "snooze", s(0))
    eng = RuleEngine(load_settings(None), store, [], "America/New_York")
    assert eng.gate("arrival", None, None, Intent("timer", ref="t"), s(10 * 60)) == "snoozed"
    assert eng.gate("arrival", None, None, Intent("timer", ref="t"), s(31 * 60)) is None


def test_unknown_action_rejected(tmp_path):
    store, _, fb = setup(tmp_path)
    nid = store.add_nudge(time=T, type="arrival", fired=True)
    with pytest.raises(ValueError):
        fb.apply(nid, "maybe", s(1))


def _timeline():
    tr = LiveTracker({})
    tr.on_front_app(CHROME, "Google Chrome", s(0))
    tr.on_tab("https://x.com/home", "Home", s(0))
    return tr


def test_outcomes_returned_vs_kept_consuming(tmp_path):
    intent = Intent("calendar", "Deep Work", ref="ev1")
    tr = _timeline()
    tr.tick(s(120))
    tr.on_front_app("dev.zed.Zed", "Zed", s(120))
    tr.tick(s(15 * 60))
    assert outcome(tr.sessions, s(60), s(60 + 5 * 60), intent) == "returned"
    tr2 = _timeline()
    tr2.tick(s(20 * 60))
    assert outcome(tr2.sessions, s(60), s(60 + 15 * 60), intent) == "kept_consuming"


def test_record_outcomes_fills_after_windows(tmp_path):
    store, _, _ = setup(tmp_path)
    nid = store.add_nudge(time=s(60), type="off_task", fired=True, intent_source="calendar", intent_ref="ev1", intent_title="Deep Work")
    tr = _timeline()
    tr.tick(s(20 * 60))
    record_outcomes(store, tr.sessions, s(7 * 60))
    n = store.get_nudge(nid)
    assert n["outcome_5"] == "kept_consuming" and n["outcome_15"] is None
    record_outcomes(store, tr.sessions, s(17 * 60))
    assert store.get_nudge(nid)["outcome_15"] == "kept_consuming"


def test_stats_hand_computed(tmp_path):
    store, _, _ = setup(tmp_path)
    rows = [("off_task", "back_to_task", "returned", "returned", 0.2),
            ("off_task", None, "kept_consuming", "kept_consuming", 0.8),
            ("arrival", "too_soon", "other", "kept_consuming", 0.6),
            ("arrival", "legit", "returned", "other", 0.1)]
    for i, (t, action, o5, o15, sc) in enumerate(rows):
        nid = store.add_nudge(time=s(i), type=t, fired=True)
        store.update_nudge(nid, action=action, outcome_5=o5, outcome_15=o15, shadow_score=sc)
    store.add_nudge(time=s(9), type="off_task", fired=False, reason="budget")
    st = stats(store)
    assert st["by_type"]["off_task"] == {"fired": 2, "suppressed": 1, "actions": {"back_to_task": 1, "ignored": 1},
                                         "return_within_5_min": 0.5}
    assert st["overall"]["fired"] == 4 and st["overall"]["return_within_5_min"] == 0.5
    # kept_consuming at 15 min: scores 0.8 and 0.6 (positives) vs 0.2 and 0.1 -> perfect ranking
    assert st["shadow_roc_auc"] == 1.0 and st["shadow_scored"] == 4
