from datetime import UTC, datetime

import pytest

from behaviornudge.egress import (
    AuditLog,
    EgressViolation,
    build_payload,
    history_payload,
    send,
)


def test_allowed_fields_pass_and_are_trimmed():
    p = build_payload({"app": "Chrome", "title": "x" * 200, "caption": "y" * 900, "channel": "ClientCo"})
    assert p["app"] == "Chrome"
    assert len(p["title"]) == 80
    assert len(p["caption"]) == 500
    assert p["channel"] == "ClientCo"


@pytest.mark.parametrize("field", ["text", "screen_text", "url", "html", "screenshot", "page_text"])
def test_forbidden_fields_rejected(field):
    with pytest.raises(EgressViolation):
        build_payload({field: "secret stuff"})


def test_unknown_field_rejected():
    with pytest.raises(EgressViolation):
        build_payload({"email_body": "hello"})


def test_urls_and_query_strings_scrubbed_from_values():
    p = build_payload({"title": "Feed | https://www.linkedin.com/feed/?trk=abc LinkedIn", "caption": "see www.x.com/home now"})
    assert "http" not in p["title"] and "trk=" not in p["title"]
    assert "www." not in p["caption"]


def test_channel_only_drops_task_title():
    p = build_payload({"channel": "Friends", "task_title": "Friend re weekend plans"}, channel_only=True)
    assert p == {"channel": "Friends"}


def test_none_values_omitted():
    assert build_payload({"title": None, "app": "Zed"}) == {"app": "Zed"}


def test_history_payload_keys_and_times_only():
    ts = datetime(2026, 11, 8, 18, 46, tzinfo=UTC)
    out = history_payload([("linkedin.com/messaging", ts)])
    assert out == [{"activity_key": "linkedin.com/messaging", "ts": "2026-11-08T18:46:00+00:00"}]
    with pytest.raises(EgressViolation):
        history_payload([("https://x.com/home?s=1", ts)])


def test_audit_log_records_payload_without_secret(tmp_path, monkeypatch):
    secret = "sk-test-SECRET-123"
    monkeypatch.setenv("TYPESAFE_API_KEY", secret)
    audit = AuditLog(tmp_path / "audit.jsonl")
    payload = build_payload({"app": "Chrome", "activity_key": "x.com/home"})
    result = send(audit, "typesafe:/v1/systemone", payload, lambda: "ok")
    assert result == "ok"
    entries = audit.entries()
    assert len(entries) == 1
    assert entries[0]["destination"] == "typesafe:/v1/systemone"
    assert entries[0]["payload"] == payload
    assert secret not in (tmp_path / "audit.jsonl").read_text()
