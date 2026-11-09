from behaviornudge.intent import NO_INTENT, Intent
from behaviornudge.labels import Label, LabelStore, cache_key, definitions_fp, rule_alignment, rule_label

DEFS = definitions_fp({"creative": "make", "reactive": "reply", "consuming": "scroll"})


def S(key, app="chrome", domain=None, title=None, project=None):
    return {"key": key, "app": app, "domain": domain or (key.split("/")[0] if "." in key else None), "title": title, "project": project}


def test_rule_table():
    assert (rule_label(S("x.com/home"), {}).mode, rule_label(S("x.com/home"), {}).purpose) == ("consuming", "browse")
    m = rule_label(S("linkedin.com/messaging"), {})
    assert (m.mode, m.purpose) == ("reactive", "reply")
    l = rule_label(S("linkedin.com/search"), {})
    assert (l.mode, l.purpose) == ("reactive", "lookup")
    assert rule_label(S("linkedin.com/profile"), {}).purpose == "lookup"
    st = rule_label(S("x.com/status"), {})
    assert (st.mode, st.purpose) == ("consuming", "follow-link")
    z = rule_label(S("zed:behaviornudge", app="zed", project="behaviornudge"), {})
    assert (z.mode, z.project, z.sources["mode"]) == ("creative", "behaviornudge", "rule")
    assert rule_label(S("ghostty", app="ghostty"), {}).mode == "creative"


def test_channel_mapping_in_project_label():
    lab = rule_label(S("zed:newenglandmusic", app="zed", project="newenglandmusic"), {"newenglandmusic": "NE Music"})
    assert lab.project == "NE Music"


def test_ambiguous_left_for_later_layers():
    lab = rule_label(S("youtube.com/watch"), {})
    assert lab.mode is None and "mode" in lab.missing()


def test_no_intent_alignment():
    lab = rule_label(S("x.com/home"), {})
    assert rule_alignment(S("x.com/home"), lab, NO_INTENT) == "no-intent"


def test_feed_is_the_task():
    s = S("linkedin.com/feed")
    lab = rule_label(s, {})
    assert rule_alignment(s, lab, Intent("timer", "Alex re LI post", "Networking")) == "on-task"


def test_feed_during_other_task_is_off_task():
    s = S("linkedin.com/feed")
    assert rule_alignment(s, rule_label(s, {}), Intent("planned", "Client tests and deployment docs", "ClientCo")) == "off-task"


def test_project_match_and_mismatch():
    s = S("zed:clientco", app="zed", project="clientco")
    lab = rule_label(s, {"clientco": "ClientCo"})
    assert rule_alignment(s, lab, Intent("timer", "Client tests", "ClientCo", project="ClientCo")) == "on-task"
    assert rule_alignment(s, lab, Intent("timer", "Fix spec", "NE Music", project="NE Music")) == "off-task"


def test_creative_with_recent_creative_intent():
    s = S("ghostty", app="ghostty")
    assert rule_alignment(s, rule_label(s, {}), Intent("recent-creative", "behaviornudge", project=None)) == "on-task"


def test_cache_hit_and_user_answer(tmp_path):
    store = LabelStore(tmp_path / "labels.sqlite")
    s = S("youtube.com/watch", title="Some talk")
    it = Intent("calendar", "Deep Work")
    ck = cache_key(s, it, DEFS)
    assert store.get(ck) is None
    store.enqueue(ck, s, it, "low confidence", Label(mode="creative", confidence={"mode": 0.4}, sources={"mode": "model"}))
    items = store.open_items()
    assert len(items) == 1
    lab = store.answer(items[0]["id"], mode="consuming")
    assert lab.mode == "consuming" and lab.sources["mode"] == "user"
    again = store.get(cache_key(s, it, DEFS))
    assert again.mode == "consuming"  # reused without another request
    assert store.open_items() == []


def test_definitions_change_invalidates_cache_key():
    s = S("youtube.com/watch", title="x")
    it = Intent("none")
    other = definitions_fp({"creative": "changed", "reactive": "reply", "consuming": "scroll"})
    assert cache_key(s, it, DEFS) != cache_key(s, it, other)


def test_user_label_overrides_rule():
    from behaviornudge.labels import overlay_user
    s = S("linkedin.com/feed")
    rule = rule_label(s, {})
    rule.set("alignment", rule_alignment(s, rule, Intent("timer", "Prepare outreach list", ref="t9")), "rule")
    assert rule.alignment == "off-task"
    user = Label(alignment="on-task", sources={"alignment": "user"})
    merged = overlay_user(rule, user)
    assert merged.alignment == "on-task" and merged.sources["alignment"] == "user"
    assert merged.mode == "consuming" and merged.sources["mode"] == "rule"  # other dims keep the rule


def test_model_label_does_not_override_rule():
    from behaviornudge.labels import overlay_user
    rule = rule_label(S("x.com/home"), {})
    model = Label(mode="creative", sources={"mode": "model"})
    assert overlay_user(rule, model).mode == "consuming"


def test_label_all_applies_user_label_first(tmp_path):
    """End to end: a stored user alignment for (activity, intent) wins over the rule in label_all."""
    import json, sqlite3
    from datetime import datetime, UTC
    from behaviornudge.config import load_config
    from behaviornudge.labels import LabelStore, cache_key, definitions_fp
    from behaviornudge.pipeline import label_all
    from behaviornudge.timeline import Session, write_timeline
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "behaviornudge.example.toml")
    cfg.raw["data_dir"] = str(tmp_path)
    t0 = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)
    t1 = datetime(2026, 11, 9, 15, 10, tzinfo=UTC)
    write_timeline(tmp_path / "timeline.sqlite", [Session(t0, t1, "linkedin.com/feed", "chrome", "linkedin.com", "feed", {"retrace_to"}, "Feed")],
                   [], "America/New_York", {})
    (tmp_path / "calendar").mkdir()
    (tmp_path / "calendar" / "events.json").write_text(json.dumps([{"start": t0.isoformat(), "end": t1.isoformat(),
        "title": "Prepare outreach list", "calendar": "x", "id": "ev1"}]))
    first = label_all(cfg)
    row = sqlite3.connect(tmp_path / "labels.sqlite").execute("SELECT alignment FROM session_labels").fetchone()
    assert row[0] == "off-task"
    defs = definitions_fp(cfg.section("labeling").get("modes", {}))
    s = {"key": "linkedin.com/feed", "title": "Feed"}
    st = LabelStore(tmp_path / "labels.sqlite")
    st.put(cache_key(s, Intent("calendar", "Prepare outreach list", None, None, "ev1"), defs),
           Label(alignment="on-task", sources={"alignment": "user"}))
    st.close()
    label_all(cfg)
    row = sqlite3.connect(tmp_path / "labels.sqlite").execute("SELECT alignment, sources FROM session_labels").fetchone()
    assert row[0] == "on-task" and json.loads(row[1])["alignment"] == "user"


def test_label_store_usable_across_threads(tmp_path):
    import threading
    store = LabelStore(tmp_path / "labels.sqlite")
    store.put("ck", Label(mode="consuming", sources={"mode": "rule"}))
    out = {}
    t = threading.Thread(target=lambda: out.update(lab=store.get("ck")))
    t.start(); t.join()
    assert out["lab"].mode == "consuming"
