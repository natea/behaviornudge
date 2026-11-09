import json
import os
import signal
import sys
import textwrap
import threading
import time

from behaviornudge.nudge.helper import HelperClient

FAKE = textwrap.dedent('''
    import json, sys
    print(json.dumps({"event": "ready", "authorized": True}), flush=True)
    for line in sys.stdin:
        msg = json.loads(line)
        if msg.get("cmd") == "ping":
            print(json.dumps({"event": "pong"}), flush=True)
        elif msg.get("cmd") == "notify":
            print(json.dumps({"event": "action", "id": msg["id"], "action": "too_soon"}), flush=True)
        elif msg.get("cmd") == "config":
            print(json.dumps({"event": "configured", "n": len(msg.get("call_allowlist", []))}), flush=True)
''')


def make_fake(tmp_path):
    p = tmp_path / "fake_helper.py"
    p.write_text(FAKE)
    return [sys.executable, "-I", str(p)]


def wait_for(events, pred, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if any(pred(e) for e in list(events)):
            return True
        time.sleep(0.05)
    return False


def test_ready_ping_and_notify_action(tmp_path):
    events = []
    c = HelperClient(make_fake(tmp_path), events.append)
    c.start()
    assert wait_for(events, lambda e: e.get("event") == "ready")
    assert c.authorized is True
    assert c.send({"cmd": "ping"})
    assert wait_for(events, lambda e: e.get("event") == "pong")
    assert c.notify(42, "8 min on X", "Task at hand: Client tests", "Sosumi", "off_task")
    assert wait_for(events, lambda e: e.get("event") == "action" and e.get("id") == "42" and e.get("action") == "too_soon")
    c.stop()


def test_restarts_after_kill_and_replays_config(tmp_path):
    events, logs = [], []
    c = HelperClient(make_fake(tmp_path), events.append, log=logs.append, restart_delay_s=0.5)
    c.start()
    assert wait_for(events, lambda e: e.get("event") == "ready")
    c.configure(call_allowlist=["us.zoom.xos", "com.google.Chrome"])
    assert wait_for(events, lambda e: e.get("event") == "configured")
    os.kill(c._proc.pid, signal.SIGKILL)
    t0 = time.time()
    assert wait_for(events, lambda e: e.get("event") == "ready" and time.time() - t0 > 0, timeout=10)
    # a second ready plus a replayed config after restart, within 10 s
    assert wait_for(events, lambda e: True and sum(1 for x in events if x.get("event") == "configured") >= 2, timeout=10)
    assert c.restarts >= 1 and any("restarting" in m for m in logs)
    assert time.time() - t0 < 10
    c.stop()


def test_send_fails_cleanly_when_not_running(tmp_path):
    c = HelperClient(make_fake(tmp_path), lambda e: None)
    assert c.send({"cmd": "ping"}) is False  # never started


def test_bad_handler_does_not_kill_reader(tmp_path):
    events = []

    def handler(e):
        events.append(e)
        if e.get("event") == "ready":
            raise RuntimeError("boom")

    c = HelperClient(make_fake(tmp_path), handler)
    c.start()
    assert wait_for(events, lambda e: e.get("event") == "ready")
    c.send({"cmd": "ping"})
    assert wait_for(events, lambda e: e.get("event") == "pong")
    c.stop()
