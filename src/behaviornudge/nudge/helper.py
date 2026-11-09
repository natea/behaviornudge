"""Build and talk to BehaviorNudgeHelper.app (design D1–D3).

`build_helper` compiles helper/BehaviorNudgeHelper.swift into a small, ad-hoc-signed app
bundle under data/helper/, rebuilding only when the sources (or bundled custom sounds) change.
`HelperClient` spawns the bundle's executable directly (so stdin/stdout stay connected),
dispatches its JSON-line events to callbacks, and restarts it if it exits.
"""

from __future__ import annotations

import hashlib
import json
import plistlib
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

BUNDLE_ID = "com.behaviornudge.helper"
APP_NAME = "BehaviorNudgeHelper"
REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE = REPO_ROOT / "helper" / f"{APP_NAME}.swift"


def app_paths(data_dir: Path) -> tuple[Path, Path]:
    app = Path(data_dir) / "helper" / f"{APP_NAME}.app"
    return app, app / "Contents" / "MacOS" / APP_NAME


def _source_hash(source: Path, sounds: list[Path]) -> str:
    h = hashlib.sha256(source.read_bytes())
    for p in sorted(sounds):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def custom_sound_files(sounds: dict[str, str]) -> list[Path]:
    """Sound settings that point at files (bundled into the app so macOS can play them)."""
    out = []
    for v in sounds.values():
        if v and ("/" in v or v.startswith("~")):
            p = Path(v).expanduser()
            if p.exists():
                out.append(p)
    return out


def build_helper(data_dir: Path, sounds: dict[str, str] | None = None, source: Path = SOURCE, force: bool = False) -> dict:
    app, exe = app_paths(data_dir)
    files = custom_sound_files(sounds or {})
    digest = _source_hash(source, files)
    stamp = app / "Contents" / "Resources" / "source.hash"
    if not force and exe.exists() and stamp.exists() and stamp.read_text() == digest:
        return {"app": str(app), "built": False, "hash": digest}
    if app.exists():
        shutil.rmtree(app)
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Resources").mkdir(parents=True)
    res = subprocess.run(["swiftc", "-O", "-swift-version", "5", "-o", str(exe), str(source)],
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"swiftc failed:\n{res.stderr[-2000:]}")
    plist = {
        "CFBundleIdentifier": BUNDLE_ID, "CFBundleName": "behaviornudge", "CFBundleDisplayName": "behaviornudge",
        "CFBundleExecutable": APP_NAME, "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "0.1",
        "CFBundleVersion": "1", "LSUIElement": True, "LSMinimumSystemVersion": "14.2",
        "NSUserNotificationAlertStyle": "alert",
    }
    with (app / "Contents" / "Info.plist").open("wb") as f:
        plistlib.dump(plist, f)
    if files:
        snd = app / "Contents" / "Library" / "Sounds"
        snd.mkdir(parents=True)
        for p in files:
            shutil.copy2(p, snd / p.name)
    stamp.write_text(digest)
    sig = subprocess.run(["codesign", "--force", "--sign", "-", "--identifier", BUNDLE_ID, str(app)],
                         capture_output=True, text=True)
    if sig.returncode != 0:
        raise RuntimeError(f"codesign failed: {sig.stderr.strip()}")
    return {"app": str(app), "built": True, "hash": digest}


EventHandler = Callable[[dict], None]


class HelperClient:
    """Runs the helper and routes its events. Restarts it (with backoff) if it exits."""

    def __init__(self, command: list[str], on_event: EventHandler, log: Callable[[str], None] = lambda m: None,
                 restart_delay_s: float = 2.0):
        self.command = command
        self.on_event = on_event
        self.log = log
        self.restart_delay_s = restart_delay_s
        self.authorized: bool | None = None
        self.restarts = 0
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._config: dict | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._supervise, daemon=True, name="helper-supervisor")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()

    def configure(self, **cfg) -> None:
        self._config = {"cmd": "config", **cfg}
        self.send(self._config)

    def send(self, msg: dict) -> bool:
        with self._lock:
            p = self._proc
            if not p or p.poll() is not None or not p.stdin:
                return False
            try:
                p.stdin.write(json.dumps(msg) + "\n")
                p.stdin.flush()
                return True
            except (BrokenPipeError, ValueError):
                return False

    def notify(self, nid: int | str, title: str, body: str, sound: str | None, nudge_type: str) -> bool:
        return self.send({"cmd": "notify", "id": str(nid), "title": title, "body": body,
                          "sound": sound or "none", "type": nudge_type})

    def _spawn(self) -> subprocess.Popen:
        p = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, bufsize=1)
        with self._lock:
            self._proc = p
        if self._config:
            self.send(self._config)
        return p

    def _supervise(self) -> None:
        delay = self.restart_delay_s
        while not self._stop.is_set():
            try:
                p = self._spawn()
            except OSError as e:
                self.log(f"helper failed to start: {e}")
                self._stop.wait(delay)
                continue
            started = time.monotonic()
            assert p.stdout is not None
            for line in p.stdout:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("event") == "ready":
                    self.authorized = bool(ev.get("authorized"))
                try:
                    self.on_event(ev)
                except Exception as e:  # a bad handler must not kill the reader
                    self.log(f"event handler error: {e}")
            p.wait()
            if self._stop.is_set():
                break
            self.restarts += 1
            self.log(f"helper exited (code {p.returncode}); restarting")
            # back off only if it keeps dying quickly
            delay = self.restart_delay_s if time.monotonic() - started > 30 else min(delay * 2, 8.0)
            self._stop.wait(delay)
