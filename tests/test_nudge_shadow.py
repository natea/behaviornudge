import time
from datetime import UTC, datetime

from behaviornudge.nudge.shadow import ShadowScorer
from behaviornudge.nudge.store import NudgeStore
from behaviornudge.predictors import DriftScore

T = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)


class Slow:
    name = "slow"
    def predict(self, h, at, intent, key):
        time.sleep(5)
        return DriftScore(0.9, model_id="slow")


class Fixed:
    name = "fixed"
    def __init__(self, p, mid):
        self.p, self.mid = p, mid
    def predict(self, h, at, intent, key):
        return DriftScore(self.p, model_id=self.mid)


class Boom:
    name = "boom"
    def predict(self, h, at, intent, key):
        raise RuntimeError("503")


def wait_score(store, nid, timeout=6):
    end = time.time() + timeout
    while time.time() < end:
        n = store.get_nudge(nid)
        if n["shadow_score"] is not None or n["shadow_error"]:
            return n
        time.sleep(0.05)
    return store.get_nudge(nid)


def test_timeout_never_delays_and_falls_back(tmp_path):
    st = NudgeStore(tmp_path / "n.sqlite")
    nid = st.add_nudge(time=T, type="arrival", fired=True)
    sc = ShadowScorer(st, Slow(), Fixed(0.3, "baseline"), timeout_s=0.5)
    t0 = time.monotonic()
    sc.submit(nid, [], T, {}, "linkedin.com/messaging")
    assert time.monotonic() - t0 < 0.1  # returns immediately: the nudge isn't delayed
    n = wait_score(st, nid)
    assert n["shadow_score"] == 0.3 and n["shadow_model"] == "baseline" and "timeout" in n["shadow_error"]


def test_success_records_score(tmp_path):
    st = NudgeStore(tmp_path / "n.sqlite")
    nid = st.add_nudge(time=T, type="off_task", fired=False, reason="budget")
    ShadowScorer(st, Fixed(0.7, "behaviorgpt:cat"), None).submit(nid, [], T, {}, "x.com/home")
    n = wait_score(st, nid)
    assert n["shadow_score"] == 0.7 and n["shadow_error"] is None


def test_error_logged(tmp_path):
    st = NudgeStore(tmp_path / "n.sqlite")
    nid = st.add_nudge(time=T, type="arrival", fired=True)
    logs = []
    ShadowScorer(st, Boom(), Fixed(0.2, "baseline"), log=logs.append).submit(nid, [], T, {}, "x.com/home")
    n = wait_score(st, nid)
    assert "503" in n["shadow_error"] and n["shadow_score"] == 0.2 and logs
