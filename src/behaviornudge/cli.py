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
        resolver = make_resolver(cfg) if make_resolver else None
    summary = build_timeline(cfg, since=_dt(args.since), until=_dt(args.until), resolver=resolver)
    print(json.dumps(summary, indent=2))


def cmd_label(args) -> None:
    from .pipeline import label_all

    cfg = load_config(args.config)
    classifier = None
    if args.jev:
        from .jev import JevClassifier
        classifier = JevClassifier(cfg)
    print(json.dumps(label_all(cfg, classifier, args.limit), indent=2))


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

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
