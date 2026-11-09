"""Messaging apps: which sessions count, and which conversation is open (messaging-drift D2, D4).

Conversation titles come from the browser tab (web messaging) or from the latest window title
jhammant Retrace recorded (native apps). They are normalized and reduced to a short hash here;
the rule engine keeps only those hashes, in memory, for the current messaging stretch.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from ..keys import app_short_name

LABELS = {"slack": "Slack", "discord": "Discord", "whatsapp": "WhatsApp", "signal": "Signal",
          "messages": "Messages", "telegram": "Telegram"}
DOMAIN_APPS = {"app.slack.com": "slack", "slack.com": "slack", "discord.com": "discord",
               "web.whatsapp.com": "whatsapp", "web.telegram.org": "telegram"}
RETRACE_MAX_AGE = timedelta(seconds=30)

_SUFFIX = re.compile(r"\s*[-|–—]\s*(slack|discord|whatsapp|signal|messages|telegram)\s*$", re.I)
_UNREAD = re.compile(r"\s*[-|]?\s*\d+\s+new\s+items?\b|\s*\(\d+\)|\s*\[\d+\]|^\(\d+\)\s*", re.I)


def _domain_match(domain: str | None, entry: str) -> bool:
    return bool(domain) and (domain == entry or domain.endswith("." + entry))


def messaging_app(s, apps: list[str]) -> str | None:
    """Short messaging-app name for a live session, or None if it isn't messaging."""
    if s.app in apps and s.app in LABELS:
        return s.app
    for entry in apps:
        if "." in entry and _domain_match(s.domain, entry):
            return DOMAIN_APPS.get(entry, entry.split(".")[-2])
    return None


def label(app: str | None) -> str:
    return LABELS.get(app or "", (app or "messaging").capitalize())


def normalize_title(title: str | None) -> str:
    t = (title or "").strip()
    for _ in range(3):  # suffixes and counters can be stacked: "x - 1 new item - Slack"
        t = _SUFFIX.sub("", t)
        t = _UNREAD.sub("", t).strip(" -|")
    t = re.sub(r"\s+", " ", t).strip().lower()
    return "" if t in LABELS or t in {v.lower() for v in LABELS.values()} else t


def conversation_id(title: str | None, app: str) -> str | None:
    """Short hash of the normalized title, or None if the title doesn't name a conversation."""
    t = normalize_title(title)
    if not t:
        return None
    return hashlib.sha256(f"{app}\x00{t}".encode()).hexdigest()[:12]


def retrace_window(status_path: Path, app: str, now: datetime) -> str | None:
    """Retrace's latest window title, if it was recorded for this app within the last 30 s."""
    try:
        st = json.loads(Path(status_path).read_text())
    except (OSError, ValueError):
        return None
    if app_short_name(None, st.get("last_app")) != app:
        return None
    at = st.get("last_hash_at") or st.get("last_capture_at")
    try:
        when = datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return None
    if now - when > RETRACE_MAX_AGE or when - now > RETRACE_MAX_AGE:
        return None
    return st.get("last_window")
