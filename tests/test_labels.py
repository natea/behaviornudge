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
