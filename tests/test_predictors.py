from datetime import timedelta

import pytest

from behaviornudge.episodes import Episode
from behaviornudge.predictors import REGISTRY, DriftScore, LookAheadError, create, register
from behaviornudge.predictors.baseline import BaselinePredictor

from .conftest import at


def S(i, key, s, e):
    return {"id": i, "key": key, "start_utc": s.isoformat(), "end_utc": e.isoformat(), "seconds": (e - s).total_seconds()}


def E(sid, key, t, outcome):
    return Episode(t, sid, key.split("/")[0], key, "reply", None, None, outcome, 5.0 if outcome == "drift" else 0, None, None)


def test_dummy_predictor_registers_and_runs():
    @register("dummy")
    def _f(cfg=None):
        class D:
            name = "dummy"
            def fit(self, sessions, episodes): pass
            def predict(self, history, at_, intent, current_key):
                return DriftScore(0.5, [(current_key, 1.0)], "dummy", 0.0)
        return D()

    p = create("dummy")
    out = p.predict([], at(0), {}, "x.com/status")
    assert isinstance(out, DriftScore) and out.p_drift == 0.5
    REGISTRY.pop("dummy")


def test_builtins_registered():
    assert create("baseline").name == "baseline"


def _synthetic():
    """Afternoon LinkedIn replies usually drift; X status visits in the morning don't."""
    sessions, episodes, i, t = [], [], 0, at(0)
    for day in range(20):
        base = at(day * 24 * 60)
        # afternoon (18:00 UTC = 13:00 NY): messaging -> feed (drift)
        i += 1; sessions.append(S(i, "linkedin.com/messaging", base, base + timedelta(minutes=1)))
        episodes.append(E(i, "linkedin.com/messaging", base, "drift"))
        i += 1; sessions.append(S(i, "linkedin.com/feed", base + timedelta(minutes=1), base + timedelta(minutes=9)))
        # morning (13:00 UTC = 08:00 NY): x status -> back to zed (clean)
        m = base - timedelta(hours=5)
        i += 1; sessions.append(S(i, "x.com/status", m, m + timedelta(minutes=1)))
        episodes.append(E(i, "x.com/status", m, "clean"))
        i += 1; sessions.append(S(i, "zed:proj", m + timedelta(minutes=1), m + timedelta(minutes=30)))
    sessions.sort(key=lambda s: s["start_utc"])
    return sessions, episodes


def test_baseline_learns_known_pattern():
    sessions, episodes = _synthetic()
    b = BaselinePredictor()
    b.fit(sessions, episodes)
    later = at(30 * 24 * 60)
    hi = b.predict([], later, {}, "linkedin.com/messaging")
    lo = b.predict([], later - timedelta(hours=5), {}, "x.com/status")
    assert hi.p_drift > 0.8 and lo.p_drift < 0.2
    assert hi.ranked_next[0][0] == "linkedin.com/feed"


def test_baseline_rejects_future_sessions():
    b = BaselinePredictor()
    future = [S(1, "zed", at(10), at(20))]
    with pytest.raises(LookAheadError):
        b.predict(future, at(5), {}, "x.com/status")


def test_unknown_context_falls_back_to_prior():
    b = BaselinePredictor()
    b.fit([], [])
    assert 0.0 < b.predict([], at(0), {}, "x.com/status").p_drift < 1.0
