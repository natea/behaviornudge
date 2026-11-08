import os
import sqlite3

import pytest

from behaviornudge.sources import Record, Visit, connect_ro, read_captures, read_knowledgec, read_retrace_to, read_visits
from behaviornudge.timeline import resolve_overlaps, sessionize, to_pieces

from .conftest import at

CHROME = "com.google.Chrome"
ZED = "dev.zed.Zed"
SLACK = "com.tinyspeck.slackmacgap"


# --- 3.1 read-only readers -------------------------------------------------

def test_reader_does_not_modify_db(retrace_to_db):
    p = retrace_to_db([(ZED, at(0), at(5), "behaviornudge — x.py", None)])
    before = (os.stat(p).st_mtime_ns, p.read_bytes())
    recs = read_retrace_to(p)
    assert len(recs) == 1 and recs[0].seconds == 300
    assert (os.stat(p).st_mtime_ns, p.read_bytes()) == before


def test_connection_rejects_writes(retrace_to_db):
    p = retrace_to_db([])
    con = connect_ro(p)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM segment")
    con.close()


def test_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_retrace_to(tmp_path / "absent.db")


def test_jhammant_readers(jhammant_db):
    p = jhammant_db(
        captures=[(at(0), "Ghostty", "com.mitchellh.ghostty", "t", None, "SCREEN TEXT", "a caption"),
                  (at(1), "Zed", ZED, "proj", None, "x", None)],
        events=[("knowledgec", ZED, None, None, at(0), at(10), 600.0),
                ("chrome", CHROME, "Feed", "https://x.com/home", at(3), None, 0.0)],
    )
    caps = read_captures(p)
    assert caps[0].seconds == 60 and caps[0].caption == "a caption"
    assert not hasattr(caps[0], "text")  # screen text is not carried into the timeline
    kc = read_knowledgec(p)
    assert kc[0].seconds == 600
    assert read_visits(p)[0].url == "https://x.com/home"


# --- 3.3 sessions -----------------------------------------------------------

def _rec(bundle, s, e, url=None, src="retrace_to", title=None):
    return Record(s, e, src, bundle_id=bundle, url=url, title=title)


def test_rapid_records_collapse():
    recs = [_rec(CHROME, at(0, i * 8), at(0, i * 8 + 5), "https://x.com/home") for i in range(5)]
    sessions, _ = sessionize(to_pieces(resolve_overlaps(recs), []))
    assert len(sessions) == 1
    assert sessions[0].key == "x.com/home"
    assert sessions[0].start == at(0, 0) and sessions[0].end == at(0, 37)


def test_brief_blip_becomes_transition():
    recs = [_rec(ZED, at(0), at(1)), _rec(SLACK, at(1), at(1, 2)), _rec(ZED, at(1, 2), at(3))]
    sessions, transitions = sessionize(to_pieces(resolve_overlaps(recs), []))
    assert [s.key for s in sessions] == ["zed"]
    assert [t.key for t in transitions] == ["slack"]


def test_two_retraces_overlap_not_double_counted():
    a = _rec(CHROME, at(0), at(1), "https://x.com/home", src="retrace_to")
    b = _rec(CHROME, at(0), at(1), "https://x.com/home", src="capture")
    resolved = resolve_overlaps([a, b])
    assert sum(r.seconds for r in resolved) == 60
    assert {r.source for r in resolved} == {"retrace_to"}


def test_lower_priority_fills_gaps_only():
    hi = _rec(ZED, at(0), at(1), src="retrace_to")
    lo = _rec(SLACK, at(0, 30), at(2), src="knowledgec")
    resolved = resolve_overlaps([lo, hi])
    assert [(r.source, r.start, r.end) for r in resolved] == [("retrace_to", at(0), at(1)), ("knowledgec", at(1), at(2))]


def test_chrome_visit_refines_untitled_session_without_duration():
    rec = _rec(CHROME, at(0), at(5), url=None)
    visit = Visit(at(2), "https://x.com/home", CHROME)
    sessions, _ = sessionize(to_pieces(resolve_overlaps([rec]), [visit]))
    keys = {s.key: s.seconds for s in sessions}
    assert keys["x.com/home"] == 180  # from visit until end of the timed session
    assert sum(s.seconds for s in sessions) == 300  # visit added no time


def test_build_is_deterministic():
    recs = [_rec(ZED, at(0), at(2)), _rec(CHROME, at(2), at(4), "https://www.linkedin.com/feed/")]
    one = sessionize(to_pieces(resolve_overlaps(list(recs)), []))[0]
    two = sessionize(to_pieces(resolve_overlaps(list(reversed(recs))), []))[0]
    assert [(s.key, s.start, s.end) for s in one] == [(s.key, s.start, s.end) for s in two]


# --- 3.4 storage ------------------------------------------------------------

def test_local_hour_new_york():
    from datetime import UTC, datetime
    from behaviornudge.timeline import local_parts
    d, h, w = local_parts(datetime(2026, 11, 8, 18, 46, tzinfo=UTC), "America/New_York")
    assert (d, h, w) == ("2026-11-08", 13, 6)


def test_write_and_read_roundtrip(tmp_path):
    from behaviornudge.timeline import read_sessions, write_timeline
    recs = [_rec(ZED, at(0), at(2)), _rec(CHROME, at(2), at(4), "https://www.linkedin.com/feed/")]
    sessions, transitions = sessionize(to_pieces(resolve_overlaps(recs), []))
    p = tmp_path / "t.sqlite"
    write_timeline(p, sessions, transitions, "America/New_York", {"x": 1})
    rows = read_sessions(p)
    assert [r["key"] for r in rows] == ["zed", "linkedin.com/feed"]
    assert rows[0]["local_hour"] == 13 and rows[0]["seconds"] == 120
