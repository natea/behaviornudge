"""Rolling weekly evaluation of drift predictors (design D9).

For each test week: fit predictors on data before the week, query every episode entry with
only the sessions that ended before it, record failures without stopping, and compute
metrics per predictor, per week, and pooled.
"""

from __future__ import annotations

import bisect
import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Sequence
from zoneinfo import ZoneInfo

from .episodes import Episode
from .predictors import DriftScore, Predictor
from .timeline import parse_utc

HISTORY_SESSIONS = 200


@dataclass
class Prediction:
    predictor: str
    episode_id: int  # entry session id
    entry_time: str
    week: str
    outcome: str
    p_drift: float | None
    extras: dict = field(default_factory=dict)
    latency_ms: float | None = None
    error: str | None = None
    kind: str = "entry"  # entry / pre-entry


def iso_week(t: datetime, tz: str) -> str:
    return t.astimezone(ZoneInfo(tz)).strftime("%G-W%V")


def week_start(week: str, tz: str) -> datetime:
    return datetime.strptime(week + "-1", "%G-W%V-%u").replace(tzinfo=ZoneInfo(tz))


class History:
    """Hands out only sessions that ended at or before a decision time."""

    def __init__(self, sessions: Sequence[dict]):
        self.sessions = sorted(sessions, key=lambda s: s["end_utc"])
        self.ends = [parse_utc(s["end_utc"]) for s in self.sessions]

    def before(self, at: datetime, n: int = HISTORY_SESSIONS) -> list[dict]:
        i = bisect.bisect_right(self.ends, at)
        return self.sessions[max(0, i - n):i]


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

def roc_auc(scores: Sequence[float], labels: Sequence[int]) -> float | None:
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    # rank-based (Mann-Whitney) with ties counted as half
    allv = sorted((s, y) for s, y in zip(scores, labels))
    ranks: dict[float, float] = {}
    i = 0
    while i < len(allv):
        j = i
        while j < len(allv) and allv[j][0] == allv[i][0]:
            j += 1
        ranks[allv[i][0]] = (i + j + 1) / 2
        i = j
    r_pos = sum(ranks[s] for s in pos)
    return (r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def pr_auc(scores: Sequence[float], labels: Sequence[int]) -> float | None:
    """Average precision."""
    npos = sum(labels)
    if npos == 0 or npos == len(labels):
        return None
    pairs = sorted(zip(scores, labels), key=lambda p: -p[0])
    tp = 0
    ap = 0.0
    for k, (_, y) in enumerate(pairs, 1):
        if y:
            tp += 1
            ap += tp / k
    return ap / npos


def at_budget(preds: Sequence[Prediction], episodes: dict[int, Episode], working_days: int, budget_per_day: float) -> dict:
    """Precision/recall when nudging the top-scored entries, at most budget_per_day per working day."""
    ok = sorted((p for p in preds if p.p_drift is not None), key=lambda p: -p.p_drift)
    max_n = int(budget_per_day * max(working_days, 1))
    chosen = ok[:max_n]
    # nudge only when the score clears the score of the last allowed slot and beats the base rate
    tp = [p for p in chosen if p.outcome == "drift"]
    total_pos = sum(p.outcome == "drift" for p in ok)
    leads = [episodes[p.episode_id].to_first_drift_min for p in tp if episodes[p.episode_id].to_first_drift_min is not None]
    return {
        "nudges": len(chosen), "max_nudges": max_n,
        "threshold": round(chosen[-1].p_drift, 4) if chosen else None,
        "precision": round(len(tp) / len(chosen), 3) if chosen else None,
        "recall": round(len(tp) / total_pos, 3) if total_pos else None,
        "false_alarms_per_day": round((len(chosen) - len(tp)) / max(working_days, 1), 2),
        "median_lead_min": round(statistics.median(leads), 2) if leads else None,
    }


def summarize_preds(preds: Sequence[Prediction]) -> dict:
    ok = [p for p in preds if p.p_drift is not None]
    s = [p.p_drift for p in ok]
    y = [int(p.outcome == "drift") for p in ok]
    lat = [p.latency_ms for p in ok if p.latency_ms is not None]
    return {
        "n": len(ok), "drifts": sum(y), "failures": sum(p.error is not None for p in preds),
        "roc_auc": _r(roc_auc(s, y)), "pr_auc": _r(pr_auc(s, y)), "base_rate": _r(sum(y) / len(y)) if y else None,
        "mean_latency_ms": round(statistics.mean(lat), 1) if lat else None,
    }


def _r(x):
    return round(x, 4) if isinstance(x, float) else x


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------

class PredictionCache:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, dict] = {}
        if path.exists():
            for line in path.read_text().splitlines():
                d = json.loads(line)
                self.data[d["_k"]] = d

    def key(self, predictor_id: str, kind: str, ref: str) -> str:
        return f"{predictor_id}|{kind}|{ref}"

    def get(self, k: str) -> dict | None:
        return self.data.get(k)

    def put(self, k: str, d: dict) -> None:
        d = {**d, "_k": k}
        self.data[k] = d
        with self.path.open("a") as f:
            f.write(json.dumps(d, default=str) + "\n")


