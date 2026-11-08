from datetime import timedelta

from behaviornudge.episodes import Episode
from behaviornudge.evaluate import (History, Prediction, at_budget, decide, evaluate, pr_auc, roc_auc,
                                    run_predictor, trigger_tasks)
from behaviornudge.predictors import DriftScore, check_history

from .conftest import at

TZ = "America/New_York"


def S(i, key, s, e):
    return {"id": i, "key": key, "start_utc": s.isoformat(), "end_utc": e.isoformat(), "seconds": (e - s).total_seconds()}


def E(sid, t, outcome, key="linkedin.com/messaging", lead=2.0):
    return Episode(t, sid, "linkedin.com", key, "reply", "calendar", "x", outcome, 5.0 if outcome == "drift" else 0,
                   lead if outcome == "drift" else None, None, ["none"], 13, 2)


class Spy:
    """Records the latest session end it was shown; fails on purpose for one episode."""
    name = "spy"

    def __init__(self, fail_at=None):
        self.max_seen_end, self.fail_at = [], fail_at

    def fit(self, s, e):
        pass

    def predict(self, history, at_, intent, current_key):
        check_history(history, at_)
        if self.fail_at and at_ == self.fail_at:
            raise RuntimeError("503 unavailable")
        return DriftScore(0.9 if current_key.endswith("messaging") else 0.1)


def _data():
    sessions, episodes, i = [], [], 0
    for d in range(0, 35, 1):  # 5 weeks of daily episodes
        t = at(d * 24 * 60)
        i += 1; sessions.append(S(i, "zed:p", t - timedelta(minutes=30), t - timedelta(minutes=1)))
        i += 1; sessions.append(S(i, "linkedin.com/messaging" if d % 2 else "x.com/status", t, t + timedelta(minutes=2)))
        episodes.append(E(i, t, "drift" if d % 2 else "clean", sessions[-1]["key"]))
        i += 1; sessions.append(S(i, "linkedin.com/feed", t + timedelta(minutes=2), t + timedelta(minutes=9)))
    return sessions, episodes


def test_no_session_after_decision_time_reaches_predictor():
    sessions, episodes = _data()
    res = evaluate(sessions, episodes, {"spy": lambda: Spy()}, TZ)
    assert res["predictions"] and all(p.error is None for p in res["predictions"])  # check_history never tripped


def test_history_helper_cuts_at_decision_time():
    sessions, _ = _data()
    h = History(sessions)
    t = at(3 * 24 * 60)
    assert all(s["end_utc"] <= t.isoformat() for s in h.before(t))


def test_failures_recorded_and_excluded_run_continues():
    sessions, episodes = _data()
    fail_t = episodes[-1].entry_time
    res = evaluate(sessions, episodes, {"spy": lambda: Spy(fail_at=fail_t)}, TZ)
    preds = res["predictions"]
    failed = [p for p in preds if p.error]
    assert len(failed) == 1 and "503" in failed[0].error
    pooled = res["metrics"]["spy"]["pooled"]
    assert pooled["failures"] == 1 and pooled["n"] == len(preds) - 1


def test_metrics_on_hand_computed_toy():
    assert roc_auc([0.9, 0.8, 0.3, 0.1], [1, 1, 0, 0]) == 1.0
    assert roc_auc([0.1, 0.2, 0.8, 0.9], [1, 1, 0, 0]) == 0.0
    assert roc_auc([0.5, 0.5], [1, 0]) == 0.5
    assert pr_auc([0.9, 0.8, 0.3, 0.1], [1, 0, 1, 0]) == (1 / 1 + 2 / 3) / 2
    assert pr_auc([0.5], [1]) is None


def test_budget_caps_nudges():
    eps = {i: E(i, at(i), "drift" if i < 3 else "clean") for i in range(10)}
    preds = [Prediction("x", i, at(i).isoformat(), "w", eps[i].outcome, 1 - i / 10) for i in range(10)]
    b = at_budget(preds, eps, working_days=1, budget_per_day=4)
    assert b["nudges"] == 4 and b["precision"] == 0.75 and b["recall"] == 1.0 and b["false_alarms_per_day"] == 1.0
    assert b["median_lead_min"] == 2.0


def test_decide_prefers_baseline_unless_candidate_wins():
    m = {"baseline": {"weekly": {"w1": {"pr_auc": 0.5}, "w2": {"pr_auc": 0.5}}, "pooled": {"pr_auc": 0.5, "mean_latency_ms": 1}},
         "behaviorgpt": {"weekly": {"w1": {"pr_auc": 0.4}, "w2": {"pr_auc": 0.6}}, "pooled": {"pr_auc": 0.55, "mean_latency_ms": 900}}}
    assert decide(m)["recommend"] == "baseline"  # 1 of 2 weeks is not "most"
    m["behaviorgpt"]["weekly"]["w1"]["pr_auc"] = 0.7
    assert decide(m)["recommend"] == "behaviorgpt"


def test_pre_entry_trigger_labels_following_drift():
    sessions, episodes = _data()
    target = episodes[-2]  # a drift episode
    assert target.outcome == "drift"
    meeting_end = target.entry_time - timedelta(seconds=30)  # Zed has just ended; the drift entry follows
    weeks = {"2026-W45", "2026-W46", "2026-W47", "2026-W48", "2026-W49", "2026-W50"}
    tasks = trigger_tasks([meeting_end], episodes, sessions, weeks, TZ)
    assert len(tasks) == 1
    _, _, T, cur, label, _ = tasks[0]
    assert T == meeting_end and label == "drift" and cur == "zed:p"
    out = run_predictor(Spy(), "spy", tasks, History(sessions), None, kind="pre-entry")
    assert out[0].kind == "pre-entry" and out[0].p_drift is not None
