"""Active browser tab (URL + title) via AppleScript, polled while a browser is frontmost.

Retrace's watcher only fires on app switches, so tab changes inside a browser are read here.

`tell application "Google Chrome"` addresses one running copy of the app, which isn't necessarily
the one in front when a second copy (an automation browser, say) is open. Each read therefore also
returns the title of the frontmost window of the frontmost process (System Events), and a tab whose
title isn't in that window title is treated as a foreign answer and dropped (engine-reliability D1).
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


FRONT_WINDOW = ('set fb to ""\n'
                'set fw to ""\n'
                'try\n'
                '  tell application "System Events"\n'
                '    set fp to first application process whose frontmost is true\n'
                '    set fb to bundle identifier of fp\n'
                '    set fw to name of front window of fp\n'
                '  end tell\n'
                'end try\n')
TITLE_PREFIX = 40  # long tab titles can be cut short in the window title


def script_for(bundle_id: str) -> str | None:
    if bundle_id not in BROWSERS:
        return None
    app, kind = BROWSERS[bundle_id]
    if kind == "safari":
        tab = f'(URL of front document) & "{SEP}" & (name of front document)'
    else:
        tab = f'(URL of active tab of front window) & "{SEP}" & (title of active tab of front window)'
    return FRONT_WINDOW + f'tell application "{app}" to if (count of windows) > 0 then return {tab} & "{SEP}" & fb & "{SEP}" & fw'


def from_front_window(tab_title: str | None, window_title: str | None) -> bool:
    """False only when both titles are known and the tab's title isn't part of the front window's."""
    if not tab_title or not window_title:
        return True
    return tab_title.strip()[:TITLE_PREFIX].lower() in window_title.lower()


def read_tab(bundle_id: str, run: Runner = osascript,
             on_foreign: Callable[[str, str], None] | None = None) -> tuple[str | None, str | None]:
    """(url, title) of the front tab, or (None, None) when unsupported, unreadable, or answered by
    a copy of the browser that isn't the one in front (then `on_foreign(tab_title, window_title)`)."""
    script = script_for(bundle_id)
    if not script:
        return None, None
    out = run(script)
    if not out:
        return None, None
    url, _, rest = out.partition(SEP)
    title, _, rest = rest.partition(SEP)
    front, _, window = rest.partition(SEP)
    if front == bundle_id and not from_front_window(title, window):
        if on_foreign:
            on_foreign(title, window)
        return None, None
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