def run_predictor(pred: Predictor, cache_id: str, tasks: list[tuple[str, int, datetime, str, str, str]],
                  hist: History, cache: PredictionCache | None, workers: int = 1, kind: str = "entry") -> list[Prediction]:
    """tasks: (week, ref_id, at, current_key, outcome, intent_source). Failures are recorded, never raised."""

    def one(t):
        week, ref, at, cur, outcome, isrc = t
        ck = cache.key(cache_id, kind, f"{ref}@{at.isoformat()}") if cache else None
        if cache and (hit := cache.get(ck)) and hit.get("error") is None:
            return Prediction(pred.name, ref, at.isoformat(), week, outcome, hit["p_drift"], hit.get("extras", {}), hit.get("latency_ms"), None, kind)
        try:
            out: DriftScore = pred.predict(hist.before(at), at, {"source": isrc}, cur)
            p = Prediction(pred.name, ref, at.isoformat(), week, outcome, float(out.p_drift), out.extras, out.latency_ms, None, kind)
        except Exception as e:  # recorded, excluded from metrics
            p = Prediction(pred.name, ref, at.isoformat(), week, outcome, None, {}, None, f"{type(e).__name__}: {str(e)[:200]}", kind)
        if cache:
            cache.put(ck, {"p_drift": p.p_drift, "extras": p.extras, "latency_ms": p.latency_ms, "error": p.error})
        return p

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(one, tasks))
    return [one(t) for t in tasks]


def evaluate(sessions: Sequence[dict], episodes: Sequence[Episode], predictors: dict[str, Callable[[], Predictor]],
             tz: str, budget_per_day: float = 5, min_train_weeks: int = 2, cache: PredictionCache | None = None,
             workers: dict[str, int] | None = None, cache_ids: dict[str, str] | None = None) -> dict:
    hist = History(sessions)
    by_week: dict[str, list[Episode]] = defaultdict(list)
    for e in episodes:
        by_week[iso_week(e.entry_time, tz)].append(e)
    weeks = sorted(by_week)
    test_weeks = weeks[min_train_weeks:]
    ep_by_id = {e.entry_session_id: e for e in episodes}
    preds: list[Prediction] = []
    for name, factory in predictors.items():
        for w in test_weeks:
            ws = week_start(w, tz)
            pred = factory()
            train_s = [s for s in sessions if parse_utc(s["end_utc"]) < ws]
            train_e = [e for e in episodes if e.entry_time < ws]
            pred.fit(train_s, train_e)
            tasks = [(w, e.entry_session_id, e.entry_time, e.key, e.outcome, e.intent_source) for e in by_week[w]]
            use_cache = cache if (cache_ids and name in cache_ids) else None  # cache only paid, zero-shot predictors
            preds += run_predictor(pred, (cache_ids or {}).get(name, name), tasks, hist, use_cache, (workers or {}).get(name, 1))
    return {"test_weeks": test_weeks, "predictions": preds, "metrics": metrics(preds, ep_by_id, sessions, tz, budget_per_day)}


def working_days(sessions: Sequence[dict], weeks: set[str], tz: str) -> int:
    z = ZoneInfo(tz)
    days = set()
    for s in sessions:
        t = parse_utc(s["start_utc"]).astimezone(z)
        if t.weekday() < 5 and t.strftime("%G-W%V") in weeks:
            days.add(t.date())
    return len(days)


