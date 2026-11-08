import json

import pytest

from behaviornudge.config import load_config
from behaviornudge.jev import JevLabeler, Routing, activity_cache_key, route, to_label

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def answers(mode="consuming", mode_p=0.95, purpose="browse", project="none", fits=0.9):
    other = {"creative": 0.0, "reactive": 0.0, "consuming": 0.0}
    other[mode] = mode_p
    rest = (1 - mode_p) / 2
    for k in other:
        if k != mode:
            other[k] = rest
    return {"mode": {"choice": mode, "probabilities": other, "confidence": 0.5},
            "purpose": {"choice": purpose, "probabilities": {purpose: 0.8}},
            "project": {"choice": project, "probabilities": {project: 0.7}},
            "fits": {"noul": fits}}


class FakeAsk:
    def __init__(self, ans):
        self.ans, self.calls = ans, []

    def __call__(self, state, qs):
        self.calls.append((state, qs))
        return {"model": "jev-1.14.0", "answers": self.ans}


def labeler(tmp_path, ans):
    cfg = load_config(ROOT / "behaviornudge.example.toml")
    cfg.raw["data_dir"] = str(tmp_path)
    fake = FakeAsk(ans)
    return JevLabeler(cfg, ["Networking", "Personal"], ask=fake), fake


def test_request_shape_and_privacy(tmp_path):
    lab, fake = labeler(tmp_path, answers())
    s = {"key": "youtube.com/watch", "app": "chrome", "path_class": "watch",
         "title": "How transformers work - https://youtube.com/watch?v=abc", "caption": "a talk", "text": "SCREEN TEXT"}
    out, review = lab.label_one(s, "zed:proj")
    state, qs = fake.calls[0]
    assert set(qs) == {"mode", "purpose", "project", "fits"}
    assert set(state) <= {"app", "activity_key", "path_class", "title", "caption", "prev_activity_key"}
    assert "http" not in state["title"] and "SCREEN TEXT" not in json.dumps(state)
    assert out.mode == "consuming" and out.purpose == "browse" and out.sources["mode"] == "model"
    assert out.model == "jev-1.14.0" and out.prompt_fp
    audit = (tmp_path / "audit.jsonl").read_text()
    assert "typesafe:/v1/systemone" in audit and "SCREEN TEXT" not in audit


def test_purpose_own_work_maps_to_na():
    lab, _, _ = to_label(answers(mode="creative", purpose="own_work"), "m", "fp")
    assert lab.purpose == "n/a"


@pytest.mark.parametrize("mode_p,fits,expected", [
    (0.95, 0.9, None), (0.90, 0.9, None),          # accept
    (0.89, 0.9, "sample"), (0.50, 0.9, "sample"),  # accept + review sample
    (0.49, 0.9, "queue"),                          # ask the user
    (0.99, 0.29, "queue"),                         # nothing fits
])
def test_routing_boundaries(mode_p, fits, expected):
    assert route(mode_p, fits, Routing(0.9, 0.5, 0.3)) == expected


def test_routing_uses_chosen_probability_not_confidence():
    lab, mode_p, fits = to_label(answers(mode_p=0.95), "m", "fp")
    assert mode_p == 0.95  # 'confidence' (0.5 in the fixture) is ignored


def test_cache_key_depends_on_activity_and_fingerprint():
    a = activity_cache_key("youtube.com/watch", "Talk", "fp1")
    assert a == activity_cache_key("youtube.com/watch", "Talk", "fp1")
    assert a != activity_cache_key("youtube.com/watch", "Talk", "fp2")
    assert a != activity_cache_key("youtube.com/watch", "Other", "fp1")


def test_errors_recorded_not_raised(tmp_path):
    lab, _ = labeler(tmp_path, answers())
    def boom(state, qs):
        raise RuntimeError("429 rate limited")
    lab._ask = boom
    res = lab.label_many([("ck1", {"key": "x.com/status", "app": "chrome"}, None)], workers=1)
    assert res[0][1] is None and "429" in res[0][3]
