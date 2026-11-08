from pathlib import Path

import pytest

from behaviornudge.config import MissingSecretError, get_secret, load_config

ROOT = Path(__file__).resolve().parents[1]


def test_missing_key_names_the_variable(monkeypatch, tmp_path):
    monkeypatch.delenv("UNBOXAI_API_KEY", raising=False)
    with pytest.raises(MissingSecretError) as e:
        get_secret("UNBOXAI_API_KEY", env_file=tmp_path / "nope.env")
    assert "UNBOXAI_API_KEY" in str(e.value)


def test_key_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    f = tmp_path / ".env"
    f.write_text("# comment\nTYPESAFE_API_KEY=abc123\nEMPTY=\n")
    assert get_secret("TYPESAFE_API_KEY", env_file=f) == "abc123"


def test_empty_value_counts_as_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("UNBOXAI_API_KEY", raising=False)
    f = tmp_path / ".env"
    f.write_text("UNBOXAI_API_KEY=\n")
    with pytest.raises(MissingSecretError):
        get_secret("UNBOXAI_API_KEY", env_file=f)


def test_example_config_loads():
    cfg = load_config(ROOT / "behaviornudge.example.toml")
    assert cfg.timezone == "America/New_York"
    assert cfg.section("intent")["calendar_allowlist"]
    assert cfg.section("labeling")["classifier_model"] == "jev-1.14.0"
    assert set(cfg.section("labeling")["modes"]) == {"creative", "reactive", "consuming"}


def test_falls_back_to_example(tmp_path):
    (tmp_path / "behaviornudge.example.toml").write_text('timezone = "Europe/Oslo"\n')
    assert load_config(tmp_path / "behaviornudge.toml").timezone == "Europe/Oslo"
