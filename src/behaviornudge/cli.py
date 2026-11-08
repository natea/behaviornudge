"""Command-line entry point: `uv run behaviornudge <command>`."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

from .config import load_config


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    d = datetime.fromisoformat(s)
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def cmd_timeline_build(args) -> None:
    from .build import build_timeline

    cfg = load_config(args.config)
    resolver = None
    if not args.no_projects:
        try:
            from .projects import make_resolver
        except ImportError:  # project resolution not available yet
            make_resolver = None
        if make_resolver:
            from .context import FocusIndex
            focus = FocusIndex(cfg.path("sources", "context_log"))
            resolver = make_resolver(cfg, context_projects=focus.project_at if focus else None)
    summary = build_timeline(cfg, since=_dt(args.since), until=_dt(args.until), resolver=resolver)
    print(json.dumps(summary, indent=2))


def cmd_label(args) -> None:
    from .pipeline import label_all

    cfg = load_config(args.config)
    out = {}
    if args.jev:
        from .pipeline import jev_pass
        out["jev"] = jev_pass(cfg, min_seconds=args.min_seconds, limit=args.limit, workers=args.workers)
    out.update(label_all(cfg))
    print(json.dumps(out, indent=2))


def cmd_review(args) -> None:
    from .labels import LabelStore

    cfg = load_config(args.config)
    store = LabelStore(cfg.data_dir / "labels.sqlite")
    if args.action == "list":
        for it in store.open_items(args.kind, args.n):
            prop = json.loads(it["proposed"])
            intent = json.loads(it["intent"])
            print(f"#{it['id']:>5}  {it['key']:<28} {it['title'][:40]:<40}  intent={intent.get('source')}:{(intent.get('title') or '')[:30]}"
                  f"  proposed mode={prop.get('mode')} align={prop.get('alignment')}  ({it['reason']})")
    else:
        lab = store.answer(args.id, mode=args.mode, purpose=args.purpose, project=args.project, alignment=args.alignment)
        print(f"saved #{args.id}: mode={lab.mode} purpose={lab.purpose} project={lab.project} alignment={lab.alignment}")
    store.close()


def cmd_episodes(args) -> None:
    from .episodes import detect, save, summarize
    from .pipeline import read_labeled, trigger_index

    cfg = load_config(args.config)
    d = cfg.section("drift")
    sessions = read_labeled(cfg)
    trig, trig_info = trigger_index(cfg)
    eps = detect(sessions, d["prone_sites"], d["window_min"], d["threshold_min"], trig, cfg.timezone,
                 entry_mode=args.entry_mode, return_min_s=d.get("return_min_s", 60))
    save(eps, cfg.data_dir / "episodes.json")
    print(json.dumps({"entry_mode": args.entry_mode, "triggers": trig_info, **summarize(eps)}, indent=2))


def cmd_catalog(args) -> None:
    from collections import Counter, defaultdict

    from .pipeline import read_labeled
    from .predictors.behaviorgpt import (build_catalog_rows, catalog_keys, check_catalog_columns,
                                         save_catalog_state, write_catalog_parquet)

    cfg = load_config(args.config)
    sessions = read_labeled(cfg)
    min_n = cfg.section("predictors").get("behaviorgpt_catalog_min_sessions", 5)
    mapping = catalog_keys(sessions, min_n)
    mode_time: dict[str, Counter] = defaultdict(Counter)
    for s in sessions:
        if s.get("mode"):
            mode_time[mapping[s["key"]]][s["mode"]] += s["seconds"]
    modes = {cid: c.most_common(1)[0][0] for cid, c in mode_time.items()}
    rows = build_catalog_rows(sessions, modes, cfg.section("drift")["prone_sites"], min_n)
    path = write_catalog_parquet(rows, cfg.data_dir / "behaviorgpt_catalog.parquet")
    missing = check_catalog_columns(path)
    info = {"items": len(rows), "keys_mapped": len(mapping), "parquet": str(path), "missing_columns": missing,
            "modes": dict(Counter(modes.values()))}
    if args.action == "refresh-modes":
        import json as _j
        st_path = cfg.data_dir / "behaviorgpt_catalog.json"
        st = _j.loads(st_path.read_text())
        save_catalog_state(cfg, st["catalog_id"], mapping, modes)
        info["catalog_id"] = st["catalog_id"]
    if args.action == "upload":
        from behaviorgpt import UnboxAIClient
        from .config import get_secret
        from .egress import AuditLog

        client = UnboxAIClient(market=cfg.section("predictors").get("behaviorgpt_market", "us"),
                               timezone=cfg.timezone, api_key=get_secret("UNBOXAI_API_KEY"))
        AuditLog(cfg.data_dir / "audit.jsonl").record("unbox:embed-catalog", {"items": [r["id"] for r in rows]})
        job = client.embed(path, wait=True, timeout=1800.0)
        save_catalog_state(cfg, job.catalog_id, mapping, modes)
        info["catalog_id"] = job.catalog_id
        info["catalogs_on_account"] = [getattr(c, "catalog_id", None) or getattr(c, "id", None) for c in client.list_catalogs()]
    print(json.dumps(info, indent=2))


def cmd_report(args) -> None:
    from .report import run, write_report

    cfg = load_config(args.config)
    names = args.predictors.split(",") if args.predictors else cfg.section("predictors").get("enabled", ["baseline"])
    res = run(cfg, names, pre_entry_limit=args.pre_entry_limit, workers=args.workers)
    out = write_report(cfg, res)
    m = res["metrics"]
    print(json.dumps({"report": str(out), "decision": res["decision"],
                      "pooled": {n: m[n]["pooled"] for n in m if not n.startswith("_")},
                      "at_budget": {n: m[n]["at_budget"] for n in m if not n.startswith("_")},
                      "pre_entry": res["pre_entry"]}, indent=2))


def cmd_context_log(args) -> None:
    import threading

    from .context import ContextLogger, TimerPoller
    from .projects import discover_repos, project_for_folder

    cfg = load_config(args.config)
    cache = cfg.data_dir / "repos.json"
    repo_map = json.loads(cache.read_text()) if cache.exists() else discover_repos()
    logger = ContextLogger(cfg.path("sources", "context_log"), lambda cwd: project_for_folder(cwd, repo_map))
    stop = threading.Event()
    src_cfg = cfg.section("sources")
    timer_src = None
    if src_cfg.get("sunsama_timer") == "mcp":
        from .sunsama_mcp import fetch
        token_path = cfg.data_dir / "sunsama_oauth.json"
        timer_src = lambda: fetch(token_path)
    elif src_cfg.get("sunsama_timer_command"):
        timer_src = src_cfg["sunsama_timer_command"]
    if timer_src:
        threading.Thread(target=TimerPoller(logger, timer_src, 30).run, args=(stop,), daemon=True).start()
    print(f"logging herdr context to {logger.log_path} (Sunsama timer: {'on' if timer_src else 'off'}); Ctrl-C to stop")
    try:
        logger.run(stop)
    except KeyboardInterrupt:
        stop.set()


def cmd_sunsama_timer(args) -> None:
    from .sunsama_mcp import LoginRequired, fetch

    cfg = load_config(args.config)
    try:
        print(json.dumps(fetch(cfg.data_dir / "sunsama_oauth.json", interactive=args.login)))
    except LoginRequired as e:
        raise SystemExit(str(e))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="behaviornudge")
    p.add_argument("--config", default="behaviornudge.toml")
    sub = p.add_subparsers(dest="cmd", required=True)

    tl = sub.add_parser("timeline", help="activity timeline")
    tls = tl.add_subparsers(dest="sub", required=True)
    b = tls.add_parser("build", help="build data/timeline.sqlite from local sources")
    b.add_argument("--since", help="ISO time (UTC if no offset)")
    b.add_argument("--until", help="ISO cutoff; use a fixed value for reproducible builds")
    b.add_argument("--no-projects", action="store_true", help="skip terminal/editor project resolution")
    b.set_defaults(func=cmd_timeline_build)

    lb = sub.add_parser("label", help="label sessions (rules, cache, optional Jev)")
    lb.add_argument("--jev", action="store_true", help="send unlabeled sessions to TypeSafe Jev")
    lb.add_argument("--limit", type=int, default=None, help="max classifier requests")
    lb.add_argument("--min-seconds", type=float, default=60, help="Jev labels sessions at least this long (plus drift windows)")
    lb.add_argument("--workers", type=int, default=8)
    lb.set_defaults(func=cmd_label)

    rv = sub.add_parser("review", help="answer queued low-confidence labels")
    rv.add_argument("action", choices=["list", "answer"])
    rv.add_argument("id", nargs="?", type=int)
    rv.add_argument("--kind", default="queue", choices=["queue", "sample"])
    rv.add_argument("-n", type=int, default=30)
    rv.add_argument("--mode", choices=["creative", "reactive", "consuming"])
    rv.add_argument("--purpose", choices=["lookup", "reply", "follow-link", "browse", "n/a"])
    rv.add_argument("--project")
    rv.add_argument("--alignment", choices=["on-task", "supporting", "off-task", "no-intent"])
    rv.set_defaults(func=cmd_review)

    ep = sub.add_parser("episodes", help="detect drift episodes in the labeled timeline")
    ep.add_argument("--entry-mode", choices=["purposeful", "any"], default="purposeful",
                    help="'any' widens entries to every non-on-task session on a drift-prone site")
    ep.set_defaults(func=cmd_episodes)

    ct = sub.add_parser("catalog", help="build (and upload) the BehaviorGPT activity catalog")
    ct.add_argument("action", choices=["build", "upload", "refresh-modes"],
                    help="refresh-modes: recompute item modes from current labels without re-uploading")
    ct.set_defaults(func=cmd_catalog)

    rp = sub.add_parser("report", help="run the rolling evaluation and write data/report.md")
    rp.add_argument("--predictors", help="comma-separated (default: predictors.enabled in config)")
    rp.add_argument("--pre-entry-limit", type=int, default=200)
    rp.add_argument("--workers", type=int, default=4)
    rp.set_defaults(func=cmd_report)

    cx = sub.add_parser("context", help="live context logger (herdr focus, folder, agent status)")
    cx.add_argument("action", choices=["log"])
    cx.set_defaults(func=cmd_context_log)

    st = sub.add_parser("sunsama-timer", help="read the Sunsama active timer via Sunsama's MCP server (read-only)")
    st.add_argument("--login", action="store_true", help="sign in through the browser (first run)")
    st.set_defaults(func=cmd_sunsama_timer)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
