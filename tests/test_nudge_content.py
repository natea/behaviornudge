from datetime import UTC, datetime

from behaviornudge.nudge import content
from behaviornudge.nudge.settings import load_settings
from behaviornudge.nudge.status import status_lines
from behaviornudge.nudge.store import NudgeStore


def test_every_type_names_the_task_and_source():
    for t, sample in content.SAMPLES.items():
        c = content.build(t, **sample)
        assert c.title and c.body
    c = content.build("purpose_done", site="linkedin.com", intent_title="Client tests", intent_source="timer")
    assert "Client tests" in c.body and "current Sunsama task" in c.body and "LinkedIn" in c.title


def test_off_task_and_calendar_wording():
    c = content.build("off_task", site="x.com", intent_title="Deep Work — Client Projects", intent_source="calendar", stretch_s=480)
    assert c.title == "8 min on X" and "(calendar)" in c.body


def test_in_call_and_agent_wording():
    assert "New England Music project" in content.build("in_call", site="x.com", intent_title=None, intent_source="call",
                                                        call_name="New England Music project").body
    assert "behaviornudge" in content.build("agent_working", site="instagram.com", intent_title="behaviornudge",
                                            intent_source="agent", agent_project="behaviornudge").body


def test_sound_per_type_in_call_silent():
    st = load_settings(None)
    assert st.sound("in_call") is None and st.sound("off_task") == "Sosumi"


def test_status_reports_denied_permission(tmp_path):
    st = NudgeStore(tmp_path / "n.sqlite")
    lines = status_lines(st, load_settings(None), datetime(2026, 11, 9, 15, tzinfo=UTC), "America/New_York",
                         authorized=False, running=True, intent={"title": "Client tests", "source": "timer"})
    text = "\n".join(lines)
    assert "NOT allowed" in text and "System Settings" in text and "Persistent" in text
    assert "budget: 0 of 8" in text and "Client tests" in text


def test_feed_landing_wording():
    c = content.build("arrival", site="linkedin.com", intent_title="Prep for Sundai demo", intent_source="calendar",
                      stretch_s=45, feed=True)
    assert c.title == "Scrolling LinkedIn?" and "Prep for Sundai demo" in c.body
