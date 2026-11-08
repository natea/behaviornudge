"""The only path by which data leaves the Mac.

Every outbound request is built from an allowlist of fields, trimmed, scrubbed of
URLs/query strings, and written to a local audit log before it is sent.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")

TITLE_MAX = 80
CAPTION_MAX = 500
TASK_TITLE_MAX = 80

# Allowed scalar fields for classifier payloads, with their max length (None = short key)
ALLOWED_FIELDS: dict[str, int | None] = {
    "app": 60,
    "activity_key": 120,
    "path_class": 120,
    "title": TITLE_MAX,
    "caption": CAPTION_MAX,
    "prev_activity_key": 120,
    "channel": 60,
    "task_title": TASK_TITLE_MAX,
    "intent_source": 20,
}

# Field names that must never be sent, even if a caller tries.
FORBIDDEN_FIELDS = {"text", "screen_text", "url", "full_url", "html", "screenshot", "thumb_path", "page_text"}

_URL_RE = re.compile(r"(https?://\S+|www\.\S+|\S+\?\S*=\S*)", re.IGNORECASE)


class EgressViolation(ValueError):
    """A caller tried to send a field that is not allowed off the machine."""


def scrub(value: str, limit: int) -> str:
    """Remove URLs and query strings, collapse whitespace, trim."""
    value = _URL_RE.sub("", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def build_payload(fields: dict[str, Any], *, channel_only: bool = False) -> dict[str, Any]:
    """Return a payload containing only allowed, trimmed fields.

    Raises EgressViolation for forbidden or unknown fields so that new data never
    leaks by accident.
    """
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if key in FORBIDDEN_FIELDS:
            raise EgressViolation(f"field {key!r} may never leave the machine")
        if key not in ALLOWED_FIELDS:
            raise EgressViolation(f"field {key!r} is not on the egress allowlist")
        if value is None:
            continue
        if channel_only and key == "task_title":
            continue
        out[key] = scrub(str(value), ALLOWED_FIELDS[key] or 120)
    return out


def history_payload(events: list[tuple[str, datetime]]) -> list[dict[str, str]]:
    """Predictor histories: activity keys and timestamps only."""
    payload = []
    for key, ts in events:
        if _URL_RE.search(key) or "/" in key and "://" in key:
            raise EgressViolation(f"activity key looks like a URL: {key!r}")
        payload.append({"activity_key": key[:120], "ts": ts.astimezone(UTC).isoformat()})
    return payload


class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, destination: str, payload: Any) -> None:
        entry = {
            "time": datetime.now(tz=UTC).isoformat(),
            "destination": destination,
            "payload": payload,
        }
        with self.path.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line]


def send(audit: AuditLog, destination: str, payload: Any, call: Callable[[], T]) -> T:
    """Audit the payload, then perform the outbound call.

    `call` must only use `payload` (already gated) plus credentials it reads itself;
    credentials are never passed through here, so they never reach the audit log.
    """
    audit.record(destination, payload)
    return call()
