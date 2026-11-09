from datetime import UTC, datetime, timedelta

from behaviornudge.intent import Intent, NO_INTENT
from behaviornudge.nudge.calls import CallState
from behaviornudge.nudge.rules import RuleEngine
from behaviornudge.nudge.settings import load_settings
from behaviornudge.nudge.store import NudgeStore
from behaviornudge.nudge.tracker import LiveTracker

T = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)  # 10:00 New York
PRONE = ["linkedin.com", "x.com", "instagram.com"]
CHROME = "com.google.Chrome"
TIMER = Intent("timer", "Client tests", "ClientCo", "ClientCo", "t1")
BLOCK = Intent("calendar", "Deep Work — Client Projects", None, None, "ev1")


def s(sec):
    return T + timedelta(seconds=sec)


class World:
    def __init__(self, tmp_path, **settings):
        self.store = NudgeStore(tmp_path / "n.sqlite")
        self.eng = RuleEngine(load_settings(settings), self.store, PRONE, "America/New_York")
        self.tr = LiveTracker({})

    def app(self, bundle, name, at):
        self.tr.on_front_app(bundle, name, at)

    def tab(self, url, at, title="t"):
        if self.tr.front_bundle != CHROME:
            self.tr.on_front_app(CHROME, "Google Chrome", at)
        self.tr.on_tab(url, title, at)

    def run(self, until, intent, step=1, **kw):
        """Tick the engine every `step` seconds; return decisions (fired and suppressed)."""
        out, t = [], self.tr.sessions[-1].end if self.tr.sessions else T
        while t <= until:
            self.tr.tick(t)
            d = self.eng.evaluate(t, self.tr.sessions, intent, **kw)
            if d:
                if d.fired:
                    self.store.add_nudge(time=t, type=d.type, fired=True, site=d.site, activity_key=d.key)
                out.append((t, d))
            t += timedelta(seconds=step)
        return out


def types(decisions, fired=True):
    return [d.type for _, d in decisions if d.fired == fired]


def test_arrival_while_replying_during_work_block(tmp_path):
    w = World(tmp_path)
    w.tab("https://www.linkedin.com/messaging/thread/1/", s(0))
    ds = w.run(s(25), TIMER)
    assert types(ds) == ["arrival"]
    t, d = ds[0]
    assert t == s(20) and d.intent.title == "Client tests"


def test_no_arrival_when_site_is_the_task(tmp_path):
    w = World(tmp_path)
    w.tab("https://www.linkedin.com/messaging/thread/1/", s(0))
    assert w.run(s(60), Intent("timer", "Alex re LI post", ref="t2")) == []


def test_purpose_done_after_reply_then_feed(tmp_path):
    w = World(tmp_path, arrival_delay_s=600)  # keep arrival out of the way
    w.tab("https://www.linkedin.com/messaging/thread/1/", s(0))
    w.run(s(60), TIMER)
    w.tab("https://www.linkedin.com/feed/", s(61))
    ds = w.run(s(95), TIMER)
    assert types(ds) == ["purpose_done"] and ds[0][0] == s(91)


def test_off_task_aimless_scrolling(tmp_path):
    w = World(tmp_path, feed_arrival_delay_s=10**6)  # isolate the off-task rule
    w.tab("https://x.com/home", s(0))
    ds = w.run(s(8 * 60 + 5), BLOCK, step=5)
    assert types(ds) == ["off_task"] and ds[0][1].intent.title == "Deep Work — Client Projects"


def test_no_off_task_without_intent(tmp_path):
    w = World(tmp_path)
    w.tab("https://x.com/home", s(0))
    assert w.run(s(20 * 60), NO_INTENT, step=10) == []


def test_return_to_work_resets_the_stretch(tmp_path):
    w = World(tmp_path, feed_arrival_delay_s=10**6)
    w.tab("https://x.com/home", s(0))
    w.run(s(5 * 60), BLOCK, step=10)
    w.app("dev.zed.Zed", "Zed", s(5 * 60 + 1))
    w.run(s(7 * 60), BLOCK, step=10)          # 2 min of work
    w.tab("https://x.com/home", s(7 * 60 + 1))
    assert w.run(s(13 * 60), BLOCK, step=10) == []  # only 6 min since the return


def test_in_call_browsing_names_the_meeting(tmp_path):
    w = World(tmp_path)
    call = CallState(True, s(-600), "Zoom", "New England Music project")
    w.tab("https://x.com/home", s(0))
    ds = w.run(s(40), NO_INTENT, call=call)
    assert types(ds) == ["in_call"] and ds[0][1].call_name == "New England Music project" and ds[0][0] == s(30)


