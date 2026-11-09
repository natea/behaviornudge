"""`behaviornudge nudge status` text."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .settings import NudgeSettings
from .store import NudgeStore

PERMISSION_HELP = ("Notifications for behaviornudge are off. Turn them on in System Settings > Notifications > "
                   "behaviornudge. Set Alert Style to Persistent so nudges stay on screen with their buttons, and turn off "
                   "Summarize notifications so nudges aren't batched or reworded.")


def status_lines(store: NudgeStore, settings: NudgeSettings, now: datetime, tz: str, *, authorized: bool | None,
                 running: bool, intent: dict | None = None, current: dict | None = None,
                 recent: list | None = None) -> list[str]:
    z = ZoneInfo(tz)
    midnight = now.astimezone(z).replace(hour=0, minute=0, second=0, microsecond=0)
    used = store.fired_count_since(midnight)
    enabled = settings.enabled and store.get_state("enabled", True)
    snooze = store.get_state("snooze_until")
    lines = [
        f"engine: {'running' if running else 'not running'}",
        f"nudges: {'on' if enabled else 'off'}",
        f"budget: {used} of {settings.daily_budget} used today",
    ]
    if snooze and datetime.fromisoformat(snooze) > now:
        lines.append(f"snoozed until {datetime.fromisoformat(snooze).astimezone(z).strftime('%H:%M')}")
    if authorized is None:
        lines.append("notifications: unknown (helper not running)")
    elif authorized:
        lines.append("notifications: allowed")
    else:
        lines.append("notifications: NOT allowed")
        lines.append(PERMISSION_HELP)
    if intent:
        lines.append(f"task at hand: {intent.get('title') or '-'} ({intent.get('source')})")
    if current:
        lines.append(f"now: {current['key']} ({current.get('mode') or 'unlabeled'}, {current.get('alignment') or '-'}, {current.get('seconds')} s)")
    if recent:
        lines.append("recent (oldest first):")
        lines += [f"  {r['key']}  {r.get('mode') or 'unlabeled'}  {r['seconds']} s" for r in recent]
    return lines
