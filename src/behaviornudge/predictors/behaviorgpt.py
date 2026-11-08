"""BehaviorGPT adapter (design D7): activity catalog + raw-API ranking with real timestamps.

Everything Unbox-specific lives here: the catalog, the View/Order encoding, the API key,
and the transport. The SDK's `complete()` re-stamps history events near "now", so this
adapter builds requests with the SDK's public models but keeps each session's real
timestamp, and posts them to `/vector/recommendations` itself (register_event=False).
"""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Sequence

import httpx

from ..config import Config, get_secret
from ..egress import AuditLog, send
from ..timeline import parse_utc
from . import DriftScore, check_history, register

API_PATH = "/vector/recommendations"
DEFAULT_BASE = "https://behaviorgpt-northeurope.api.unboxai.com/v1"
OTHER_WEB, OTHER_APP = "other-web", "other-app"
BLOCK_GAP = timedelta(minutes=30)

# ---------------------------------------------------------------------------
# Catalog (8.1)
# ---------------------------------------------------------------------------

CATALOG_COLUMNS = ["id", "name", "brand", "categories", "image_url", "event_type", "group", "sales_since",
                   "timestamp", "frequency", "market", "price", "currency", "search_keywords", "keywords"]

PATH_LABELS = {"feed": "feed", "home": "home feed", "messaging": "messages", "messages": "messages", "profile": "profile",
               "search": "search", "status": "single post", "post": "single post", "reels": "reels", "watch": "video",
               "shorts": "shorts", "repo": "code repository", "notifications": "notifications"}


def human_name(key: str) -> str:
    if key in (OTHER_WEB, OTHER_APP):
        return "other website" if key == OTHER_WEB else "other app"
    if ":" in key and "/" not in key:
        app, proj = key.split(":", 1)
        return f"{app} on project {proj}"
    if "/" in key:
        dom, pc = key.split("/", 1)
        return f"{dom} {PATH_LABELS.get(pc, pc)}"
    return key


def catalog_keys(sessions: Sequence[dict], min_sessions: int = 5) -> dict[str, str]:
    """Map every activity key to its catalog id (itself, or an other-* bucket)."""
    counts = Counter(s["key"] for s in sessions)
    out = {}
    for key, n in counts.items():
        if n >= min_sessions:
            out[key] = key
        else:
            out[key] = OTHER_WEB if ("." in key.split("/")[0] and ":" not in key) else OTHER_APP
    return out


def build_catalog_rows(sessions: Sequence[dict], modes: dict[str, str], prone: Sequence[str], min_sessions: int = 5) -> list[dict]:
    """One catalog row per kept key (plus the two buckets). `modes` maps key -> majority mode."""
    mapping = catalog_keys(sessions, min_sessions)
    freq = Counter(mapping[s["key"]] for s in sessions)
    rows = []
    for cid in sorted(set(mapping.values()) | {OTHER_WEB, OTHER_APP}):
        dom = cid.split("/")[0].split(":")[0]
        mode = modes.get(cid) or "unknown"
        drift_prone = any(dom == p or dom.endswith("." + p) for p in prone)
        rows.append({
            "id": cid, "name": human_name(cid), "brand": dom,
            "categories": " > ".join([mode, "drift-prone" if drift_prone else "regular"]),
            "image_url": None, "event_type": "product", "group": "product", "sales_since": None,
            "timestamp": None, "frequency": int(freq.get(cid, 0)), "market": None, "price": None, "currency": None,
            "search_keywords": None, "keywords": [mode, dom] + (["drift-prone"] if drift_prone else []),
        })
    return rows


