"""Layered activity labeling: rules -> cache -> classifier -> user review.

Labels per session: mode (creative / reactive / consuming), purpose (lookup / reply /
follow-link / browse, or n/a for non-web work), project (Sunsama channel or none) and
alignment with intent (on-task / supporting / off-task / no-intent).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .intent import Intent

MODES = ("creative", "reactive", "consuming")
PURPOSES = ("lookup", "reply", "follow-link", "browse", "n/a")
ALIGNMENTS = ("on-task", "supporting", "off-task", "no-intent")

# ---------------------------------------------------------------------------
# Rule table (5.1)
# ---------------------------------------------------------------------------

CREATIVE_APPS = {"ghostty", "zed", "terminal", "iterm", "vscode", "cursor", "xcode", "obsidian", "bambu-studio"}
REACTIVE_APPS = {"superhuman", "mail", "slack", "whatsapp", "messages", "signal", "discord", "zoom", "calendar", "sunsama"}
CREATIVE_KEYS = {"localhost", "file"}
REACTIVE_KEYS = {
    "linkedin.com/messaging": "reply", "linkedin.com/search": "lookup", "linkedin.com/profile": "lookup",
    "linkedin.com/notifications": "browse", "x.com/messages": "reply", "x.com/notifications": "browse",
    "instagram.com/direct": "reply", "calendar.google.com": "n/a", "meet.google.com": "n/a", "mail.google.com": "reply",
}
CONSUMING_KEYS = {
    "linkedin.com/feed": "browse", "x.com/home": "browse", "x.com/search": "browse", "instagram.com/feed": "browse",
    "instagram.com/reels": "browse", "instagram.com/explore": "browse", "instagram.com/stories": "browse",
    "youtube.com/home": "browse", "youtube.com/shorts": "browse", "youtube.com/feed": "browse",
    "facebook.com": "browse", "reddit.com": "browse", "news.ycombinator.com": "browse",
}
# Purposeful single-item visits on drift-prone sites (mode left to later layers when ambiguous)
FOLLOW_LINK_KEYS = {"x.com/status", "linkedin.com/post", "instagram.com/post"}
LOOKUP_KEYS = {"x.com/profile", "instagram.com/profile", "linkedin.com/company"}


@dataclass
class Label:
    mode: str | None = None
    purpose: str | None = None
    project: str | None = None
    alignment: str | None = None
    sources: dict = field(default_factory=dict)  # dimension -> rule/cache/model/user
    confidence: dict = field(default_factory=dict)  # dimension -> float (model labels)
    model: str | None = None
    prompt_fp: str | None = None

    def set(self, dim: str, value, source: str, conf: float | None = None):
        if value is None or getattr(self, dim) is not None:
            return
        setattr(self, dim, value)
        self.sources[dim] = source
        if conf is not None:
            self.confidence[dim] = conf

    def missing(self) -> list[str]:
        return [d for d in ("mode", "purpose", "project", "alignment") if getattr(self, d) is None]


DIMS = ("mode", "purpose", "project", "alignment")


def overlay_user(rule: Label, *stored: Label | None) -> Label:
    """User labels first, then the rule's labels: dimensions the user answered (review queue
    or "Legit task" feedback) override rules; everything else keeps the rule's value."""
    out = Label()
    for lab in stored:
        if not lab:
            continue
        for d in DIMS:
            if lab.sources.get(d) == "user":
                out.set(d, getattr(lab, d), "user")
    for d in DIMS:
        out.set(d, getattr(rule, d), rule.sources.get(d, "rule"), rule.confidence.get(d))
    return out


def rule_label(s: dict, channel_map: dict[str, str]) -> Label:
    """Intent-independent rule labels (mode, purpose, project) for a session row."""
    key, app = s["key"], s["app"]
    lab = Label()
    if app in CREATIVE_APPS or key.split(":")[0] in CREATIVE_APPS or key in CREATIVE_KEYS:
        lab.set("mode", "creative", "rule")
        lab.set("purpose", "n/a", "rule")
    elif app in REACTIVE_APPS:
        lab.set("mode", "reactive", "rule")
        lab.set("purpose", "reply" if app not in ("zoom", "calendar", "sunsama") else "n/a", "rule")
    elif key in REACTIVE_KEYS:
        lab.set("mode", "reactive", "rule")
        lab.set("purpose", REACTIVE_KEYS[key], "rule")
    elif key in CONSUMING_KEYS:
        lab.set("mode", "consuming", "rule")
        lab.set("purpose", CONSUMING_KEYS[key], "rule")
    elif key in FOLLOW_LINK_KEYS:
        # reading a single post is content consumption; the visit itself is purposeful
        lab.set("mode", "consuming", "rule")
        lab.set("purpose", "follow-link", "rule")
    elif key in LOOKUP_KEYS:
        lab.set("purpose", "lookup", "rule")
        lab.set("mode", "reactive", "rule")
    if s.get("project"):
        lab.set("project", channel_map.get(s["project"], s["project"]), "rule")
    elif lab.mode in ("consuming",):
        lab.set("project", "none", "rule")
    return lab


