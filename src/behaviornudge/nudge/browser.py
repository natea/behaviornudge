"""Active browser tab (URL + title) via AppleScript, polled while a browser is frontmost.

Retrace's watcher only fires on app switches, so tab changes inside a browser are read here.
"""

from __future__ import annotations

import subprocess
from typing import Callable

# bundle id -> (AppleScript application name, script kind)
BROWSERS = {
    "com.google.Chrome": ("Google Chrome", "chromium"),
    "com.brave.Browser": ("Brave Browser", "chromium"),
    "com.microsoft.edgemac": ("Microsoft Edge", "chromium"),
    "company.thebrowser.Browser": ("Arc", "chromium"),
    "com.apple.Safari": ("Safari", "safari"),
}
SEP = "␟"  # unit separator unlikely to appear in titles

Runner = Callable[[str], "str | None"]


def osascript(script: str) -> str | None:
    try:
        res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout.strip() if res.returncode == 0 else None


def script_for(bundle_id: str) -> str | None:
    if bundle_id not in BROWSERS:
        return None
    app, kind = BROWSERS[bundle_id]
    if kind == "safari":
        return (f'tell application "{app}" to if (count of windows) > 0 then '
                f'return (URL of front document) & "{SEP}" & (name of front document)')
    return (f'tell application "{app}" to if (count of windows) > 0 then '
            f'return (URL of active tab of front window) & "{SEP}" & (title of active tab of front window)')


def read_tab(bundle_id: str, run: Runner = osascript) -> tuple[str | None, str | None]:
    """(url, title) of the front tab, or (None, None) when unsupported or unreadable."""
    script = script_for(bundle_id)
    if not script:
        return None, None
    out = run(script)
    if not out:
        return None, None
    url, _, title = out.partition(SEP)
    return (url or None), (title or None)


def on_battery(run: Callable[[list[str]], str] | None = None) -> bool:
    try:
        out = run(["pmset", "-g", "batt"]) if run else subprocess.run(["pmset", "-g", "batt"], capture_output=True,
                                                                      text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "Battery Power" in out


def poll_interval(battery: bool) -> float:
    return 5.0 if battery else 2.0
