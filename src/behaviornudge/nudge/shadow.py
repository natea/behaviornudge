"""Shadow predictions (design D9): score every nudge moment with the configured predictor,
log the score, never let it delay or decide a nudge."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime
from typing import Callable

from ..predictors import Predictor
from .store import NudgeStore


class ShadowScorer:
    def __init__(self, store: NudgeStore, predictor: Predictor | None, fallback: Predictor | None = None,
                 timeout_s: float = 3.0, log: Callable[[str], None] = lambda m: None):
        self.store = store
        self.predictor = predictor
        self.fallback = fallback
        self.timeout = timeout_s
        self.log = log
        self._jobs = ThreadPoolExecutor(max_workers=2, thread_name_prefix="shadow")
        self._calls = ThreadPoolExecutor(max_workers=2, thread_name_prefix="shadow-call")

    def submit(self, nudge_id: int, history: list[dict], at: datetime, intent: dict, current_key: str):
        """Returns immediately; the score lands in the nudge store when ready."""
        return self._jobs.submit(self._score, nudge_id, history, at, intent, current_key)

    def _score(self, nudge_id, history, at, intent, current_key) -> None:
        error = None
        if self.predictor is not None:
            fut = self._calls.submit(self.predictor.predict, history, at, intent, current_key)
            try:
                out = fut.result(timeout=self.timeout)
                self.store.update_nudge(nudge_id, shadow_score=float(out.p_drift), shadow_model=out.model_id)
                return
            except FutureTimeout:
                error = f"timeout after {self.timeout:.0f}s"
            except Exception as e:
                error = f"{type(e).__name__}: {str(e)[:150]}"
            self.log(f"shadow predictor failed for nudge {nudge_id}: {error}")
        if self.fallback is not None:
            try:
                out = self.fallback.predict(history, at, intent, current_key)
                self.store.update_nudge(nudge_id, shadow_score=float(out.p_drift), shadow_model=out.model_id,
                                        shadow_error=error)
                return
            except Exception as e:
                error = f"{error}; fallback {type(e).__name__}: {e}" if error else f"fallback {e}"
        self.store.update_nudge(nudge_id, shadow_error=error or "no predictor")

    def shutdown(self) -> None:
        self._jobs.shutdown(wait=False, cancel_futures=True)
        self._calls.shutdown(wait=False, cancel_futures=True)