# ---------------------------------------------------------------------------
# Alignment (5.3)
# ---------------------------------------------------------------------------

SITE_WORDS = {
    "linkedin.com": re.compile(r"\b(LI|linkedin)\b", re.I),
    "x.com": re.compile(r"\b(X|twitter|tweet|tweets)\b"),
    "instagram.com": re.compile(r"\b(IG|instagram|insta)\b", re.I),
    "youtube.com": re.compile(r"\b(youtube|YT|video)\b", re.I),
}
COMMS_WORDS = re.compile(r"\b(comms|inbox|email|e-mail|reply|respond|message|messages|slack|call|sync|meeting|1:1|re)\b", re.I)


def rule_alignment(s: dict, lab: Label, intent: Intent) -> str | None:
    if not intent.known:
        return "no-intent"
    title = intent.title or ""
    site = s.get("domain")
    if site and site in SITE_WORDS and SITE_WORDS[site].search(title):
        return "on-task"  # e.g. intended "Alex re LI post" while on LinkedIn
    sess_proj = (lab.project if lab.project not in (None, "none") else None)
    if sess_proj and intent.project:
        return "on-task" if sess_proj.lower() == intent.project.lower() else "off-task"
    if sess_proj and title and len(sess_proj) >= 4 and sess_proj.lower() in title.lower():
        return "on-task"
    if lab.mode == "consuming":
        return "off-task"
    if lab.mode == "creative" and intent.source == "recent-creative":
        return "on-task"
    if lab.mode == "reactive" and COMMS_WORDS.search(title):
        return "on-task"
    return None  # undecided by rules -> later layers


# ---------------------------------------------------------------------------
# Cache and review queue (5.2)
# ---------------------------------------------------------------------------

def trim_title(title: str | None) -> str:
    return re.sub(r"\s+", " ", (title or "")).strip()[:80]


def definitions_fp(modes: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(modes, sort_keys=True).encode()).hexdigest()[:12]


def cache_key(s: dict, intent: Intent, defs_fp: str) -> str:
    raw = json.dumps([s["key"], trim_title(s.get("title")), intent.source, intent.ref or intent.title or "", defs_fp])
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


class LabelStore:
    """Local SQLite store for cached labels, the review queue and user answers."""

    def __init__(self, path: Path):
        import threading
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # shared by the live engine's threads (helper events, Jev workers, main loop)
        self._lock = threading.RLock()
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.executescript("""
        CREATE TABLE IF NOT EXISTS cache (ck TEXT PRIMARY KEY, label TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS review (
            id INTEGER PRIMARY KEY, ck TEXT UNIQUE, key TEXT, title TEXT, intent TEXT, reason TEXT,
            proposed TEXT, status TEXT DEFAULT 'open', kind TEXT DEFAULT 'queue');
        """)

    def get(self, ck: str) -> Label | None:
        with self._lock:
            row = self.con.execute("SELECT label FROM cache WHERE ck=?", (ck,)).fetchone()
            return Label(**json.loads(row[0])) if row else None

    def put(self, ck: str, lab: Label) -> None:
        with self._lock:
            self.con.execute("INSERT OR REPLACE INTO cache VALUES (?,?)", (ck, json.dumps(asdict(lab))))

    def enqueue(self, ck: str, s: dict, intent: Intent, reason: str, proposed: Label, kind: str = "queue") -> None:
        with self._lock:
            self.con.execute(
                "INSERT OR IGNORE INTO review (ck, key, title, intent, reason, proposed, kind) VALUES (?,?,?,?,?,?,?)",
                (ck, s["key"], trim_title(s.get("title")), json.dumps(asdict(intent)), reason, json.dumps(asdict(proposed)), kind))

    def open_items(self, kind: str = "queue", limit: int = 50) -> list[dict]:
        with self._lock:
            cur = self.con.execute("SELECT id, key, title, intent, reason, proposed FROM review WHERE status='open' AND kind=? ORDER BY id LIMIT ?", (kind, limit))
            return [dict(zip(("id", "key", "title", "intent", "reason", "proposed"), r)) for r in cur]

    def answer(self, item_id: int, **dims) -> Label:
        with self._lock:
            row = self.con.execute("SELECT ck, proposed FROM review WHERE id=?", (item_id,)).fetchone()
            if not row:
                raise KeyError(item_id)
            ck, proposed = row
            lab = Label(**json.loads(proposed))
            for dim, val in dims.items():
                if val is None:
                    continue
                setattr(lab, dim, val)
                lab.sources[dim] = "user"
                lab.confidence.pop(dim, None)
            self.put(ck, lab)
            self.con.execute("UPDATE review SET status='answered' WHERE id=?", (item_id,))
            self.con.commit()
            return lab

    def delete(self, ck: str) -> bool:
        with self._lock:
            n = self.con.execute("DELETE FROM cache WHERE ck=?", (ck,)).rowcount
            self.con.commit()
            return n > 0

    def commit(self):
        with self._lock:
            self.con.commit()

    def close(self):
        with self._lock:
            self.con.commit()
            self.con.close()
