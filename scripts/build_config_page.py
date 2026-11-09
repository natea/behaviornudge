"""Build docs/configuration.html from behaviornudge.example.toml.

Run: uv run python scripts/build_config_page.py
The code blocks come straight from the example config, so the page shows the real defaults.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "behaviornudge.example.toml"
OUT = ROOT / "docs" / "configuration.html"

NOTES: dict[str, tuple[str, str]] = {
    "": ("Basics", "Your timezone (used for quiet hours, daily budgets and the weekly evaluation) and where local data lives. "
         "API keys never go in this file: they stay in your environment or a git-ignored <code>.env</code>."),
    "sources": ("Where activity comes from", "Paths to the local databases behaviornudge reads, always read-only. Both Retraces are optional; "
                "use either or both. <code>retrace_repo</code> and <code>retrace_api</code> are for the launchd agent that keeps "
                "Retrace running. <code>sunsama_timer = \"mcp\"</code> reads your running Sunsama timer through Sunsama's official MCP server. "
                "<a href=\"capture.html\">More on the capture sources →</a>"),
    "sessions": ("How records become sessions", "Records of the same activity less than 30 s apart merge into one session. "
                 "Anything shorter than 3 s is treated as passing through, not as doing something."),
    "intent": ("What you were supposed to be doing", "Intent comes from your Sunsama timer, then a Sunsama timebox, then a calendar event, "
               "then your most recent creative work. Timers you forget to stop are capped at 45 min. Only the calendars you list here are read."),
    "drift": ("What counts as drift", "A drift episode starts with a purposeful visit to one of these sites. It counts as drift when at least "
              "3 min of off-task scrolling follow within 15 min, before you get back to work for at least 60 s. These settings drive the backtest."),
    "labeling": ("Creative, reactive or consuming", "Rules label most activity. TypeSafe Jev labels the unclear rest, pinned to one model version so backtests are repeatable. "
                 "Answers with a probability of at least 0.9 are accepted; ones below 0.5 go to your review queue. "
                 "<code>channel_only = true</code> means task titles are never sent, only their channel."),
    "labeling.modes": ("Define the modes in your own words", "These definitions are what Jev reads when it decides. Change them, and the labels follow."),
    "projects": ("Repos to Sunsama channels", "Maps a code repository's name to the Sunsama channel it belongs to, so work in a repo matches the task you planned."),
    "predictors": ("Which models predict drift", "Every predictor implements one interface, so you can switch them on and off here. "
                   "BehaviorGPT gets your last 50 sessions and returns its top 20 likely next activities."),
    "nudge": ("When nudges fire", "Every timing, the daily budget, cooldowns, snooze length and quiet hours. "
              "<code>too_soon_factor</code> is how much longer a nudge waits after you click <em>Too soon</em> (up to 4×). "
              "<code>call_allowlist</code> lists the apps whose microphone use counts as being in a call."),
    "nudge.sounds": ("A sound for each nudge", "Any macOS system sound name, the path to a sound file, or <code>\"none\"</code>. The in-call nudge is silent so nobody on the call hears it."),
}

DEMO = [
    ("daily_budget", "8", "50"), ("cooldown_min", "15", "1"), ("arrival_delay_s", "20", "8"),
    ("feed_arrival_delay_s", "45", "10"), ("purpose_done_delay_s", "30", "8"), ("off_task_threshold_min", "8", "0.5"),
    ("in_call_delay_s", "30", "10"), ("agent_delay_min", "2", "0.5"), ("calls_silence_others", "true", "false"),
]

TOKEN = re.compile(r'("(?:[^"\\]|\\.)*")|(\btrue\b|\bfalse\b|-?\d+(?:\.\d+)?)')


def _value(s: str) -> str:
    out, pos = [], 0
    for m in TOKEN.finditer(s):
        out.append(html.escape(s[pos:m.start()]))
        cls = "s" if m.group(1) else "n"
        out.append(f'<span class="{cls}">{html.escape(m.group(0))}</span>')
        pos = m.end()
    out.append(html.escape(s[pos:]))
    return "".join(out)


def _split_comment(line: str) -> tuple[str, str]:
    in_str = False
    for i, ch in enumerate(line):
        if ch == '"' and (i == 0 or line[i - 1] != "\\"):
            in_str = not in_str
        elif ch == "#" and not in_str:
            return line[:i], line[i:]
    return line, ""


def highlight(line: str) -> str:
    body, comment = _split_comment(line)
    com = f'<span class="c">{html.escape(comment)}</span>' if comment else ""
    if not body.strip():
        return com
    m = re.match(r"^(\s*)(\[[^\]]+\])(\s*)$", body)
    if m:
        return f'{m.group(1)}<span class="h">{html.escape(m.group(2))}</span>{m.group(3)}{com}'
    m = re.match(r'^(\s*)("[^"]+"|[A-Za-z0-9_.-]+)(\s*=\s*)(.*)$', body)
    if m:
        return f'{m.group(1)}<span class="k">{html.escape(m.group(2))}</span>{html.escape(m.group(3))}{_value(m.group(4))}{com}'
    return _value(body) + com


def sections(text: str) -> list[tuple[str, list[str]]]:
    lines = text.splitlines()
    while lines and lines[0].startswith("#"):
        lines.pop(0)  # the file's own header comment
    out: list[tuple[str, list[str]]] = [("", [])]
    for line in lines:
        m = re.match(r"^\[([^\]]+)\]", line)
        if m:
            out.append((m.group(1), []))
        out[-1][1].append(line)
    return [(name, _trim(body)) for name, body in out]


def _trim(body: list[str]) -> list[str]:
    while body and not body[-1].strip():
        body.pop()
    while body and not body[0].strip():
        body.pop(0)
    return body


def block(lines: list[str]) -> str:
    return '<pre class="toml"><code>' + "\n".join(highlight(x) for x in lines) + "</code></pre>"


def build() -> str:
    rows = []
    for name, body in sections(SRC.read_text()):
        title, note = NOTES.get(name, (name, ""))
        anchor = name.replace(".", "-") or "basics"
        label = f"[{name}]" if name else "top level"
        rows.append(f'''      <article class="row" id="{anchor}">
        <div class="note-col"><p class="sec">{html.escape(label)}</p><h2 class="row-title">{title}</h2><p>{note}</p></div>
        <div class="code-col">{block(body)}</div>
      </article>''')
    demo_lines = ["[nudge]"] + [f"{k} = {new}" for k, _, new in DEMO]
    demo_table = "\n".join(f"<tr><td><code>{k}</code></td><td>{old}</td><td>{new}</td></tr>" for k, old, new in DEMO)
    return PAGE.format(rows="\n".join(rows), demo=block(demo_lines), demo_table=demo_table)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>behaviornudge configuration</title>
<meta name="description" content="Every setting in behaviornudge.toml, explained: sources, intent, drift, labeling, predictors, and nudge timings and sounds.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Crect width='24' height='24' rx='6' fill='%232E5B86'/%3E%3Cpath d='M4 15.5c3.2 0 4.4-7 8-7s4.8 7 8 7' fill='none' stroke='white' stroke-width='2.2' stroke-linecap='round'/%3E%3C/svg%3E">
<link rel="stylesheet" href="site.css">
<style>
.toc {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 24px 0 0; padding: 0; list-style: none; }}
.toc a {{ display: inline-block; font: 13px/1 var(--mono); padding: 7px 10px; border-radius: 8px; border: 1px solid var(--line); background: var(--card); color: var(--ink-2); text-decoration: none; }}
.toc a:hover {{ border-color: var(--flow); color: var(--flow); }}
.row {{ display: grid; gap: 16px 32px; padding: 28px 0; border-top: 1px solid var(--line-2); scroll-margin-top: 16px; }}
.row:first-child {{ border-top: 0; padding-top: 0; }}
@media (min-width: 900px) {{ .row {{ grid-template-columns: minmax(0, 5fr) minmax(0, 7fr); }} .note-col {{ position: sticky; top: 16px; align-self: start; }} }}
.row > * {{ min-width: 0; }}
.row-title {{ font: 650 22px/1.25 var(--sans); letter-spacing: 0; margin: 0 0 8px; }}
.sec {{ font: 600 13px/1 var(--mono); color: var(--tok-sec); margin: 0 0 10px; }}
.note-col p {{ margin: 0; color: var(--ink-2); }}
.note-col p.sec {{ color: var(--tok-sec); }}
.demo {{ display: grid; gap: 24px; }}
@media (min-width: 900px) {{ .demo {{ grid-template-columns: 1fr 1fr; align-items: start; }} }}
table {{ width: 100%; border-collapse: collapse; font-size: 15px; }}
th, td {{ text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--line-2); }}
th {{ font-size: 13px; color: var(--ink-3); font-weight: 600; }}
td:nth-child(3) {{ color: var(--consuming); font-weight: 600; }}
.tablecard {{ padding: 8px 6px; overflow-x: auto; }}
</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <symbol id="mark" viewBox="0 0 24 24"><path d="M4 15.5c3.2 0 4.4-7 8-7s4.8 7 8 7" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/><path d="M15.5 5.5 19 8.5l-3.5 3" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" transform="translate(-1 -1)"/></symbol>
</svg>
<div class="wrap">
  <nav class="top" aria-label="Main">
    <a class="brand" href="./"><span class="logo"><svg width="17" height="17" aria-hidden="true"><use href="#mark"/></svg></span>behaviornudge</a>
    <ul>
      <li><a href="configuration.html" aria-current="page">Config</a></li>
      <li><a href="capture.html">Capture</a></li>
      <li class="opt"><a href="specs/#/specs">Specs</a></li>
      <li class="opt"><a href="architecture.html">Architecture</a></li>
      <li><a href="https://github.com/natea/behaviornudge">GitHub</a></li>
    </ul>
  </nav>
  <header class="page">
    <p class="eyebrow">Configuration</p>
    <h1>One file, every knob.</h1>
    <p class="lede">Everything behaviornudge decides is set in <code>behaviornudge.toml</code>: where your activity comes from, what counts as drift, how activity is labeled, which models predict, and when each nudge fires and how it sounds. Copy <code>behaviornudge.example.toml</code> to get started. Below is that file, section by section, with its real defaults.</p>
    <ul class="toc">
      <li><a href="#basics">top level</a></li><li><a href="#sources">[sources]</a></li><li><a href="#sessions">[sessions]</a></li>
      <li><a href="#intent">[intent]</a></li><li><a href="#drift">[drift]</a></li><li><a href="#labeling">[labeling]</a></li>
      <li><a href="#projects">[projects]</a></li><li><a href="#predictors">[predictors]</a></li><li><a href="#nudge">[nudge]</a></li>
      <li><a href="#nudge-sounds">[nudge.sounds]</a></li><li><a href="#demo">demo preset</a></li>
    </ul>
  </header>
</div>

<section>
  <div class="wrap">
{rows}
  </div>
</section>

<section id="demo">
  <div class="wrap">
    <h2>Example: a two-minute demo</h2>
    <p class="sub">To show nudges live on stage, shrink the waits from minutes to seconds and lift the daily budget. Change these lines, restart <code>behaviornudge nudge run</code>, and change them back afterwards.</p>
    <div class="demo">
      {demo}
      <div class="card tablecard"><table><thead><tr><th>setting</th><th>default</th><th>demo</th></tr></thead><tbody>
{demo_table}
      </tbody></table></div>
    </div>
  </div>
</section>

<footer>
  <div class="wrap">
    <p><a href="./">← behaviornudge</a> · <a href="capture.html">Capture sources</a> · <a href="https://github.com/natea/behaviornudge/blob/main/behaviornudge.example.toml">behaviornudge.example.toml on GitHub</a></p>
    <p>MIT © 2026 Nate Aune</p>
  </div>
</footer>
</body>
</html>
"""

if __name__ == "__main__":
    OUT.write_text(build())
    print(f"wrote {OUT.relative_to(ROOT)}")