def metrics(preds: Sequence[Prediction], ep_by_id: dict[int, Episode], sessions: Sequence[dict], tz: str, budget: float) -> dict:
    out: dict = {}
    names = sorted({p.predictor for p in preds})
    weeks = sorted({p.week for p in preds})
    wd = working_days(sessions, set(weeks), tz)
    for n in names:
        mine = [p for p in preds if p.predictor == n and p.kind == "entry"]
        weekly = {}
        for w in weeks:
            wp = [p for p in mine if p.week == w]
            weekly[w] = summarize_preds(wp)
        def by(fn):
            g = defaultdict(list)
            for p in mine:
                for k in fn(p):
                    g[str(k)].append(p)
            return {k: summarize_preds(v) for k, v in sorted(g.items())}
        z = ZoneInfo(tz)
        out[n] = {
            "pooled": summarize_preds(mine),
            "at_budget": at_budget(mine, ep_by_id, wd, budget),
            "weekly": weekly,
            "by_trigger": by(lambda p: ep_by_id[p.episode_id].triggers),
            "by_intent_source": by(lambda p: [ep_by_id[p.episode_id].intent_source]),
            "by_hour_bucket": by(lambda p: [_bucket(datetime.fromisoformat(p.entry_time).astimezone(z).hour)]),
            "failures": [p.error for p in mine if p.error][:5],
        }
    out["_working_days"] = wd
    return out


def _bucket(h: int) -> str:
    return "night" if h < 6 else "morning" if h < 12 else "afternoon" if h < 18 else "evening"


def decide(metrics: dict, candidate: str = "behaviorgpt", baseline: str = "baseline", max_latency_ms: float = 3000) -> dict:
    """Recommend the candidate only if it beats the baseline's PR-AUC in most defined weeks,
    pooled, and is fast enough for live nudges."""
    if candidate not in metrics or baseline not in metrics:
        return {"recommend": baseline, "reason": f"{candidate} was not evaluated"}
    c, b = metrics[candidate], metrics[baseline]
    wins = losses = 0
    for w, cm in c["weekly"].items():
        bm = b["weekly"].get(w, {})
        if cm.get("pr_auc") is None or bm.get("pr_auc") is None:
            continue
        wins += cm["pr_auc"] > bm["pr_auc"]
        losses += cm["pr_auc"] <= bm["pr_auc"]
    cp, bp = c["pooled"]["pr_auc"] or 0, b["pooled"]["pr_auc"] or 0
    lat = c["pooled"]["mean_latency_ms"] or 0
    ok = wins > losses and cp > bp and lat <= max_latency_ms
    reason = (f"{candidate} won {wins} of {wins + losses} comparable weeks on PR-AUC; pooled PR-AUC "
              f"{cp:.3f} vs {bp:.3f}; mean latency {lat:.0f} ms (limit {max_latency_ms:.0f}).")
    return {"recommend": candidate if ok else baseline, "reason": reason, "weeks_won": wins, "weeks_compared": wins + losses}


# ---------------------------------------------------------------------------
# pre-entry analysis at trigger moments (9.3)
# ---------------------------------------------------------------------------

def trigger_tasks(trigger_times: Sequence[datetime], episodes: Sequence[Episode], sessions: Sequence[dict],
                  test_weeks: set[str], tz: str, horizon_min: float = 10, limit: int | None = None) -> list[tuple]:
    """For each trigger moment T: label 'drift' if a drift episode starts within horizon after T."""
    hist = History(sessions)
    entries = sorted(episodes, key=lambda e: e.entry_time)
    etimes = [e.entry_time for e in entries]
    out = []
    for T in sorted(trigger_times):
        w = iso_week(T, tz)
        if w not in test_weeks:
            continue
        prev = hist.before(T, 1)
        if not prev:
            continue
        i = bisect.bisect_left(etimes, T)
        label = "clean"
        while i < len(entries) and entries[i].entry_time <= T + timedelta(minutes=horizon_min):
            if entries[i].outcome == "drift":
                label = "drift"
                break
            i += 1
        out.append((w, int(T.timestamp()), T, prev[-1]["key"], label, "trigger"))
    if limit and len(out) > limit:
        step = len(out) / limit
        out = [out[int(k * step)] for k in range(limit)]
    return out
