"""Local baseline predictor (design D8).

Drift probability: smoothed empirical drift rate from training episodes, conditioned on
(current key, previous key, hour bucket, weekday type) with hierarchical back-off.
Ranked next activities: smoothed transition counts from the current key in training sessions.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from datetime import datetime
from typing import Sequence
from zoneinfo import ZoneInfo

from . import DriftScore, check_history, register


def hour_bucket(h: int) -> str:
    return "night" if h < 6 else "morning" if h < 12 else "afternoon" if h < 18 else "evening"


def site(key: str) -> str:
    return key.split("/")[0].split(":")[0]


class BaselinePredictor:
    name = "baseline"

    def __init__(self, cfg=None, prior_strength: float = 4.0, tz: str = "America/New_York"):
        self.tz = ZoneInfo(cfg.timezone if cfg else tz)
        self.k = prior_strength
        self.stats: dict[tuple, list[float]] = defaultdict(lambda: [0.0, 0.0])  # context -> [drifts, n]
        self.trans: dict[str, Counter] = defaultdict(Counter)

    def _contexts(self, key: str, prev: str | None, at: datetime) -> list[tuple]:
        loc = at.astimezone(self.tz)
        hb, wk = hour_bucket(loc.hour), "weekend" if loc.weekday() >= 5 else "weekday"
        return [("kphw", key, prev, hb, wk), ("kh", key, hb), ("k", key), ("s", site(key)), ("all",)]

    def fit(self, sessions: Sequence[dict], episodes: Sequence) -> None:
        self.stats.clear()
        self.trans.clear()
        for a, b in zip(sessions, sessions[1:]):
            self.trans[a["key"]][b["key"]] += 1
        by_id = {s["id"]: i for i, s in enumerate(sessions)}
        for e in episodes:
            i = by_id.get(e.entry_session_id)
            prev = sessions[i - 1]["key"] if i else None
            for c in self._contexts(e.key, prev, e.entry_time):
                self.stats[c][0] += e.outcome == "drift"
                self.stats[c][1] += 1

    def predict(self, history: Sequence[dict], at: datetime, intent: dict, current_key: str) -> DriftScore:
        t0 = time.perf_counter()
        check_history(history, at)
        prev = history[-1]["key"] if history else None
        ctxs = self._contexts(current_key, prev, at)
        # back-off from the most general context to the most specific, using each as the prior
        d, n = self.stats.get(("all",), [0.0, 0.0])
        p = (d + 1) / (n + 2)
        for c in reversed(ctxs[:-1]):
            d, n = self.stats.get(c, [0.0, 0.0])
            p = (d + self.k * p) / (n + self.k)
        nxt = self.trans.get(current_key, Counter())
        total = sum(nxt.values()) or 1
        ranked = [(k, v / total) for k, v in nxt.most_common(10)]
        return DriftScore(p_drift=p, ranked_next=ranked, model_id="baseline-backoff-v1",
                          latency_ms=(time.perf_counter() - t0) * 1000)


@register("baseline")
def _factory(cfg=None):
    return BaselinePredictor(cfg)
