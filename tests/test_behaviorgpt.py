from datetime import timedelta

import pytest

from behaviornudge.predictors import LookAheadError
from behaviornudge.predictors.behaviorgpt import (
    CATALOG_COLUMNS, OTHER_WEB, BehaviorGPTPredictor, build_catalog_rows, catalog_keys,
    check_catalog_columns, drift_scores, history_events, write_catalog_parquet,
)

from .conftest import at


def S(i, key, s, e):
    return {"id": i, "key": key, "start_utc": s.isoformat(), "end_utc": e.isoformat(), "seconds": (e - s).total_seconds()}


SESSIONS = [S(i, k, at(i * 2), at(i * 2 + 1)) for i, k in enumerate(["zed:proj"] * 6 + ["linkedin.com/feed"] * 6 + ["rare.example.com"])]
MODES = {"zed:proj": "creative", "linkedin.com/feed": "consuming"}


def test_catalog_has_all_columns_and_buckets(tmp_path):
    rows = build_catalog_rows(SESSIONS, MODES, ["linkedin.com"], min_sessions=5)
    ids = {r["id"] for r in rows}
    assert {"zed:proj", "linkedin.com/feed", OTHER_WEB}.issubset(ids)
    assert "rare.example.com" not in ids  # rare sites are not uploaded individually
    feed = next(r for r in rows if r["id"] == "linkedin.com/feed")
    assert feed["event_type"] == "product" and feed["group"] == "product"
    assert feed["categories"] == "consuming > drift-prone"
    p = write_catalog_parquet(rows, tmp_path / "c.parquet")
    assert check_catalog_columns(p) == []
    assert len(CATALOG_COLUMNS) == 15


def test_rare_key_maps_to_bucket():
    assert catalog_keys(SESSIONS, 5)["rare.example.com"] == OTHER_WEB


def test_history_keeps_real_timestamps_and_orders_long_sessions():
    hist = [S(1, "zed:proj", at(0), at(15)), S(2, "linkedin.com/feed", at(16), at(17))]
    ev = history_events(hist, {"zed:proj": "zed:proj", "linkedin.com/feed": "linkedin.com/feed"}, order_min=10)
    assert ("view", at(0), "zed:proj", "b0") in ev
    assert ("order", at(15), "zed:proj", "b0") in ev  # 15-min session = commitment
    assert not any(k == "order" and c == "linkedin.com/feed" for k, _, c, _ in ev)


class FakeTransport:
    def __init__(self, items):
        self.items, self.bodies = items, []

    def __call__(self, body):
        self.bodies.append(body)
        return {"products": {"items": self.items, "offset": 0, "limit": 20}}


def _predictor(tmp_path, items):
    cat = {"catalog_id": "cat1", "mapping": {"zed:proj": "zed:proj", "linkedin.com/feed": "linkedin.com/feed",
                                             "linkedin.com/messaging": "linkedin.com/messaging"}, "modes": MODES}
    fake = FakeTransport(items)
    p = BehaviorGPTPredictor(None, transport=fake, catalog=cat)
    from behaviornudge.egress import AuditLog
    p.audit = AuditLog(tmp_path / "audit.jsonl")
    return p, fake


def test_request_has_real_timestamps_asof_and_no_event_storage(tmp_path):
    p, fake = _predictor(tmp_path, [{"id": "linkedin.com/feed", "score": 2.0, "data": {}}, {"id": "zed:proj", "score": 1.0, "data": {}}])
    hist = [S(1, "zed:proj", at(0), at(15))]
    out = p.predict(hist, at(20), {}, "linkedin.com/messaging")
    body = fake.bodies[0]
    assert body["register_event"] is False
    assert body["timezone"] == "America/New_York" and body["store_id"] == "cat1"
    assert body["timestamp"].startswith(at(20).isoformat()[:19])
    stamps = [e["timestamp"][:19] for e in body["history"]]
    assert stamps[0] == at(0).isoformat()[:19]  # real time, not "now"
    assert body["history"][-1]["event"]["type"] == "viewItem"  # the visit that just started
    assert out.p_drift > 0.5 and out.extras["best_consuming_rank"] == 1
    assert out.ranked_next[0][0] == "linkedin.com/feed"


def test_request_contains_only_keys_and_times(tmp_path):
    p, fake = _predictor(tmp_path, [])
    hist = [dict(S(1, "zed:proj", at(0), at(5)), title="SECRET TITLE", caption="secret caption", url="https://x.com/?q=1")]
    p.predict(hist, at(10), {}, "zed:proj")
    raw = str(fake.bodies[0])
    assert "SECRET" not in raw and "secret caption" not in raw and "https://" not in raw
    audit = (tmp_path / "audit.jsonl").read_text()
    assert "unbox:/vector/recommendations" in audit and "SECRET" not in audit


def test_rejects_future_history(tmp_path):
    p, _ = _predictor(tmp_path, [])
    with pytest.raises(LookAheadError):
        p.predict([S(1, "zed:proj", at(0), at(30))], at(10), {}, "zed:proj")


def test_transport_error_propagates_for_harness(tmp_path):
    p, _ = _predictor(tmp_path, [])
    def boom(body):
        raise RuntimeError("BehaviorGPT 503: unavailable")
    p._transport = boom
    with pytest.raises(RuntimeError):
        p.predict([], at(10), {}, "zed:proj")


def test_drift_scores_empty():
    assert drift_scores([], MODES) == (0.0, None)
