"""Swappable drift predictors (design D12).

Contract: ``predict(history, at, intent, current_key) -> DriftScore`` where ``history``
holds only sessions that ended at or before ``at``. Model-specific details (catalogs,
encodings, prompts, keys) stay inside each adapter. The evaluation harness only sees
this interface and the registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Protocol, Sequence

from ..timeline import parse_utc


@dataclass
class DriftScore:
    p_drift: float
    ranked_next: list[tuple[str, float]] = field(default_factory=list)
    model_id: str = ""
    latency_ms: float = 0.0
    extras: dict = field(default_factory=dict)  # adapter-specific variants (e.g. best consuming rank)


class LookAheadError(AssertionError):
    """A predictor was handed a session that ends after the decision time."""


def check_history(history: Sequence[dict], at: datetime) -> None:
    for s in history:
        if parse_utc(s["end_utc"]) > at:
            raise LookAheadError(f"session {s.get('id')} ends after decision time {at.isoformat()}")


class Predictor(Protocol):
    name: str

    def fit(self, sessions: Sequence[dict], episodes: Sequence) -> None: ...

    def predict(self, history: Sequence[dict], at: datetime, intent: dict, current_key: str) -> DriftScore: ...


Factory = Callable[..., Predictor]
REGISTRY: dict[str, Factory] = {}


def register(name: str):
    def deco(factory: Factory) -> Factory:
        REGISTRY[name] = factory
        return factory
    return deco


def create(name: str, cfg=None) -> Predictor:
    if name not in REGISTRY:
        # import built-ins lazily so optional SDKs are only needed when selected
        from . import baseline, behaviorgpt  # noqa: F401
    if name not in REGISTRY:
        raise KeyError(f"unknown predictor {name!r}; known: {sorted(REGISTRY)}")
    return REGISTRY[name](cfg)