def write_catalog_parquet(rows: list[dict], path: Path) -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema([
        ("id", pa.string()), ("name", pa.string()), ("brand", pa.string()), ("categories", pa.string()),
        ("image_url", pa.string()), ("event_type", pa.string()), ("group", pa.string()),
        ("sales_since", pa.list_(pa.int64())), ("timestamp", pa.int64()), ("frequency", pa.int64()),
        ("market", pa.string()), ("price", pa.string()), ("currency", pa.string()),
        ("search_keywords", pa.list_(pa.string())), ("keywords", pa.list_(pa.string())),
    ])
    table = pa.Table.from_pylist(rows, schema=schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
    return path


def check_catalog_columns(path: Path) -> list[str]:
    import pyarrow.parquet as pq

    names = pq.read_schema(path).names
    return [c for c in CATALOG_COLUMNS if c not in names]


# ---------------------------------------------------------------------------
# Request building (8.2)
# ---------------------------------------------------------------------------

def history_events(history: Sequence[dict], mapping: dict[str, str], order_min: float) -> list[tuple[str, datetime, str, str]]:
    """Sessions -> (kind, timestamp, catalog id, block id). Long sessions add an Order (commitment)."""
    out = []
    block = 0
    prev_end = None
    for s in history:
        start, end = parse_utc(s["start_utc"]), parse_utc(s["end_utc"])
        if prev_end and start - prev_end > BLOCK_GAP:
            block += 1
        cid = mapping.get(s["key"], OTHER_WEB if "." in s["key"] else OTHER_APP)
        out.append(("view", start, cid, f"b{block}"))
        if s["seconds"] >= order_min * 60:
            out.append(("order", end, cid, f"b{block}"))
        prev_end = end
    out.sort(key=lambda e: e[1])
    return out


def build_request(events, at: datetime, catalog_id: str, tz: str, market: str, limit: int) -> dict:
    """JSON body for /vector/recommendations, serialized through the SDK's own models."""
    from behaviorgpt.types import Domains, Order, SessionEvent, UnboxAIRequest, View

    domains = Domains(market=market)
    hist = []
    for kind, ts, cid, block in events:
        ev = View(cid) if kind == "view" else Order([cid])
        hist.append(SessionEvent(store_id=catalog_id, domains=domains, timezone=tz, timestamp=ts, event=ev, session_id=block))
    req = UnboxAIRequest(history=hist, store_id=catalog_id, domains=domains, timezone=tz, limit=limit,
                         register_event=False, timestamp=at)
    return req.model_dump(mode="json", exclude_none=True)


# ---------------------------------------------------------------------------
# Predictor
# ---------------------------------------------------------------------------

Transport = Callable[[dict], dict]


class BehaviorGPTPredictor:
    name = "behaviorgpt"

    def __init__(self, cfg: Config | None = None, transport: Transport | None = None, catalog: dict | None = None):
        p = cfg.section("predictors") if cfg else {}
        self.tz = cfg.timezone if cfg else "America/New_York"
        self.market = p.get("behaviorgpt_market", "us")
        self.k = p.get("behaviorgpt_top_k", 20)
        self.n_hist = p.get("behaviorgpt_history_sessions", 50)
        self.order_min = p.get("behaviorgpt_order_min", 10)
        self.base = p.get("behaviorgpt_base_url", DEFAULT_BASE)
        self.timeout = p.get("behaviorgpt_timeout_s", 30)
        self.audit = AuditLog((cfg.data_dir if cfg else Path("data")) / "audit.jsonl")
        state = catalog or (_load_catalog_state(cfg) if cfg else None)
        if not state:
            raise RuntimeError("no BehaviorGPT catalog yet: run `behaviornudge catalog upload` first")
        self.catalog_id = state["catalog_id"]
        self.mapping: dict[str, str] = state["mapping"]
        self.modes: dict[str, str] = state["modes"]
        self._transport = transport or self._http

    def _http(self, body: dict) -> dict:
        key = get_secret("UNBOXAI_API_KEY")
        r = httpx.post(self.base + API_PATH, json=body, headers={"x-api-key": key, "x-catalog-id": self.catalog_id}, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"BehaviorGPT {r.status_code}: {r.text[:300]}")
        return r.json()

    def fit(self, sessions, episodes) -> None:  # zero-shot
        return None

    def predict(self, history: Sequence[dict], at: datetime, intent: dict, current_key: str) -> DriftScore:
        t0 = time.perf_counter()
        check_history(history, at)
        hist = list(history)[-self.n_hist:]
        events = history_events(hist, self.mapping, self.order_min)
        cur = self.mapping.get(current_key, OTHER_WEB if "." in current_key else OTHER_APP)
        block = events[-1][3] if events else "b0"
        events.append(("view", at, cur, block))  # the visit that just started
        body = build_request(events, at, self.catalog_id, self.tz, self.market, self.k)
        audited = {"catalog_id": self.catalog_id, "at": at.isoformat(), "timezone": self.tz,
                   "history": [{"kind": k, "activity_key": c, "ts": t.isoformat()} for k, t, c, _ in events]}
        data = send(self.audit, "unbox:/vector/recommendations", audited, lambda: self._transport(body))
        items = _items(data)
        ranked = [(i["id"], float(i.get("score", 0.0))) for i in items]
        p_sum, best_rank = drift_scores(ranked, self.modes)
        return DriftScore(p_drift=p_sum, ranked_next=ranked[:10], model_id=f"behaviorgpt:{self.catalog_id}",
                          latency_ms=(time.perf_counter() - t0) * 1000,
                          extras={"best_consuming_rank": best_rank, "rank_score": (1.0 / best_rank) if best_rank else 0.0})


def _items(data: dict) -> list[dict]:
    if "products" in data:
        return data["products"].get("items", [])
    return data.get("items", [])


def drift_scores(ranked: list[tuple[str, float]], modes: dict[str, str]) -> tuple[float, int | None]:
    """Summed (softmax-normalized) score mass on consuming items in the top K, and the best rank of one."""
    if not ranked:
        return 0.0, None
    import math

    mx = max(s for _, s in ranked)
    w = [math.exp(s - mx) for _, s in ranked]
    total = sum(w) or 1.0
    mass = sum(wi for (cid, _), wi in zip(ranked, w) if modes.get(cid) == "consuming") / total
    best = next((i + 1 for i, (cid, _) in enumerate(ranked) if modes.get(cid) == "consuming"), None)
    return mass, best


def _load_catalog_state(cfg: Config) -> dict | None:
    p = cfg.data_dir / "behaviorgpt_catalog.json"
    return json.loads(p.read_text()) if p.exists() else None


def save_catalog_state(cfg: Config, catalog_id: str, mapping: dict, modes: dict) -> Path:
    p = cfg.data_dir / "behaviorgpt_catalog.json"
    p.write_text(json.dumps({"catalog_id": catalog_id, "mapping": mapping, "modes": modes}, indent=1))
    return p


@register("behaviorgpt")
def _factory(cfg=None):
    return BehaviorGPTPredictor(cfg)
