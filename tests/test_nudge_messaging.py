"""Messaging drift (openspec change messaging-drift): hopping and lingering in messaging apps."""

import json
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from behaviornudge.intent import NO_INTENT, Intent
from behaviornudge.nudge import content
from behaviornudge.nudge.calls import CallState
from behaviornudge.nudge.engine import NudgeEngine
from behaviornudge.nudge.messaging import conversation_id, messaging_app, normalize_title, retrace_window
from behaviornudge.nudge.settings import DEFAULT_MESSAGING_APPS, load_settings

sys.path.insert(0, str(Path(__file__).parent))
from test_nudge_rules import BLOCK, TIMER, World, s, types  # noqa: E402  (shared rule-test harness)

SLACK = ("com.tinyspeck.slackmacgap", "Slack")
WHATSAPP = ("net.whatsapp.WhatsApp", "WhatsApp")
DISCORD = ("com.hnc.Discord", "Discord")
ZED = ("dev.zed.Zed", "Zed")


def convo(w, n, at, app="slack"):
    w.eng.note_conversation(conversation_id(f"channel-{n} (Channel) - Acme - Slack", app), at)


def run_with_convos(w, until, intent, convos=(), **kw):
    """Tick like the engine, noting conversation n at its time."""
    sched = dict(convos)  # seconds -> conversation number
    out, t = [], w.tr.sessions[-1].end
    while t <= until:
        sec = int((t - s(0)).total_seconds())
        if sec in sched:
            convo(w, sched[sec], t)
        w.tr.tick(t)
        d = w.eng.evaluate(t, w.tr.sessions, intent, **kw)
        if d:
            if d.fired:
                w.store.add_nudge(time=t, type=d.type, fired=True, site=d.site, activity_key=d.key)
            out.append((t, d))
        t += timedelta(seconds=1)
    return out


# ---- titles --------------------------------------------------------------------------------

def test_normalize_titles():
    assert normalize_title("general (Channel) - Acme - 1 new item - Slack") == "general (channel) - acme"
    assert normalize_title("general (Channel) - Acme - Slack") == "general (channel) - acme"
    assert normalize_title("Dana (DM) - Acme - 3 new items - Slack") == "dana (dm) - acme"
    assert normalize_title("#help | Some Server - Discord") == "#help | some server"
    assert normalize_title("(3) Discord | #general | Server") == "discord | #general | server"
    for bare in ("Signal (1)", "WhatsApp", "(2) WhatsApp", "Slack", "", None):
        assert normalize_title(bare) == ""


def test_same_channel_with_new_items_is_one_conversation():
    a = conversation_id("general (Channel) - Acme - 1 new item - Slack", "slack")
    b = conversation_id("general (Channel) - Acme - Slack", "slack")
    assert a == b and a is not None and len(a) == 12
    assert conversation_id("Signal (2)", "signal") is None


def test_messaging_app_native_and_web():
    apps = DEFAULT_MESSAGING_APPS
    assert messaging_app(SimpleNamespace(app="slack", domain=None), apps) == "slack"
    assert messaging_app(SimpleNamespace(app="chrome", domain="app.slack.com"), apps) == "slack"
    assert messaging_app(SimpleNamespace(app="chrome", domain="web.whatsapp.com"), apps) == "whatsapp"
    assert messaging_app(SimpleNamespace(app="chrome", domain="linkedin.com"), apps) is None
    assert messaging_app(SimpleNamespace(app="zed", domain=None), apps) is None
    assert messaging_app(SimpleNamespace(app="slack", domain=None), []) is None


def _status(tmp_path, app, window, at):
    p = tmp_path / "status.json"
    p.write_text(json.dumps({"last_app": app, "last_window": window, "last_hash_at": at.isoformat()}))
    return p


def test_retrace_window_fresh_stale_missing_and_other_app(tmp_path):
    p = _status(tmp_path, "Slack", "general (Channel) - Acme - Slack", s(0))
    assert retrace_window(p, "slack", s(10)) == "general (Channel) - Acme - Slack"
    assert retrace_window(p, "slack", s(45)) is None          # older than 30 s
    assert retrace_window(p, "discord", s(10)) is None        # recorded for another app
    assert retrace_window(tmp_path / "missing.json", "slack", s(10)) is None
    (tmp_path / "bad.json").write_text("{not json")
    assert retrace_window(tmp_path / "bad.json", "slack", s(10)) is None


def test_engine_notes_conversation_from_retrace_or_tab(tmp_path):
    noted = []
    fake = SimpleNamespace(st=load_settings(None), rules=SimpleNamespace(note_conversation=lambda c, t: noted.append(c)),
                           retrace_status=_status(tmp_path, "Slack", "general (Channel) - Acme - Slack", s(0)))
    native = SimpleNamespace(app="slack", domain=None, title="Slack", closed=False)
    NudgeEngine._note_conversation(fake, [native], s(5))
    web = SimpleNamespace(app="chrome", domain="discord.com", title="#help | Server - Discord", closed=False)
    NudgeEngine._note_conversation(fake, [web], s(6))
    other = SimpleNamespace(app="zed", domain=None, title="main.py", closed=False)
    NudgeEngine._note_conversation(fake, [other], s(7))
    assert noted == [conversation_id("general (Channel) - Acme - Slack", "slack"),
                     conversation_id("#help | Server - Discord", "discord")]


