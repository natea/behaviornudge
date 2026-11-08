"""Project resolution for terminals and editors.

Folders map to their main git repository (worktrees resolve to the repo they belong
to); repositories map to Sunsama channels via config. For historical terminal
activity, projects are inferred from screen text, then window titles (marked inferred).
Live terminal context from herdr is plugged in via `context_projects` (see context.py).
"""

from __future__ import annotations

import bisect
import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .config import Config
from .keys import PROJECT_APPS, app_short_name
from .sources import Record, connect_ro, _parse_ts

DEFAULT_ROOTS = ("~/Developer", "~/Documents/code")
GENERIC = {"code", "src", "app", "test", "tests", "docs", "data", "tmp", "home", "main", "dev", "repo", "lib", "bin", "web", "api"}


def main_repo_name(folder: Path) -> str | None:
    """Name of the main repository that `folder` belongs to (worktrees -> their main repo)."""
    try:
        res = subprocess.run(["git", "-C", str(folder), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    common = Path(res.stdout.strip())
    # /x/repo/.git -> repo ; bare repos (/x/repo.git) -> repo
    return common.parent.name if common.name == ".git" else common.name.removesuffix(".git")


def discover_repos(roots=DEFAULT_ROOTS, depth: int = 2) -> dict[str, str]:
    """Map each git folder name found under roots to its main repository name."""
    found: dict[str, str] = {}
    for root in roots:
        base = Path(root).expanduser()
        if not base.exists():
            continue
        candidates = [base] + [p for p in base.iterdir() if p.is_dir()]
        if depth >= 2:
            candidates += [q for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")
                           for q in p.iterdir() if q.is_dir() and not q.name.startswith(".")]
        for folder in candidates:
            if (folder / ".git").exists():
                main = main_repo_name(folder)
                if main:
                    found[folder.name] = main
    return found


def project_for_folder(folder: str | Path, repo_map: dict[str, str] | None = None) -> str | None:
    folder = Path(folder).expanduser()
    if repo_map and folder.name in repo_map:
        return repo_map[folder.name]
    return main_repo_name(folder) or (folder.name or None)


def channel_for(project: str | None, cfg_projects: dict[str, str]) -> str | None:
    if not project:
        return None
    return cfg_projects.get(project, project)


class NameMatcher:
    """Find known repo names in free text. Path-style mentions (`/name`, `name/`) score higher;
    short or generic names only count in path style."""

    def __init__(self, names: dict[str, str]):
        self.map = names
        keys = sorted(names, key=len, reverse=True)
        self.rx = re.compile(r"(?<![\w.-])(" + "|".join(re.escape(k) for k in keys) + r")(?![\w-])") if keys else None

    def best(self, text: str) -> str | None:
        if not self.rx or not text:
            return None
        score: Counter = Counter()
        for m in self.rx.finditer(text):
            name = m.group(1)
            s, e = m.start(), m.end()
            path_style = (s > 0 and text[s - 1] in "/~:") or (e < len(text) and text[e] == "/")
            if not path_style and (len(name) < 5 or name.lower() in GENERIC):
                continue
            weight = 3 if path_style else 1
            if name.lower() in GENERIC:
                weight = 0.1  # generic folder names (e.g. ~/Documents/code) only win when nothing else matches
            score[self.map[name]] += weight
        return score.most_common(1)[0][0] if score else None


def title_project(app: str, title: str | None, matcher: NameMatcher) -> str | None:
    if not title:
        return None
    if app == "zed":
        head = re.split(r" — | - ", title, maxsplit=1)[0]
        first = head.split(",")[0].strip()
        if first in matcher.map:
            return matcher.map[first]
    if app in ("ghostty", "terminal", "iterm") and ": " in title:
        tail = title.split(": ", 1)[1].strip().rstrip("/").split("/")[-1]
        if tail in matcher.map:
            return matcher.map[tail]
    return matcher.best(title)


class ScreenTextIndex:
    """Repo-name hits from jhammant Retrace captures of terminals/editors (local only)."""

    def __init__(self, db: Path, matcher: NameMatcher, apps=("Ghostty", "Terminal", "iTerm2", "Zed")):
        self.times: list[datetime] = []
        self.projects: list[str] = []
        try:
            con = connect_ro(db)
        except FileNotFoundError:
            return
        try:
            q = "SELECT captured_at, text FROM captures WHERE app_name IN (%s) ORDER BY captured_at" % ",".join("?" * len(apps))
            for ts, text in con.execute(q, apps):
                p = matcher.best((text or "")[-8000:])  # the bottom of the screen is the live part
                t = _parse_ts(ts)
                if p and t:
                    self.times.append(t)
                    self.projects.append(p)
        finally:
            con.close()

    def near(self, t: datetime, within_s: float = 120) -> str | None:
        i = bisect.bisect_left(self.times, t)
        best, bestd = None, None
        for j in (i - 1, i):
            if 0 <= j < len(self.times):
                d = abs((self.times[j] - t).total_seconds())
                if d <= within_s and (bestd is None or d < bestd):
                    best, bestd = self.projects[j], d
        return best


Resolver = Callable[[Record], "tuple[str | None, str | None]"]


def make_resolver(cfg: Config, context_projects: Callable[[str, datetime], str | None] | None = None,
                  repo_map: dict[str, str] | None = None) -> Resolver:
    """Resolver for records -> (project, source). Sources: herdr, title, screen-text-inferred, title-inferred."""
    cache = cfg.data_dir / "repos.json"
    if repo_map is None:
        if cache.exists():
            repo_map = json.loads(cache.read_text())
        else:
            repo_map = discover_repos()
            cache.write_text(json.dumps(repo_map, indent=1, sort_keys=True))
    matcher = NameMatcher(repo_map)
    screen = ScreenTextIndex(cfg.path("sources", "retrace_jhammant_db"), matcher)

    def resolve(r: Record) -> tuple[str | None, str | None]:
        app = app_short_name(r.bundle_id, r.app_name)
        if app not in PROJECT_APPS:
            return None, None
        mid = r.start + (r.end - r.start) / 2
        if context_projects and app in ("ghostty", "terminal", "iterm"):
            p = context_projects(app, mid)
            if p:
                return p, "herdr"
        if app == "zed":
            p = title_project(app, r.title, matcher)
            if p:
                return p, "title"
        p = screen.near(mid)
        if p:
            return p, "screen-text-inferred"
        p = title_project(app, r.title, matcher)
        return (p, "title-inferred") if p else (None, None)

    return resolve
