from datetime import UTC, datetime

from behaviornudge.calendar import parse_helper_output

ALLOW = ["Nate Aune (natejaune)"]


def _ev(cal, start, end, all_day=False, title="x"):
    return {"title": title, "start": start, "end": end, "calendar": cal, "allDay": all_day, "id": f"{cal}-{start}"}


def test_allowlist_and_all_day_filtering():
    s = int(datetime(2026, 11, 3, 16, 0, tzinfo=UTC).timestamp())
    raw = {"ok": True, "events": [
        _ev("Nate Aune (natejaune)", s, s + 1800, title="Client sync"),
        _ev("Meditation", s, s + 1200),
        _ev("Exercise", s, s + 3600),
        _ev("Nate Aune (natejaune)", s, s + 86400, all_day=True, title="Conference"),
    ]}
    events = parse_helper_output(raw, ALLOW)
    assert [e.title for e in events] == ["Client sync"]
    assert {e.calendar for e in events} == {"Nate Aune (natejaune)"}


def test_times_are_utc_with_correct_local_conversion():
    from zoneinfo import ZoneInfo
    s = int(datetime(2026, 11, 3, 16, 0, tzinfo=UTC).timestamp())
    e = parse_helper_output({"events": [_ev(ALLOW[0], s, s + 1800)]}, ALLOW)[0]
    loc = e.start.astimezone(ZoneInfo("America/New_York"))
    assert (loc.hour, loc.minute) == (11, 0)  # 16:00 UTC = 11:00 EST
    assert (e.end - e.start).total_seconds() == 1800