# ---- rules ---------------------------------------------------------------------------------

def test_checking_the_unreads(tmp_path):
    w = World(tmp_path)
    w.app(*SLACK, s(0))
    ds = run_with_convos(w, s(90), TIMER, convos={0: 1, 15: 2, 30: 3, 45: 4})
    assert types(ds) == ["messaging"]
    t, d = ds[0]
    assert t == s(60) and d.hopping and d.site == "slack" and d.title is None
    assert d.intent.title == "Client tests"
    c = content.build("messaging", site=d.site, intent_title=d.intent.title, intent_source="timer", app=d.site, hopping=True)
    assert c.title == "Browsing Slack unreads?" and "Client tests" in c.body


def test_quick_reply_stays_quiet(tmp_path):
    w = World(tmp_path)
    w.app(*WHATSAPP, s(0))
    run_with_convos(w, s(89), TIMER, convos={0: 1})
    w.app(*ZED, s(90))
    assert run_with_convos(w, s(500), TIMER) == []


def test_lingering_in_one_app(tmp_path):
    w = World(tmp_path)
    w.app(*DISCORD, s(0))
    ds = run_with_convos(w, s(320), BLOCK)
    assert types(ds) == ["messaging"]
    t, d = ds[0]
    assert t == s(300) and not d.hopping
    c = content.build("messaging", site="discord", intent_title=d.intent.title, intent_source="calendar", app="discord")
    assert c.title == "Still in Discord?"


def test_hopping_between_apps_is_one_stretch(tmp_path):
    w = World(tmp_path)
    w.app(*SLACK, s(0))
    assert run_with_convos(w, s(119), TIMER) == []
    w.app(*WHATSAPP, s(120))
    ds = run_with_convos(w, s(320), TIMER)
    assert types(ds) == ["messaging"] and ds[0][0] == s(300)


def test_fires_once_per_stretch(tmp_path):
    w = World(tmp_path, cooldown_min=0)
    w.app(*SLACK, s(0))
    ds = run_with_convos(w, s(1200), TIMER, convos={0: 1, 10: 2, 20: 3, 30: 4, 400: 5})
    assert types(ds) == ["messaging"]


def test_sixty_seconds_back_at_work_ends_the_stretch(tmp_path):
    w = World(tmp_path)
    w.app(*SLACK, s(0))
    assert run_with_convos(w, s(199), TIMER, convos={0: 1, 10: 2, 20: 3}) == []
    w.app(*ZED, s(200))
    run_with_convos(w, s(269), TIMER)
    w.app(*SLACK, s(270))
    # one more conversation: the three from the earlier stretch don't count; lingering restarts at 270
    assert run_with_convos(w, s(560), TIMER, convos={300: 4}) == []


def test_task_is_a_reply(tmp_path):
    w = World(tmp_path)
    w.app(*SLACK, s(0))
    reply = Intent("timer", "Reply to Chris re job hunt", ref="t3")
    assert run_with_convos(w, s(600), reply, convos={0: 1, 10: 2, 20: 3, 30: 4, 40: 5, 50: 6}) == []


def test_no_intent(tmp_path):
    w = World(tmp_path)
    w.app(*SLACK, s(0))
    assert run_with_convos(w, s(600), NO_INTENT, convos={0: 1, 10: 2, 20: 3, 30: 4}) == []


def test_slack_huddle(tmp_path):
    w = World(tmp_path)
    w.app(*SLACK, s(0))
    call = CallState(True, s(-60), "Slack", "your Slack call")
    assert run_with_convos(w, s(600), TIMER, convos={0: 1, 10: 2, 20: 3, 30: 4}, call=call) == []


def test_legit_task_suppresses(tmp_path):
    w = World(tmp_path)
    w.store.add_suppression("slack", "t1", "Client tests", None)
    w.app(*SLACK, s(0))
    ds = run_with_convos(w, s(320), TIMER)
    assert types(ds) == [] and types(ds, fired=False) == ["messaging"]
    assert ds[0][1].reason == "legit"


def test_too_soon_lengthens_lingering(tmp_path):
    w = World(tmp_path)
    w.store.set_multiplier("messaging", "slack", "timer", 1.5)
    w.app(*SLACK, s(0))
    ds = run_with_convos(w, s(460), TIMER)
    assert types(ds) == ["messaging"] and ds[0][0] == s(450)


def test_web_messaging_counts(tmp_path):
    w = World(tmp_path)
    w.tab("https://app.slack.com/client/T1/C1", s(0), title="general (Channel) - Acme - Slack")
    ds = run_with_convos(w, s(310), TIMER)
    assert types(ds) == ["messaging"] and ds[0][1].site == "slack"


def test_turned_off_with_empty_app_list(tmp_path):
    w = World(tmp_path, messaging_apps=[])
    w.app(*SLACK, s(0))
    assert run_with_convos(w, s(600), TIMER, convos={0: 1, 10: 2, 20: 3, 30: 4}) == []


def test_messaging_defaults():
    st = load_settings(None)
    assert st.sound("messaging") == "Purr" and st.base_delay_s("messaging") == 300
    assert st.messaging_hop_conversations == 4 and st.messaging_hop_min_s == 60
