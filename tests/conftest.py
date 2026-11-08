import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

T0 = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)


def at(minutes: float = 0, seconds: float = 0) -> datetime:
    return T0 + timedelta(minutes=minutes, seconds=seconds)


def _ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


@pytest.fixture
def retrace_to_db(tmp_path):
    """Factory: build a retrace.to-shaped DB from (bundle, start, end, title, url) rows."""
    def make(rows):
        p = tmp_path / "retrace_to.db"
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE segment (id INTEGER PRIMARY KEY AUTOINCREMENT, bundleID TEXT NOT NULL, startDate INTEGER NOT NULL, endDate INTEGER NOT NULL, windowName TEXT, browserUrl TEXT, type INTEGER NOT NULL)")
        con.executemany("INSERT INTO segment (bundleID,startDate,endDate,windowName,browserUrl,type) VALUES (?,?,?,?,?,0)",
                        [(b, int(s.timestamp() * 1000), int(e.timestamp() * 1000), t, u) for b, s, e, t, u in rows])
        con.commit(); con.close()
        return p
    return make


@pytest.fixture
def jhammant_db(tmp_path):
    """Factory: build a jhammant-Retrace-shaped DB with captures and activity_events."""
    def make(captures=(), events=()):
        p = tmp_path / "jhammant.db"
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE captures (id INTEGER PRIMARY KEY, captured_at DATETIME NOT NULL, app_name VARCHAR, bundle_id VARCHAR, window_title TEXT, url TEXT, doc_path TEXT, text TEXT NOT NULL, text_len INTEGER NOT NULL, text_source VARCHAR NOT NULL, caption TEXT, caption_model VARCHAR, content_hash VARCHAR, thumb_path TEXT, calendar_event TEXT, created_at DATETIME NOT NULL)")
        con.execute("CREATE TABLE activity_events (id INTEGER PRIMARY KEY, source VARCHAR NOT NULL, app VARCHAR NOT NULL, title TEXT, url TEXT, start_at DATETIME NOT NULL, end_at DATETIME, seconds FLOAT NOT NULL, day VARCHAR NOT NULL, detail JSON, created_at DATETIME NOT NULL)")
        for ts, app, bundle, title, url, text, caption in captures:
            con.execute("INSERT INTO captures (captured_at, app_name, bundle_id, window_title, url, text, text_len, text_source, caption, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (_ts(ts), app, bundle, title, url, text, len(text), "accessibility", caption, _ts(ts)))
        for source, app, title, url, start, end, secs in events:
            con.execute("INSERT INTO activity_events (source, app, title, url, start_at, end_at, seconds, day, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (source, app, title, url, _ts(start), _ts(end) if end else None, secs, start.strftime("%Y-%m-%d"), _ts(start)))
        con.commit(); con.close()
        return p
    return make
