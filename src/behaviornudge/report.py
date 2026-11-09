"""End-to-end evaluation run and Markdown report (task 9.4)."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

from .calendar import read_events
from .config import Config
from .episodes import load as load_episodes
from .episodes import summarize as summarize_episodes
from .evaluate import PredictionCache, decide, evaluate, metrics, run_predictor, summarize_preds, trigger_tasks, History
from .pipeline import read_labeled
from .predictors import create

PALETTE = {"baseline": "#2E5B86", "behaviorgpt": "#B0346A"}


def run(cfg: Config, predictors: list[str], pre_entry_limit: int = 200, workers: int = 4) -> dict:
    sessions = read_labeled(cfg)
    episodes = load_episodes(cfg.data_dir / "episodes.json")
    budget = cfg.section("drift").get("nudge_budget_per_day", 5)
    cache = PredictionCache(cfg.data_dir / "predictions_cache.jsonl")
    factories, cache_ids, wk = {}, {}, {}
    for name in predictors:
        factories[name] = (lambda n=name: create(n, cfg))
        if name == "behaviorgpt":
            p = create(name, cfg)
            import hashlib
            modes_fp = hashlib.sha256(json.dumps(p.modes, sort_keys=True).encode()).hexdigest()[:8]
            cache_ids[name] = f"behaviorgpt:{p.catalog_id}:{modes_fp}"  # scores depend on item modes
            wk[name] = workers
    res = evaluate(sessions, episodes, factories, cfg.timezone, budget, cache=cache, workers=wk, cache_ids=cache_ids)
    # pre-entry analysis at trigger moments (meeting ends), test weeks only
    ends = [e.end for e in read_events(cfg.data_dir / "calendar" / "events.json")]
    ttasks = trigger_tasks(ends, episodes, sessions, set(res["test_weeks"]), cfg.timezone, limit=pre_entry_limit)
    hist = History(sessions)
    pre = {}
    for name in predictors:
        pred = create(name, cfg)
        pred.fit(sessions[: len(sessions) // 2], [e for e in episodes if e.entry_time.isoformat() < sessions[len(sessions) // 2]["start_utc"]])
        out = run_predictor(pred, cache_ids.get(name, name), ttasks, hist, cache if name in cache_ids else None,
                            wk.get(name, 1), kind="pre-entry")
        pre[name] = summarize_preds(out)
    res["pre_entry"] = {"triggers": len(ttasks), "drift_follows": sum(t[4] == "drift" for t in ttasks), "by_predictor": pre}
    res["decision"] = decide(res["metrics"])
    res["episode_summary"] = summarize_episodes(episodes)
    return res


# ---------------------------------------------------------------------------
# charts (tiny inline SVG writers)
# ---------------------------------------------------------------------------

def svg_weekly(metrics: dict, weeks: list[str], names: list[str], path: Path) -> None:
    W, H, pad = 760, 260, 40
    xs = {w: pad + i * (W - 2 * pad) / max(len(weeks) - 1, 1) for i, w in enumerate(weeks)}
    y = lambda v: H - pad - v * (H - 2 * pad)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="system-ui,sans-serif" font-size="11">',
             f'<rect width="{W}" height="{H}" fill="white"/>']
    for g in (0, .25, .5, .75, 1):
        parts.append(f'<line x1="{pad}" x2="{W - pad}" y1="{y(g)}" y2="{y(g)}" stroke="#E6EAEF"/><text x="8" y="{y(g) + 4}" fill="#7A8494">{g:.2f}</text>')
    for n in names:
        pts = [(xs[w], y(metrics[n]["weekly"][w]["pr_auc"])) for w in weeks if metrics[n]["weekly"].get(w, {}).get("pr_auc") is not None]
        if pts:
            parts.append(f'<polyline fill="none" stroke="{PALETTE.get(n, "#555")}" stroke-width="2" points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in pts)}"/>')
            parts += [f'<circle cx="{a:.1f}" cy="{b:.1f}" r="2.5" fill="{PALETTE.get(n, "#555")}"/>' for a, b in pts]
    for i, w in enumerate(weeks):
        if i % 3 == 0:
            parts.append(f'<text x="{xs[w]:.1f}" y="{H - 12}" text-anchor="middle" fill="#7A8494">{w[5:]}</text>')
    for i, n in enumerate(names):
        parts.append(f'<rect x="{pad + i * 130}" y="10" width="12" height="3" fill="{PALETTE.get(n, "#555")}"/><text x="{pad + 16 + i * 130}" y="15" fill="#18202B">{n}</text>')
    parts.append(f'<text x="{W - pad}" y="15" text-anchor="end" fill="#7A8494">PR-AUC by test week</text></svg>')
    path.write_text("".join(parts))


def svg_bars(rows: list[tuple[str, float]], title: str, path: Path, color: str = "#2E5B86") -> None:
    W, bh = 520, 22
    H = 40 + bh * len(rows)
    mx = max((v for _, v in rows), default=1) or 1
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="system-ui,sans-serif" font-size="11">',
             f'<rect width="{W}" height="{H}" fill="white"/><text x="10" y="18" fill="#18202B" font-weight="600">{title}</text>']
    for i, (label, v) in enumerate(rows):
        yy = 30 + i * bh
        parts.append(f'<text x="10" y="{yy + 14}" fill="#4A5565">{label}</text>'
                     f'<rect x="170" y="{yy + 4}" width="{(W - 240) * v / mx:.1f}" height="12" fill="{color}"/>'
                     f'<text x="{W - 10}" y="{yy + 14}" text-anchor="end" fill="#18202B">{v:.1%}</text>')
    parts.append("</svg>")
    path.write_text("".join(parts))


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def _fmt(v, pct=False):
    if v is None:
        return "–"
    return f"{v:.1%}" if pct else (f"{v:.3f}" if isinstance(v, float) else str(v))


def coverage(cfg: Config) -> tuple[dict, dict]:
    con = sqlite3.connect(cfg.data_dir / "labels.sqlite")
    con.execute(f"ATTACH '{cfg.data_dir / 'timeline.sqlite'}' AS t")
    total = con.execute("SELECT sum(seconds) FROM t.sessions").fetchone()[0] or 1
    src = Counter()
    for sources, secs in con.execute("SELECT l.sources, s.seconds FROM session_labels l JOIN t.sessions s ON s.id = l.session_id"):
        src[json.loads(sources).get("mode", "unlabeled")] += secs
    intents = Counter()
    for isrc, secs in con.execute("SELECT l.intent_source, s.seconds FROM session_labels l JOIN t.sessions s ON s.id = l.session_id"):
        intents[isrc] += secs
    con.close()
    return ({k: v / total for k, v in src.most_common()}, {k: v / total for k, v in intents.most_common()})


def caveats(res: dict, names: list[str], label_cov: dict) -> list[str]:
    """Plain-language caveats computed from this run's own numbers."""
    m, out = res["metrics"], []
    rocs = {n: m[n]["pooled"]["roc_auc"] for n in names if m[n]["pooled"]["roc_auc"] is not None}
    if rocs:
        best = max(rocs, key=rocs.get)
        parts = ", ".join(f"{n} {v:.2f}" for n, v in rocs.items())
        if rocs[best] < 0.65:
            msg = f"**The prediction signal is real but small.** ROC-AUC (0.5 = chance): {parts}. Better than chance is not yet good."
            near = [n for n, v in rocs.items() if abs(v - 0.5) < 0.03]
            if near:
                msg += f" {', '.join(near)} is at chance with the current labels."
            out.append(msg)
    n0 = names[0]
    b, pooled = m[n0]["at_budget"], m[n0]["pooled"]
    if pooled["n"] and b["max_nudges"] >= 0.5 * pooled["n"]:
        per_day = pooled["n"] / max(m["_working_days"], 1)
        precs = ", ".join(f"{n} {m[n]['at_budget']['precision']:.0%}" for n in names if m[n]["at_budget"]["precision"] is not None)
        out.append(f"**The nudge budget doesn't actually limit anything.** There are about {per_day:.1f} drift-prone visits per working day, "
                   f"so the budget lets each model nudge {b['nudges'] / pooled['n']:.0%} of visits. Precision: {precs}. "
                   "A tighter budget (1–2 a day) would test whether the top-ranked predictions are worth acting on.")
    weekly = [w for w in m[n0]["weekly"].values() if w["n"]]
    drifts = sorted(w["drifts"] for w in weekly)
    if drifts and drifts[len(drifts) // 2] < 10:
        singles = [wk for wk, w in m[n0]["weekly"].items() if w["drifts"] == 1]
        out.append(f"**Weekly numbers are noisy.** Weeks have {drifts[0]}–{drifts[-1]} drifts (median {drifts[len(drifts) // 2]}), "
                   f"so a single drift can swing a week's score ({len(singles)} weeks have exactly one drift).")
    pe = res.get("pre_entry", {})
    if pe and pe.get("drift_follows", 0) < 10:
        out.append(f"**The \"predict at meeting end\" test can't conclude anything.** Only {pe['drift_follows']} of {pe['triggers']} "
                   "meeting ends were followed by a drift.")
    unl = label_cov.get("unlabeled", 0)
    if unl > 0.1:
        if label_cov.get("model", 0) > 0:
            out.append(f"**{unl:.0%} of your time still has no mode label.** It's mostly sessions under a minute outside drift windows, "
                       "plus activities waiting in your review queue (`behaviornudge review list`). Unlabeled time can't count as drift.")
        else:
            out.append(f"**{unl:.0%} of your time has no mode label.** Unlabeled time can't count as drift, so drift is undercounted "
                       "and the comparison may change once the Jev layer labels it.")
    if "behaviorgpt" in names:
        out.append("**Time of day is still an open question.** Shifting one whole history by 12 hours didn't change BehaviorGPT's "
                   "answer. That's expected if it reads the delays between events (as its technical report describes), since a "
                   "uniform shift keeps every delay the same. Whether clock time matters needs a different test.")
    return out


def write_report(cfg: Config, res: dict) -> Path:
    d = cfg.data_dir
    m, names = res["metrics"], [n for n in res["metrics"] if not n.startswith("_")]
    weeks = res["test_weeks"]
    svg_weekly(m, weeks, names, d / "report_weekly_prauc.svg")
    label_cov, intent_cov = coverage(cfg)
    svg_bars(sorted(label_cov.items(), key=lambda kv: -kv[1]), "Mode label source (share of time)", d / "report_label_sources.svg")
    svg_bars(sorted(intent_cov.items(), key=lambda kv: -kv[1]), "Intent source (share of time)", d / "report_intent_sources.svg", "#2F7A55")
    corrected = json.loads((d / "corrected_time.json").read_text()) if (d / "corrected_time.json").exists() else []
    es = res["episode_summary"]
    dec = res["decision"]
    L = [f"# behaviornudge drift-prediction report",
         f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} · test weeks {weeks[0]}–{weeks[-1]} ({len(weeks)}) · working days {m['_working_days']}_", "",
         f"## Recommendation: **{dec['recommend']}**", "", dec["reason"], "",
         "## Pooled results (episode entries)", "",
         "| predictor | episodes | drifts | base rate | ROC-AUC | PR-AUC | failures | mean latency |",
         "|---|---|---|---|---|---|---|---|"]
    for n in names:
        p = m[n]["pooled"]
        L.append(f"| {n} | {p['n']} | {p['drifts']} | {_fmt(p['base_rate'], True)} | {_fmt(p['roc_auc'])} | {_fmt(p['pr_auc'])} | {p['failures']} | {_fmt(p['mean_latency_ms'])} ms |")
    L += ["", f"## At the nudge budget (≤ {cfg.section('drift').get('nudge_budget_per_day', 5)} per working day)", "",
          "| predictor | nudges | precision | recall | false alarms/day | median lead (min) | threshold |", "|---|---|---|---|---|---|---|"]
    for n in names:
        b = m[n]["at_budget"]
        L.append(f"| {n} | {b['nudges']}/{b['max_nudges']} | {_fmt(b['precision'], True)} | {_fmt(b['recall'], True)} | {b['false_alarms_per_day']} | {_fmt(b['median_lead_min'])} | {_fmt(b['threshold'])} |")
    L += ["", "## By week", "", "![PR-AUC by week](report_weekly_prauc.svg)", "",
          "| week | " + " | ".join(f"{n} PR-AUC" for n in names) + " | drifts / episodes |", "|---|" + "---|" * (len(names) + 1)]
    for w in weeks:
        first = m[names[0]]["weekly"][w]
        L.append(f"| {w} | " + " | ".join(_fmt(m[n]["weekly"][w]["pr_auc"]) for n in names) + f" | {first['drifts']} / {first['n']} |")
    for title, key in [("By trigger", "by_trigger"), ("By intent source", "by_intent_source"), ("By time of day", "by_hour_bucket")]:
        groups = sorted(set().union(*[m[n][key].keys() for n in names]))
        L += ["", f"## {title}", "", "| group | episodes | drifts | " + " | ".join(f"{n} ROC-AUC" for n in names) + " |", "|---|---|---|" + "---|" * len(names)]
        for g in groups:
            first = m[names[0]][key].get(g, {})
            L.append(f"| {g} | {first.get('n', 0)} | {first.get('drifts', 0)} | " + " | ".join(_fmt(m[n][key].get(g, {}).get("roc_auc")) for n in names) + " |")
    pe = res["pre_entry"]
    L += ["", "## Before entry: prediction at meeting ends", "",
          f"{pe['triggers']} meeting-end moments in test weeks; a drift episode followed within 10 min after {pe['drift_follows']}.", "",
          "| predictor | ROC-AUC | PR-AUC | failures |", "|---|---|---|---|"]
    for n, s in pe["by_predictor"].items():
        L.append(f"| {n} | {_fmt(s['roc_auc'])} | {_fmt(s['pr_auc'])} | {s['failures']} |")
    L += ["", "## Drift episodes", "",
          f"{es['episodes']} episodes, {es['drifts']} drifts ({es['drift_rate']:.1%}), {es['drift_minutes']:.0f} drift minutes.", "",
          "| site | episodes | drift rate |", "|---|---|---|"]
    L += [f"| {k} | {v['episodes']} | {v['drift_rate']:.1%} |" for k, v in es["by_site"].items()]
    L += ["", "## Coverage", "", "![Label sources](report_label_sources.svg)", "", "![Intent sources](report_intent_sources.svg)", "",
          "## Corrected time per Sunsama task", "", "| task | channel | recorded min | worked min |", "|---|---|---|---|"]
    L += [f"| {c['task'][:50]} | {c.get('channel') or '–'} | {c['recorded_min']:.0f} | {c['corrected_min']:.0f} |" for c in corrected[:15]]
    prev_path = d / "report_rules_only.json"
    if prev_path.exists():
        prev = json.loads(prev_path.read_text())
        pm, pe_ = prev["metrics"], prev["episode_summary"]
        L += ["", "## Compared with the rules-only run", "",
              "| | rules-only labels | this run |", "|---|---|---|",
              f"| episodes / drifts | {pe_['episodes']} / {pe_['drifts']} ({pe_['drift_rate']:.1%}) | {es['episodes']} / {es['drifts']} ({es['drift_rate']:.1%}) |"]
        for n in names:
            if n in pm:
                a, b_ = pm[n]["pooled"], m[n]["pooled"]
                L.append(f"| {n} ROC-AUC / PR-AUC | {_fmt(a['roc_auc'])} / {_fmt(a['pr_auc'])} | {_fmt(b_['roc_auc'])} / {_fmt(b_['pr_auc'])} |")
        if "decision" in prev:
            L.append(f"| recommendation | {prev['decision']['recommend']} | {dec['recommend']} |")
    L += ["", "## Caveats", ""] + [f"- {c}" for c in caveats(res, names, label_cov)]
    fails = {n: m[n]["failures"] for n in names if m[n]["failures"]}
    L += ["", "## Notes", "",
          "- Labels in this run: see the label-source chart; unlabeled time cannot count as drift, so drift is undercounted until the Jev layer runs.",
          "- BehaviorGPT receives real event timestamps via the raw API (SDK `complete()` re-stamps events near now).",
          "- Notification triggers are unavailable (no local notification history); declined meetings cannot be filtered (EventKit helper).",
          f"- Failures (excluded from metrics): {json.dumps(fails) if fails else 'none'}"]
    out = d / "report.md"
    out.write_text("\n".join(L) + "\n")
    (d / "report.json").write_text(json.dumps({k: v for k, v in res.items() if k != "predictions"}, indent=1, default=str))
    return out
