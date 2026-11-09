"""Nudge store (data/nudges.sqlite): every nudge moment, feedback adjustments,
legit-task suppressions, and engine state (enabled, snooze)."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS nudges (
  id INTEGER PRIMARY KEY, time TEXT NOT NULL, type TEXT NOT NULL, activity_key TEXT, title TEXT,
  site TEXT, intent_title TEXT, intent_source TEXT, intent_ref TEXT,
  fired INTEGER NOT NULL, reason TEXT, shadow_score REAL, shadow_model TEXT, shadow_error TEXT,
  action TEXT, action_time TEXT, outcome_5 TEXT, outcome_15 TEXT, context TEXT
);
CREATE INDEX IF NOT EXISTS ix_nudges_time ON nudges(time);
CREATE TABLE IF NOT EXISTS adjustments (
  type TEXT NOT NULL, site TEXT NOT NULL, intent_source TEXT NOT NULL,
  multiplier REAL NOT NULL, updated TEXT NOT NULL, PRIMARY KEY (type, site, intent_source)
);
CREATE TABLE IF NOT EXISTS suppressions (
  id INTEGER PRIMARY KEY, activity_key TEXT NOT NULL, intent_ref TEXT NOT NULL, intent_title TEXT,
  created TEXT NOT NULL, label_ck TEXT, UNIQUE (activity_key, intent_ref)
);
CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


class NudgeStore:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)

    # --- nudges ---------------------------------------------------------------
    def add_nudge(self, *, time: datetime, type: str, fired: bool, reason: str | None = None,
                  activity_key: str | None = None, title: str | None = None, site: str | None = None,
                  intent_title: str | None = None, intent_source: str | None = None,
                  intent_ref: str | None = None, context: dict | None = None) -> int:
        with self._lock:
            cur = self.con.execute(
                "INSERT INTO nudges (time, type, activity_key, title, site, intent_title, intent_source, intent_ref, fired, reason, context)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (time.isoformat(), type, activity_key, title, site, intent_title, intent_source, intent_ref,
                 int(fired), reason, json.dumps(context or {})))
            self.con.commit()
            return cur.lastrowid

    def update_nudge(self, nudge_id: int, **fields: Any) -> None:
        allowed = {"shadow_score", "shadow_model", "shadow_error", "action", "action_time", "outcome_5", "outcome_15"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot update {bad}")
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self.con.execute(f"UPDATE nudges SET {cols} WHERE id=?", (*fields.values(), nudge_id))
            self.con.commit()

    def get_nudge(self, nudge_id: int) -> dict | None:
        r = self.con.execute("SELECT * FROM nudges WHERE id=?", (nudge_id,)).fetchone()
        return dict(r) if r else None

    def nudges(self, since: datetime | None = None, fired_only: bool = False) -> list[dict]:
        q, args = "SELECT * FROM nudges WHERE 1=1", []
        if since:
            q += " AND time >= ?"
            args.append(since.isoformat())
        if fired_only:
            q += " AND fired = 1"
        return [dict(r) for r in self.con.execute(q + " ORDER BY id", args)]

    def fired_count_since(self, since: datetime) -> int:
        return self.con.execute("SELECT count(*) FROM nudges WHERE fired=1 AND time >= ?", (since.isoformat(),)).fetchone()[0]

    def last_fired(self, type: str, site: str | None) -> datetime | None:
        r = self.con.execute("SELECT max(time) FROM nudges WHERE fired=1 AND type=? AND coalesce(site,'')=coalesce(?, '')",
                             (type, site)).fetchone()[0]
        return datetime.fromisoformat(r) if r else None

    # --- adjustments --------------------------------------------------------------
    def multiplier(self, type: str, site: str | None, intent_source: str | None) -> float:
        r = self.con.execute("SELECT multiplier FROM adjustments WHERE type=? AND site=? AND intent_source=?",
                             (type, site or "", intent_source or "")).fetchone()
        return float(r[0]) if r else 1.0

    def set_multiplier(self, type: str, site: str | None, intent_source: str | None, m: float) -> None:
        with self._lock:
            self.con.execute("INSERT OR REPLACE INTO adjustments VALUES (?,?,?,?,?)",
                             (type, site or "", intent_source or "", m, now_utc().isoformat()))
            self.con.commit()

    def adjustments(self) -> list[dict]:
        return [dict(r) for r in self.con.execute("SELECT rowid AS id, * FROM adjustments ORDER BY updated")]

    def delete_adjustment(self, rowid: int) -> bool:
        with self._lock:
            n = self.con.execute("DELETE FROM adjustments WHERE rowid=?", (rowid,)).rowcount
            self.con.commit()
            return n > 0

    # --- suppressions ---------------------------------------------------------------
    def add_suppression(self, activity_key: str, intent_ref: str, intent_title: str | None, label_ck: str | None) -> None:
        with self._lock:
            self.con.execute("INSERT OR REPLACE INTO suppressions (activity_key, intent_ref, intent_title, created, label_ck) VALUES (?,?,?,?,?)",
                             (activity_key, intent_ref, intent_title, now_utc().isoformat(), label_ck))
            self.con.commit()

    def is_suppressed(self, activity_key: str, intent_ref: str | None) -> bool:
        if not intent_ref:
            return False
        return self.con.execute("SELECT 1 FROM suppressions WHERE activity_key=? AND intent_ref=?",
                                (activity_key, intent_ref)).fetchone() is not None

    def suppressions(self) -> list[dict]:
        return [dict(r) for r in self.con.execute("SELECT * FROM suppressions ORDER BY id")]

    def delete_suppression(self, sid: int) -> dict | None:
        with self._lock:
            r = self.con.execute("SELECT * FROM suppressions WHERE id=?", (sid,)).fetchone()
            if r:
                self.con.execute("DELETE FROM suppressions WHERE id=?", (sid,))
                self.con.commit()
            return dict(r) if r else None

    # --- state ------------------------------------------------------------------
    def get_state(self, k: str, default: Any = None) -> Any:
        r = self.con.execute("SELECT v FROM state WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else default

    def set_state(self, k: str, v: Any) -> None:
        with self._lock:
            self.con.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (k, json.dumps(v, default=str)))
            self.con.commit()

    def close(self) -> None:
        self.con.close()
