"""TypeSafe Jev labeling layer (task group 10).

Judgments from Jev, decisions in code: Jev answers typed questions about one *activity*
(app, page, trimmed title, caption). Mode, purpose and project are judged once per unique
activity and cached; alignment with intent is then decided in code (labels.rule_alignment).

All question wording lives in QUESTIONS below so it can be audited and edited in one place.
Routing uses the probability of the chosen option (not `confidence`, which measures how
concentrated the distribution is, not correctness).
"""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .config import Config, get_secret
from .egress import AuditLog, build_payload, send
from .labels import Label, trim_title

# ---------------------------------------------------------------------------
# Question wording (edit here)
# ---------------------------------------------------------------------------

PURPOSE_OPTIONS = {
    "lookup": "Looking up one specific person, company, page or fact, such as a profile or a search",
    "reply": "Reading or writing a message in a conversation, such as a direct message, email or chat",
    "follow_link": "Opening one specific post, video or article, such as a link someone shared",
    "browse": "Scrolling a feed or browsing without a specific target",
    "own_work": "Working in a tool on the person's own project, such as code, a document or a design",
}
PURPOSE_TO_LABEL = {"lookup": "lookup", "reply": "reply", "follow_link": "follow-link", "browse": "browse", "own_work": "n/a"}


def questions(mode_defs: dict[str, str], channels: list[str]) -> dict[str, Any]:
    from typesafe_sdk import Choice, Noul, NoulCriteria

    return {
        "mode": Choice(
            instructions="What kind of activity is the person doing in `activity_key`, judging from `title` and `caption`?",
            criteria=dict(mode_defs),
        ),
        "purpose": Choice(
            instructions="What is the person doing on `activity_key`, judging from `title` and `caption`?",
            criteria=dict(PURPOSE_OPTIONS),
        ),
        "project": Choice(
            instructions="Which area of the person's life or work is `title` about?",
            criteria={**{c: None for c in channels}, "none": "Not about any of the other areas"},
        ),
        "fits": Noul(
            instructions="Is the person actively using `activity_key` for something (reading, writing, building, watching or messaging)?",
            criteria=NoulCriteria(
                true="The title or caption shows content the person is using, such as a page, a document, a chat or a video",
                false="The screen is idle, locked, a login prompt, a password manager or a system dialog",
            ),
        ),
    }


def questions_fp(mode_defs: dict[str, str], channels: list[str], model: str) -> str:
    raw = json.dumps({"modes": mode_defs, "purpose": PURPOSE_OPTIONS, "channels": channels, "model": model,
                      "v": "q1"}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def activity_cache_key(key: str, title: str | None, fp: str) -> str:
    return "act|" + hashlib.sha256(json.dumps([key, trim_title(title), fp]).encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Routing (decisions in code)
# ---------------------------------------------------------------------------

@dataclass
class Routing:
    accept: float = 0.9
    review: float = 0.5
    fits_min: float = 0.3


def route(mode_p: float, fits: float, r: Routing) -> str | None:
    """None = accept silently; 'sample' = accept and include in the review sample; 'queue' = ask the user."""
    if fits < r.fits_min or mode_p < r.review:
        return "queue"
    if mode_p < r.accept:
        return "sample"
    return None


def to_label(answers: dict, model: str, fp: str) -> tuple[Label, float, float]:
    """Convert raw answers {name: {choice, probabilities} | {noul}} into a Label."""
    lab = Label(model=model, prompt_fp=fp)
    m = answers["mode"]
    mode_p = float(m["probabilities"][m["choice"]])
    lab.set("mode", m["choice"], "model", mode_p)
    pu = answers["purpose"]
    lab.set("purpose", PURPOSE_TO_LABEL[pu["choice"]], "model", float(pu["probabilities"][pu["choice"]]))
    pr = answers["project"]
    lab.set("project", pr["choice"], "model", float(pr["probabilities"][pr["choice"]]))
    fits = float(answers["fits"]["noul"])
    return lab, mode_p, fits


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

Ask = Callable[[dict, dict], dict]  # (state, questions) -> {"model": str, "answers": {...}}


class JevLabeler:
    def __init__(self, cfg: Config, channels: list[str], ask: Ask | None = None):
        lc = cfg.section("labeling")
        self.model = lc.get("classifier_model", "jev-1.14.0")
        self.mode_defs = lc.get("modes", {})
        self.channel_only = lc.get("channel_only", False)
        self.channels = channels
        self.routing = Routing(lc.get("accept_probability", 0.9), lc.get("review_probability", 0.5), lc.get("fits_min", 0.3))
        self.fp = questions_fp(self.mode_defs, channels, self.model)
        self.audit = AuditLog(cfg.data_dir / "audit.jsonl")
        self._ask = ask or self._sdk_ask
        self._client = None

    def _sdk_ask(self, state: dict, qs: dict) -> dict:
        from typesafe_sdk import TypeSafeClient

        if self._client is None:
            os.environ.setdefault("TYPESAFE_API_KEY", get_secret("TYPESAFE_API_KEY"))
            self._client = TypeSafeClient(model=self.model)
        resp = self._client.system_one(state=state, questions=qs)
        answers = {}
        for name, a in resp.answers.items():
            d = a.model_dump() if hasattr(a, "model_dump") else dict(a)
            answers[name] = d
        return {"model": getattr(resp, "model", self.model), "answers": answers}

    def state_for(self, s: dict, prev_key: str | None) -> dict:
        return build_payload({
            "app": s.get("app"), "activity_key": s["key"], "path_class": s.get("path_class"),
            "title": s.get("title"), "caption": s.get("caption"), "prev_activity_key": prev_key,
        }, channel_only=self.channel_only)

    def label_one(self, s: dict, prev_key: str | None) -> tuple[Label, str | None]:
        state = self.state_for(s, prev_key)
        qs = questions(self.mode_defs, self.channels)
        out = send(self.audit, "typesafe:/v1/systemone", {"model": self.model, "state": state, "questions": list(qs)},
                   lambda: self._ask(state, qs))
        lab, mode_p, fits = to_label(out["answers"], out.get("model", self.model), self.fp)
        lab.model = out.get("model", self.model)
        return lab, route(mode_p, fits, self.routing)

    def label_many(self, items: Iterable[tuple[str, dict, str | None]], workers: int = 8) -> list[tuple[str, Label | None, str | None, str | None]]:
        """items: (cache_key, session, prev_key). Returns (cache_key, label, review, error)."""

        def one(it):
            ck, s, prev = it
            try:
                lab, rv = self.label_one(s, prev)
                return ck, lab, rv, None
            except Exception as e:  # recorded; session stays unlabeled
                return ck, None, None, f"{type(e).__name__}: {str(e)[:200]}"

        with ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(one, list(items)))