def test_in_call_without_calendar_names_app(tmp_path):
    w = World(tmp_path)
    call = CallState(True, s(-60), "Zoom", "your Zoom call")
    w.tab("https://www.instagram.com/", s(0))
    ds = w.run(s(35), NO_INTENT, call=call)
    assert ds[0][1].call_name == "your Zoom call"


def test_agent_working_doom_scrolling(tmp_path):
    w = World(tmp_path)
    w.tab("https://www.instagram.com/reels/", s(0))
    ds = w.run(s(130), NO_INTENT, agent_working_since=s(-30), agent_project="behaviornudge")
    assert types(ds) == ["agent_working"] and ds[0][1].agent_project == "behaviornudge"


def test_precedence_in_call_beats_off_task(tmp_path):
    w = World(tmp_path, off_task_threshold_min=0.5, in_call_delay_s=30)
    w.tab("https://x.com/home", s(0))
    ds = w.run(s(31), BLOCK, call=CallState(True, s(-60), "Zoom", "Standup"))
    assert types(ds)[0] == "in_call"


def test_budget_used_logs_suppression(tmp_path):
    w = World(tmp_path, daily_budget=0)
    w.tab("https://www.linkedin.com/messaging/thread/1/", s(0))
    ds = w.run(s(25), TIMER)
    assert types(ds) == [] and [d.reason for _, d in ds] == ["budget"]


def test_snooze_blocks_until_expiry(tmp_path):
    w = World(tmp_path, feed_arrival_delay_s=10**6)
    w.store.set_state("snooze_until", s(20 * 60).isoformat())
    w.tab("https://x.com/home", s(0))
    ds = w.run(s(9 * 60), BLOCK, step=10)
    assert [d.reason for _, d in ds] == ["snoozed"]


def test_quiet_hours(tmp_path):
    w = World(tmp_path, quiet_hours=[["09:30", "11:00"]])  # T is 10:00 local
    w.tab("https://www.linkedin.com/messaging/thread/1/", s(0))
    assert [d.reason for _, d in w.run(s(25), TIMER)] == ["quiet_hours"]


def test_legit_suppression_and_cooldown(tmp_path):
    w = World(tmp_path)
    w.store.add_suppression("linkedin.com/messaging", "t1", "Client tests", None)
    w.tab("https://www.linkedin.com/messaging/thread/1/", s(0))
    assert [d.reason for _, d in w.run(s(25), TIMER)] == ["legit"]
    w2 = World(tmp_path / "b")
    w2.store.add_nudge(time=s(-60), type="arrival", fired=True, site="linkedin.com")
    w2.tab("https://www.linkedin.com/messaging/thread/1/", s(0))
    assert [d.reason for _, d in w2.run(s(25), TIMER)] == ["cooldown"]


def test_too_soon_multiplier_lengthens_wait(tmp_path):
    w = World(tmp_path, feed_arrival_delay_s=10**6)
    w.store.set_multiplier("off_task", "x.com", "calendar", 1.5)
    w.tab("https://x.com/home", s(0))
    ds = w.run(s(13 * 60), BLOCK, step=5)
    assert types(ds) == ["off_task"] and ds[0][0] >= s(12 * 60)


SUNDAI = Intent("calendar", "Prep for Sundai demo", None, None, "ev9")


def test_landing_straight_on_the_feed(tmp_path):
    w = World(tmp_path)
    w.tab("https://www.linkedin.com/feed/", s(0))
    ds = w.run(s(50), SUNDAI)
    assert types(ds) == ["arrival"] and ds[0][0] == s(45) and ds[0][1].intent.title == "Prep for Sundai demo"


def test_feed_hopping_on_same_site_counts_together(tmp_path):
    w = World(tmp_path)
    w.tab("https://www.linkedin.com/feed/", s(0))
    w.run(s(20), SUNDAI)
    w.tab("https://www.linkedin.com/posts/someone-123/", s(21))
    w.run(s(35), SUNDAI)
    w.tab("https://www.linkedin.com/feed/", s(36))
    ds = w.run(s(50), SUNDAI)
    assert types(ds) == ["arrival"] and len(ds) == 1


def test_no_feed_landing_nudge_without_intent_or_when_on_task(tmp_path):
    w = World(tmp_path)
    w.tab("https://www.linkedin.com/feed/", s(0))
    assert w.run(s(120), NO_INTENT) == []
    w2 = World(tmp_path / "b")
    w2.tab("https://www.linkedin.com/feed/", s(0))
    assert w2.run(s(120), Intent("timer", "Alex re LI post", ref="t2")) == []


def test_snooze_also_holds_feed_landing(tmp_path):
    w = World(tmp_path)
    w.store.set_state("snooze_until", s(20 * 60).isoformat())
    w.tab("https://x.com/home", s(0))
    assert [d.reason for _, d in w.run(s(60), BLOCK)] == ["snoozed"]
