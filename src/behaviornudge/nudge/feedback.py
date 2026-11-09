"""Nudge feedback (design D7): apply button clicks, record outcomes, review/undo, stats."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Sequence

from ..evaluate import roc_auc
from ..intent import Intent
from ..labels import Label, LabelStore, cache_key
from .rules import is_drift, is_work
from .settings import NudgeSettings
from .store import NudgeStore

ACTIONS = {"back_to_task", "too_soon", "legit", "snooze", "opened", "dismissed"}


def nudge_intent(n: dict) -> Intent:
    return Intent(n.get("intent_source") or "none", n.get("intent_title"), None, None, n.get("intent_ref"))


class FeedbackHandler:
    def __init__(self, store: NudgeStore, settings: NudgeSettings, labels: LabelStore | None, defs_fp: str):
        self.store, self.st, self.labels, self.defs = store, settings, labels, defs_fp

    def apply(self, nudge_id: int, action: str, now: datetime) -> dict:
        n = self.store.get_nudge(nudge_id)
        if not n:
            raise KeyError(nudge_id)
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        self.store.update_nudge(nudge_id, action=action, action_time=now.isoformat())
        effect: dict = {"action": action}
        site, src = n.get("site"), n.get("intent_source")
        if action == "too_soon":
            m = min(self.st.too_soon_cap, self.store.multiplier(n["type"], site, src) * self.st.too_soon_factor)
            self.store.set_multiplier(n["type"], site, src, m)
            effect["multiplier"] = m
        elif action in ("back_to_task", "opened"):
            m = self.store.multiplier(n["type"], site, src)
            if m != 1.0:
                m = m + (1.0 - m) * 0.1
                self.store.set_multiplier(n["type"], site, src, m)
            effect["multiplier"] = m
        elif action == "legit":
            intent = nudge_intent(n)
            ref = intent.ref or intent.title or ""
            ck = None
            if self.labels is not None and n.get("activity_key"):
                ck = cache_key({"key": n["activity_key"], "title": n.get("title")}, intent, self.defs)
                self.labels.put(ck, Label(alignment="on-task", sources={"alignment": "user"}))
                self.labels.commit()
            self.store.add_suppression(n["activity_key"], ref, intent.title, ck)
            effect["suppressed"] = (n["activity_key"], ref)
        elif action == "snooze":
            until = now + timedelta(minutes=self.st.snooze_min)
            self.store.set_state("snooze_until", until.isoformat())
            effect["snooze_until"] = until.isoformat()
        return effect

    # --- review / undo ------------------------------------------------------------
    def list(self) -> dict:
        return {"adjustments": self.store.adjustments(), "suppressions": self.store.suppressions()}

    def reset_suppression(self, sid: int) -> dict | None:
        row = self.store.delete_suppression(sid)
        if row and row.get("label_ck") and self.labels is not None:
            self.labels.delete(row["label_ck"])
        return row

    def reset_adjustment(self, rowid: int) -> bool:
        return self.store.delete_adjustment(rowid)


def outcome(sessions: Sequence, start: datetime, end: datetime, intent: Intent) -> str:
    """What happened after a nudge: 'returned' (a >=60 s work session began), 'kept_consuming',
    or 'other' work/activity."""
    after = [s for s in sessions if s.end > start and s.start < end]
    for s in after:
        if s.start >= start and is_work(s, intent):
            return "returned"
    drift = sum((min(s.end, end) - max(s.start, start)).total_seconds() for s in after if is_drift(s, intent))
    total = sum((min(s.end, end) - max(s.start, start)).total_seconds() for s in after) or 1
    return "kept_consuming" if drift / total >= 0.5 else "other"


def record_outcomes(store: NudgeStore, sessions: Sequence, now: datetime) -> int:
    """Fill outcome_5 / outcome_15 for fired nudges whose windows have passed."""
    done = 0
    for n in store.nudges(since=now - timedelta(hours=4), fired_only=True):
        t = datetime.fromisoformat(n["time"])
        intent = nudge_intent(n)
        for field, mins in (("outcome_5", 5), ("outcome_15", 15)):
            if n[field] is None and now >= t + timedelta(minutes=mins):
                store.update_nudge(n["id"], **{field: outcome(sessions, t, t + timedelta(minutes=mins), intent)})
                done += 1
    return done


def stats(store: NudgeStore, since: datetime | None = None) -> dict:
    rows = store.nudges(since=since)
    fired = [r for r in rows if r["fired"]]
    by_type: dict = defaultdict(lambda: {"fired": 0, "suppressed": 0, "actions": Counter(), "returned_5": 0, "with_outcome_5": 0})
    for r in rows:
        b = by_type[r["type"]]
        if r["fired"]:
            b["fired"] += 1
            b["actions"][r["action"] or "ignored"] += 1
            if r["outcome_5"]:
                b["with_outcome_5"] += 1
                b["returned_5"] += r["outcome_5"] == "returned"
        else:
            b["suppressed"] += 1
    scored = [r for r in fired if r["shadow_score"] is not None and r["outcome_15"]]
    auc = roc_auc([r["shadow_score"] for r in scored], [int(r["outcome_15"] == "kept_consuming") for r in scored])

    def finish(b):
        rate = b["returned_5"] / b["with_outcome_5"] if b["with_outcome_5"] else None
        return {"fired": b["fired"], "suppressed": b["suppressed"], "actions": dict(b["actions"]),
                "return_within_5_min": round(rate, 3) if rate is not None else None}

    total = {"fired": 0, "suppressed": 0, "actions": Counter(), "returned_5": 0, "with_outcome_5": 0}
    for b in by_type.values():
        for k in ("fired", "suppressed", "returned_5", "with_outcome_5"):
            total[k] += b[k]
        total["actions"].update(b["actions"])
    return {"by_type": {t: finish(b) for t, b in sorted(by_type.items())}, "overall": finish(total),
            "shadow_roc_auc": round(auc, 3) if auc is not None else None, "shadow_scored": len(scored)}
