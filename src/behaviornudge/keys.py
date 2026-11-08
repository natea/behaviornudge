"""Activity-key normalization: turn raw app/URL records into coarse, privacy-safe keys.

Browser pages become ``domain/path-class``; native apps become a short app name;
terminals and editors become ``app:project`` once a project is known. Full URLs,
query strings and screen text never appear in a key.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

BROWSER_BUNDLES = {
    "com.google.Chrome",
    "com.google.Chrome.canary",
    "com.apple.Safari",
    "company.thebrowser.Browser",
    "org.mozilla.firefox",
    "com.brave.Browser",
    "com.microsoft.edgemac",
}

# Apps whose project matters (terminals and editors)
PROJECT_APPS = {"ghostty", "zed", "terminal", "iterm", "vscode", "cursor", "xcode"}

APP_NAMES = {
    "com.mitchellh.ghostty": "ghostty",
    "dev.zed.Zed": "zed",
    "com.superhuman.electron": "superhuman",
    "net.whatsapp.WhatsApp": "whatsapp",
    "com.tinyspeck.slackmacgap": "slack",
    "us.zoom.xos": "zoom",
    "com.apple.MobileSMS": "messages",
    "com.anthropic.claudefordesktop": "claude",
    "org.whispersystems.signal-desktop": "signal",
    "com.hnc.Discord": "discord",
    "com.apple.Terminal": "terminal",
    "com.googlecode.iterm2": "iterm",
    "com.microsoft.VSCode": "vscode",
    "com.todesktop.230313mzl4w4u92": "cursor",
    "com.apple.dt.Xcode": "xcode",
    "com.apple.iCal": "calendar",
    "com.apple.mail": "mail",
    "com.apple.finder": "finder",
    "com.apple.Preview": "preview",
    "com.sunsama.app": "sunsama",
    "com.google.Chrome": "chrome",
    "com.apple.Safari": "safari",
    "company.thebrowser.Browser": "arc",
    "org.mozilla.firefox": "firefox",
    "com.brave.Browser": "brave",
    "com.microsoft.edgemac": "edge",
}

# Display names (as jhammant Retrace stores app_name) -> short names
DISPLAY_NAMES = {
    "Ghostty": "ghostty", "Zed": "zed", "Superhuman": "superhuman", "WhatsApp": "whatsapp",
    "Slack": "slack", "zoom.us": "zoom", "Messages": "messages", "Claude": "claude",
    "Signal": "signal", "Discord": "discord", "Terminal": "terminal", "iTerm2": "iterm",
    "Code": "vscode", "Cursor": "cursor", "Xcode": "xcode", "Calendar": "calendar",
    "Mail": "mail", "Finder": "finder", "Preview": "preview", "Sunsama": "sunsama",
    "Google Chrome": "chrome", "Safari": "safari", "Arc": "arc", "Firefox": "firefox",
}


def app_short_name(bundle_id: str | None, display_name: str | None = None) -> str:
    if bundle_id and bundle_id in APP_NAMES:
        return APP_NAMES[bundle_id]
    if display_name and display_name in DISPLAY_NAMES:
        return DISPLAY_NAMES[display_name]
    if bundle_id:
        return bundle_id.rsplit(".", 1)[-1].lower()
    if display_name:
        return display_name.strip().lower().replace(" ", "-")
    return "unknown"


def is_browser(bundle_id: str | None, display_name: str | None = None) -> bool:
    if bundle_id in BROWSER_BUNDLES:
        return True
    return app_short_name(bundle_id, display_name) in {"chrome", "safari", "arc", "firefox", "brave", "edge"}


def normalize_domain(host: str) -> str:
    host = host.lower().split("@")[-1]
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    if host == "twitter.com":
        host = "x.com"
    if host.startswith("localhost") or host.startswith("127.0.0.1"):
        return "localhost"
    return host


def _segments(path: str) -> list[str]:
    return [p for p in path.split("/") if p]


def path_class(domain: str, path: str) -> str | None:
    """Coarse class of a page within a site, or None to key by domain only."""
    s = _segments(path)
    first = s[0].lower() if s else ""
    if domain == "linkedin.com":
        if not s or first == "feed":
            return "post" if len(s) > 1 and s[1] == "update" else "feed"
        return {
            "messaging": "messaging", "in": "profile", "search": "search", "notifications": "notifications",
            "jobs": "jobs", "posts": "post", "company": "company", "mynetwork": "network", "events": "events",
        }.get(first, "other")
    if domain == "x.com":
        if not s or first == "home":
            return "home"
        if len(s) >= 3 and s[1] == "status":
            return "status"
        if first in ("search", "explore"):
            return "search"
        if first in ("messages", "notifications"):
            return first
        if first == "i":
            return "other"
        return "profile"
    if domain == "instagram.com":
        if not s:
            return "feed"
        return {"reels": "reels", "reel": "reels", "direct": "direct", "p": "post", "explore": "explore", "stories": "stories"}.get(first, "profile")
    if domain == "youtube.com":
        if not s:
            return "home"
        return {"watch": "watch", "shorts": "shorts", "results": "search", "feed": "feed", "playlist": "playlist"}.get(
            first, "channel" if first.startswith("@") or first in ("channel", "c") else "other"
        )
    if domain == "github.com":
        if not s:
            return "home"
        if first in ("notifications", "pulls", "issues", "search", "explore", "settings", "marketplace"):
            return first
        return "repo" if len(s) >= 2 else "profile"
    return None


@dataclass(frozen=True)
class KeyInfo:
    key: str
    app: str
    domain: str | None = None
    path_class: str | None = None
    project_hint: str | None = None  # e.g. repo name from a GitHub URL


def url_key(url: str) -> KeyInfo | None:
    """Key for a browser URL, or None if the URL is unusable."""
    if not url:
        return None
    if url.startswith("file:"):
        return KeyInfo(key="file", app="browser", domain="file")
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.netloc:
        return None
    domain = normalize_domain(parts.netloc)
    pc = path_class(domain, parts.path)
    hint = None
    if domain == "github.com" and pc == "repo":
        hint = _segments(parts.path)[1].lower()
    key = f"{domain}/{pc}" if pc else domain
    return KeyInfo(key=key, app="browser", domain=domain, path_class=pc, project_hint=hint)


def activity_key(
    bundle_id: str | None,
    display_name: str | None = None,
    url: str | None = None,
    project: str | None = None,
) -> KeyInfo:
    """Normalize one raw record to an activity key."""
    app = app_short_name(bundle_id, display_name)
    if url and is_browser(bundle_id, display_name):
        info = url_key(url)
        if info:
            return KeyInfo(key=info.key, app=app, domain=info.domain, path_class=info.path_class, project_hint=info.project_hint)
    if app in PROJECT_APPS and project:
        return KeyInfo(key=f"{app}:{project}", app=app)
    return KeyInfo(key=app, app=app)
