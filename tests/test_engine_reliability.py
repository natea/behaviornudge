"""engine-reliability: plain notices, Sunsama sign-in lock and login notice."""

import threading
import time
from types import SimpleNamespace

import pytest

from behaviornudge.nudge.engine import NudgeEngine
from behaviornudge.sunsama_mcp import FileTokenStorage, LoginRequired, needs_login


def _fake_engine():
    sent, logged = [], []
    fake = SimpleNamespace(_notices={}, NOTICE_EVERY_S=3600, log=logged.append,
                           helper=SimpleNamespace(notify=lambda *a: sent.append(a) or True))
    fake.notice = lambda *a: NudgeEngine.notice(fake, *a)
    return fake, sent


def test_notice_is_plain_and_hourly():
    fake, sent = _fake_engine()
    assert NudgeEngine.notice(fake, "browser", "t", "b")
    assert not NudgeEngine.notice(fake, "browser", "t", "b")   # within the hour
    assert NudgeEngine.notice(fake, "sunsama", "t", "b")       # other kinds are independent
    assert [a[4] for a in sent] == ["notice", "notice"] and all(a[3] is None for a in sent)  # no sound
    assert all(str(a[0]).startswith("notice-") for a in sent)  # never a nudge id
    fake._notices["browser"] -= 3601
    assert NudgeEngine.notice(fake, "browser", "t", "b")


def test_foreign_tab_shows_browser_notice():
    fake, sent = _fake_engine()
    NudgeEngine._foreign_tab(fake, "behaviornudge and BehaviorGPT", "Feed | LinkedIn - Google Chrome")
    assert len(sent) == 1 and "browser" in sent[0][2].lower()


def test_login_required_is_found_inside_exception_groups():
    try:
        try:
            raise LoginRequired("x")
        except LoginRequired as inner:
            raise RuntimeError("OAuth flow error") from inner
    except RuntimeError as e:
        wrapped = e
    assert needs_login(ExceptionGroup("unhandled errors in a TaskGroup", [wrapped]))
    assert not needs_login(ExceptionGroup("g", [TimeoutError("read timeout")]))


def test_sunsama_login_notice_and_reraise():
    fake, sent = _fake_engine()

    def expired():
        raise ExceptionGroup("unhandled errors in a TaskGroup", [LoginRequired("login")])
    call = NudgeEngine._sunsama(fake, expired)
    with pytest.raises(ExceptionGroup):
        call()
    with pytest.raises(ExceptionGroup):
        call()
    assert len(sent) == 1 and "sunsama-timer --login" in sent[0][2]
    ok = NudgeEngine._sunsama(fake, lambda: {"hasActiveTimer": False})
    assert ok() == {"hasActiveTimer": False}


def test_sunsama_calls_are_serialized(tmp_path):
    """Two callers (timer poller, timebox refresh) never hold the token file at once."""
    st = FileTokenStorage(tmp_path / "sunsama_oauth.json")
    inside, overlap = [0], []

    def caller():
        with st.locked():
            inside[0] += 1
            overlap.append(inside[0])
            time.sleep(0.05)
            inside[0] -= 1
    ts = [threading.Thread(target=caller) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert overlap == [1, 1, 1, 1]
