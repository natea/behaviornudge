from datetime import UTC, datetime, timedelta

from behaviornudge.labels import Label, rule_label
from behaviornudge.nudge.browser import SEP, read_tab
from behaviornudge.nudge.tracker import LiveTracker

T = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)
CHROME = "com.google.Chrome"


def s(sec):
    return T + timedelta(seconds=sec)


def test_read_tab_with_fake_runner():
    run = lambda script: f"https://www.linkedin.com/messaging/thread/1/{SEP}Messaging | LinkedIn"
    assert read_tab(CHROME, run) == ("https://www.linkedin.com/messaging/thread/1/", "Messaging | LinkedIn")
    assert read_tab("org.mozilla.firefox", run) == (None, None)
    assert read_tab(CHROME, lambda script: None) == (None, None)


def test_tab_change_updates_key_immediately():
    tr = LiveTracker({})
    tr.on_front_app(CHROME, "Google Chrome", s(0))
    tr.on_tab("https://docs.zed.dev/", "Zed docs", s(0))
    tr.on_tab("https://www.linkedin.com/messaging/thread/1/", "Messaging", s(2))
    assert tr.current().key == "linkedin.com/messaging"


def test_unsupported_browser_falls_back_to_app_key():
    tr = LiveTracker({})
    tr.on_front_app("org.mozilla.firefox", "Firefox", s(0))
    tr.on_tab(None, None, s(0))  # the engine's tab read returns nothing for unsupported browsers
    tr.tick(s(10))
    assert tr.current().key == "firefox"


def test_live_label_matches_backtest_rules():
    tr = LiveTracker({})
    tr.on_front_app(CHROME, "Google Chrome", s(0))
    tr.on_tab("https://x.com/home", "Home / X", s(0))
    lab = tr.current().label
    expected = rule_label({"key": "x.com/home", "app": "chrome", "domain": "x.com", "title": "Home / X", "project": None}, {})
    assert (lab.mode, lab.purpose) == (expected.mode, expected.purpose) == ("consuming", "browse")


def test_new_site_tracked_immediately_and_queued_for_jev():
    submitted = []
    tr = LiveTracker({}, jev_submit=lambda row, cb: submitted.append((row, cb)))
    tr.on_front_app(CHROME, "Google Chrome", s(0))
    tr.on_tab("https://never-seen.example.com/a", "Something new", s(0))
    cur = tr.current()
    assert cur.key == "never-seen.example.com" and cur.label.mode is None
    assert len(submitted) == 1
    submitted[0][1](Label(mode="consuming", purpose="browse", sources={"mode": "model", "purpose": "model"}))
    assert tr.current().label.mode == "consuming"  # applied when the background label arrives


def test_user_label_wins_live():
    user = Label(alignment="on-task", sources={"alignment": "user"})
    tr = LiveTracker({}, lookup=lambda row: (user, None))
    tr.on_front_app(CHROME, "Google Chrome", s(0))
    tr.on_tab("https://www.linkedin.com/feed/", "Feed", s(0))
    assert tr.current().label.alignment == "on-task"


def test_blip_is_transition_and_sessions_merge():
    tr = LiveTracker({})
    tr.on_front_app("dev.zed.Zed", "Zed", s(0))
    tr.on_front_app("com.tinyspeck.slackmacgap", "Slack", s(60))
    tr.on_front_app("dev.zed.Zed", "Zed", s(62))  # 2 s Slack blip
    tr.tick(s(120))
    assert [x.key for x in tr.sessions] == ["zed"]
    assert tr.sessions[0].start == s(0) and tr.sessions[0].end == s(120)


def test_terminal_project_from_herdr():
    tr = LiveTracker({}, project_for=lambda app, t: "behaviornudge")
    tr.on_front_app("com.mitchellh.ghostty", "Ghostty", s(0))
    assert tr.current().key == "ghostty:behaviornudge" and tr.current().label.mode == "creative"


def test_locked_screen_stops_accumulation():
    tr = LiveTracker({})
    tr.on_front_app(CHROME, "Google Chrome", s(0))
    tr.on_tab("https://x.com/home", "Home", s(0))
    tr.tick(s(60))
    tr.on_idle(0, True, s(60))       # locked
    tr.tick(s(60 * 30))              # 30 minutes locked
    assert sum(x.seconds for x in tr.sessions if x.key == "x.com/home") == 60
    tr.on_idle(0, False, s(60 * 30))  # unlocked: new session starts
    tr.tick(s(60 * 30 + 10))
    assert tr.current().start == s(60 * 30)
