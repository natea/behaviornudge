"""Labeling pipeline: timeline sessions + intent context -> labeled sessions."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .calendar import read_events
from .config import Config
from .intent import (
    ActivitySpan,
    Intent,
    IntentResolver,
    Span,
    cap_timers,
    calendar_spans,
    corrected_time_report,
    default_related,
    load_timebox_spans,
    load_timer_spans,
)
from .labels import Label, LabelStore, cache_key, definitions_fp, rule_alignment, rule_label
from .timeline import parse_utc, read_sessions


class Classifier(Protocol):
    """External labeling layer (e.g. Jev). Returns a Label with model sources/confidences,
    plus whether the item must be reviewed by the user."""

    model: str

    def classify(self, s: dict, prev: dict | None, intent: Intent, rule: Label) -> tuple[Label, str | None]: ...


def channel_maps(cfg: Config) -> tuple[dict[str, str], dict[str, str]]:
    proj2ch = dict(cfg.section("projects"))
    ch2proj = {v: v for v in proj2ch.values()}  # intents and sessions both speak in channels
    return proj2ch, ch2proj


def build_intent(cfg: Config, sessions: list[dict], rule_labels: list[Label]) -> tuple[IntentResolver, list, dict]:
    tz = cfg.timezone
    icfg = cfg.section("intent")
    proj2ch, ch2proj = channel_maps(cfg)
    acts = [ActivitySpan(parse_utc(s["start_utc"]), parse_utc(s["end_utc"]), s["key"], lab.project if lab.project != "none" else None, lab.mode)
            for s, lab in zip(sessions, rule_labels)]
    ddir = cfg.data_dir
    raw_timers = load_timer_spans(ddir / "sunsama" / "timers.json", tz)
    capped = cap_timers(raw_timers, acts, default_related(ch2proj), icfg.get("timer_cap_min", 45))
    timers = [Span(c.span.start, c.capped_end, c.span.title, c.span.channel, c.span.ref) for c in capped if c.capped_end > c.span.start]
    planned = load_timebox_spans(ddir / "sunsama" / "timeboxes.json")
    cal = calendar_spans(read_events(ddir / "calendar" / "events.json"))
    resolver = IntentResolver(timers, planned, cal, acts, icfg.get("recent_creative_window_min", 30), ch2proj)
    info = {"timer_sessions": len(raw_timers), "planned_timeboxes": len(planned), "calendar_events": len(cal),
            "declined_filtering": "unavailable (EventKit helper does not report declined status)"}
    return resolver, corrected_time_report(capped), info


def label_all(cfg: Config, classifier: Classifier | None = None, limit_classifier: int | None = None) -> dict:
    ddir = cfg.data_dir
    sessions = read_sessions(ddir / "timeline.sqlite")
    proj2ch, _ = channel_maps(cfg)
    lcfg = cfg.section("labeling")
    defs = definitions_fp(lcfg.get("modes", {}))
    rules = [rule_label(s, proj2ch) for s in sessions]
    resolver, corrected, intent_info = build_intent(cfg, sessions, rules)
    store = LabelStore(ddir / "labels.sqlite")
    out_rows = []
    sent = 0
    queued = 0
    prev = None
    act_fp = jev_fingerprint(cfg)
    for s, lab in zip(sessions, rules):
        intent = resolver.resolve(parse_utc(s["start_utc"]))
        if act_fp and any(getattr(lab, d) is None for d in ("mode", "purpose", "project")):
            from .jev import activity_cache_key
            act = store.get(activity_cache_key(s["key"], s.get("title"), act_fp))
            if act:
                for d in ("mode", "purpose", "project"):
                    src = act.sources.get(d, "model")
                    lab.set(d, getattr(act, d), "user" if src == "user" else "model", act.confidence.get(d))
                lab.model, lab.prompt_fp = act.model, act.prompt_fp
        lab.set("alignment", rule_alignment(s, lab, intent), "rule")  # decided in code from the judgments
        ck = cache_key(s, intent, defs)
        if lab.missing():
            cached = store.get(ck)
            if cached:
                for d in lab.missing():
                    lab.set(d, getattr(cached, d), "cache" if cached.sources.get(d) != "user" else "user", cached.confidence.get(d))
        if lab.missing() and classifier and (limit_classifier is None or sent < limit_classifier):
            model_lab, review_reason = classifier.classify(s, prev, intent, lab)
            sent += 1
            for d in lab.missing():
                lab.set(d, getattr(model_lab, d), "model", model_lab.confidence.get(d))
            lab.model, lab.prompt_fp = model_lab.model, model_lab.prompt_fp
            store.put(ck, lab)
            if review_reason == "queue":
                store.enqueue(ck, s, intent, "low confidence or no mode fits", lab, "queue")
                queued += 1
            elif review_reason == "sample":
                store.enqueue(ck, s, intent, "medium confidence (review sample)", lab, "sample")
        out_rows.append((s["id"], lab.mode, lab.purpose, lab.project, lab.alignment, json.dumps(lab.sources),
                         json.dumps(lab.confidence), lab.model, lab.prompt_fp, intent.source, intent.title, intent.project))
        prev = s
    store.commit()
    store.close()
    con = sqlite3.connect(ddir / "labels.sqlite")
    con.executescript("DROP TABLE IF EXISTS session_labels; CREATE TABLE session_labels (session_id INTEGER PRIMARY KEY, mode TEXT, purpose TEXT, project TEXT, alignment TEXT, sources TEXT, confidence TEXT, model TEXT, prompt_fp TEXT, intent_source TEXT, intent_title TEXT, intent_project TEXT);")
    con.executemany("INSERT INTO session_labels VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", out_rows)
    con.commit()
    con.close()
    (ddir / "corrected_time.json").write_text(json.dumps(corrected, indent=1))
    return summarize(sessions, out_rows) | {"classifier_requests": sent, "queued_for_review": queued, "intent": intent_info,
                                             "intent_coverage": resolver.coverage([parse_utc(s["start_utc"]) for s in sessions])}


def summarize(sessions: list[dict], rows: list[tuple]) -> dict:
    total = sum(s["seconds"] for s in sessions) or 1
    by_src: dict[str, Counter] = {d: Counter() for d in ("mode", "purpose", "project", "alignment")}
    mode_time: Counter = Counter()
    for s, r in zip(sessions, rows):
        srcs = json.loads(r[5])
        for d in by_src:
            by_src[d][srcs.get(d, "unlabeled")] += s["seconds"]
        mode_time[r[1] or "unlabeled"] += s["seconds"]
    return {
        "sessions": len(sessions),
        "label_source_share_of_time": {d: {k: round(v / total, 3) for k, v in c.most_common()} for d, c in by_src.items()},
        "mode_share_of_time": {k: round(v / total, 3) for k, v in mode_time.most_common()},
    }


def read_labeled(cfg: Config) -> list[dict]:
    """Sessions joined with their labels, ordered by time."""
    ddir = cfg.data_dir
    sessions = read_sessions(ddir / "timeline.sqlite")
    con = sqlite3.connect(f"file:{ddir / 'labels.sqlite'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    labels = {r["session_id"]: dict(r) for r in con.execute("SELECT * FROM session_labels")}
    con.close()
    for s in sessions:
        s.update({k: v for k, v in labels.get(s["id"], {}).items() if k != "session_id"})
    return sessions


def trigger_index(cfg: Config):
    """Build trigger sources: calendar event ends, terminal agents working (context log).
    Notifications are reported as unavailable: no local notification history is recorded yet."""
    from .context import agent_working_spans
    from .episodes import TriggerIndex

    cal = read_events(cfg.data_dir / "calendar" / "events.json")
    agent = agent_working_spans(cfg.path("sources", "context_log"))
    info = {"calendar_ends": len(cal), "notifications": "unavailable (no notification history recorded)",
            "agent_working_spans": len(agent)}
    return TriggerIndex([e.end for e in cal], [], agent), info


def sunsama_channels(cfg: Config) -> list[str]:
    p = cfg.data_dir / "sunsama" / "tasks.json"
    chans = set(cfg.section("projects").values())
    if p.exists():
        chans |= {t.get("channel") for t in json.loads(p.read_text()) if t.get("channel")}
    return sorted(chans)


def jev_fingerprint(cfg: Config) -> str | None:
    from .jev import questions_fp
    lc = cfg.section("labeling")
    return questions_fp(lc.get("modes", {}), sunsama_channels(cfg), lc.get("classifier_model", "jev-1.14.0"))


def jev_pass(cfg: Config, min_seconds: float = 60, limit: int | None = None, workers: int = 8, labeler=None) -> dict:
    """Label each unique (activity, title) that rules left without a mode, once. Sessions
    shorter than min_seconds are included only when they fall inside a drift window."""
    from .episodes import load as load_episodes
    from .jev import JevLabeler, activity_cache_key

    ddir = cfg.data_dir
    sessions = read_sessions(ddir / "timeline.sqlite")
    proj2ch, _ = channel_maps(cfg)
    rules = [rule_label(s, proj2ch) for s in sessions]
    windows = []
    ep_path = ddir / "episodes.json"
    if ep_path.exists():
        from datetime import timedelta
        win = timedelta(minutes=cfg.section("drift").get("window_min", 15))
        windows = sorted((e.entry_time, e.entry_time + win) for e in load_episodes(ep_path))
    import bisect
    wstarts = [w[0] for w in windows]

    def in_window(t):
        i = bisect.bisect_right(wstarts, t) - 1
        return i >= 0 and windows[i][0] <= t < windows[i][1]

    labeler = labeler or JevLabeler(cfg, sunsama_channels(cfg))
    store = LabelStore(ddir / "labels.sqlite")
    todo, seen = [], set()
    prev_key = None
    for s, lab in zip(sessions, rules):
        if lab.mode is None and (s["seconds"] >= min_seconds or in_window(parse_utc(s["start_utc"]))):
            ck = activity_cache_key(s["key"], s.get("title"), labeler.fp)
            if ck not in seen and store.get(ck) is None:
                seen.add(ck)
                todo.append((ck, s, prev_key))
        prev_key = s["key"]
    if limit is not None:
        todo = todo[:limit]
    results = labeler.label_many(todo, workers=workers)
    by_ck = {ck: s for ck, s, _ in todo}
    counts = Counter()
    models = Counter()
    errors = []
    for ck, lab, review, err in results:
        if err:
            errors.append(err)
            counts["error"] += 1
            continue
        models[lab.model] += 1
        counts[review or "accepted"] += 1
        if review:
            from .intent import NO_INTENT
            store.enqueue(ck, by_ck[ck], NO_INTENT, "low probability or nothing fits" if review == "queue" else "review sample", lab, review)
        if review == "queue":
            # too uncertain to use: keep the mode out of the analysis until the user answers
            import copy
            held = copy.deepcopy(lab)
            held.mode = None
            held.sources.pop("mode", None)
            store.put(ck, held)
        else:
            store.put(ck, lab)
    store.close()
    return {"requests": len(todo), "outcomes": dict(counts), "models": dict(models), "errors": errors[:5], "fingerprint": labeler.fp}
