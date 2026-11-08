"""Timeline build orchestration: read every source, resolve, sessionize, store."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .config import Config
from .sources import Record, read_captures, read_knowledgec, read_retrace_to, read_visits
from .timeline import ProjectResolver, resolve_overlaps, sessionize, to_pieces, write_timeline


MIN_DATE = datetime(2025, 1, 1, tzinfo=UTC)  # floor for bogus clock values (e.g. a 2012 capture)


def _clip(records: list[Record], until: datetime | None) -> list[Record]:
    out = []
    for r in records:
        if r.start < MIN_DATE:
            continue
        if until is None:
            out.append(r)
            continue
        if r.start >= until:
            continue
        if r.end > until:
            r = Record(r.start, until, r.source, r.bundle_id, r.app_name, r.url, r.title, r.caption, r.meta)
        out.append(r)
    return out


def gather(cfg: Config, since: datetime | None = None, until: datetime | None = None) -> tuple[list[Record], list, dict]:
    """Read all sources; a missing source is reported, not fatal."""
    summary: dict = {"sources": {}, "missing": []}
    records: list[Record] = []
    visits: list = []
    jh = cfg.path("sources", "retrace_jhammant_db")
    rt = cfg.path("sources", "retrace_to_db")

    def add(name: str, fn, path: Path, kind: str = "records"):
        nonlocal visits
        try:
            got = fn(path, since)
        except FileNotFoundError:
            summary["missing"].append({"source": name, "path": str(path)})
            return
        if kind == "visits":
            got = [v for v in got if v.time >= MIN_DATE and (not until or v.time < until)]
            visits = got
            times = [v.time for v in got]
        else:
            got = _clip(got, until)
            records.extend(got)
            times = [r.start for r in got]
        summary["sources"][name] = {
            "count": len(got),
            "first": min(times).isoformat() if times else None,
            "last": max(times).isoformat() if times else None,
        }

    add("retrace_to", read_retrace_to, rt)
    add("knowledgec", read_knowledgec, jh)
    add("capture", read_captures, jh)
    add("browser_visits", read_visits, jh, kind="visits")
    return records, visits, summary


def build_timeline(cfg: Config, out: Path | None = None, since: datetime | None = None,
                   until: datetime | None = None, resolver: ProjectResolver | None = None) -> dict:
    s = cfg.section("sessions")
    records, visits, summary = gather(cfg, since, until)
    resolved = resolve_overlaps(records)
    pieces = to_pieces(resolved, visits, resolver)
    sessions, transitions = sessionize(pieces, s.get("merge_gap_s", 30), s.get("transition_max_s", 3))
    out = out or cfg.data_dir / "timeline.sqlite"
    summary.update({
        "sessions": len(sessions),
        "transitions": len(transitions),
        "first": sessions[0].start.isoformat() if sessions else None,
        "last": sessions[-1].end.isoformat() if sessions else None,
        "hours": round(sum(x.seconds for x in sessions) / 3600, 1),
        "until": until.isoformat() if until else None,
        "since": since.isoformat() if since else None,
        "built_at": datetime.now(tz=UTC).isoformat(),
    })
    info = {k: v for k, v in summary.items() if k != "built_at"}
    write_timeline(out, sessions, transitions, cfg.timezone, info)
    summary["path"] = str(out)
    return summary
