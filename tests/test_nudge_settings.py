from pathlib import Path

import pytest

from behaviornudge.config import load_config
from behaviornudge.nudge.settings import NudgeSettings, load_settings

ROOT = Path(__file__).resolve().parents[1]


def test_defaults_without_section():
    s = load_settings(None)
    assert s.daily_budget == 8 and s.base_delay_s("off_task") == 480
    assert s.sound("in_call") is None and s.sound("off_task") == "Sosumi"


def test_example_config_matches_defaults():
    cfg = load_config(ROOT / "behaviornudge.example.toml")
    s = load_settings(cfg.section("nudge"))
    d = NudgeSettings()
    assert s.base_delay_s("arrival") == d.base_delay_s("arrival") == 20
    assert s.sounds == d.sounds and s.call_allowlist == d.call_allowlist


def test_user_override_takes_effect():
    s = load_settings({"off_task_threshold_min": 12, "sounds": {"off_task": "Basso", "arrival": "none"}})
    assert s.base_delay_s("off_task") == 720
    assert s.sound("off_task") == "Basso" and s.sound("arrival") is None
    assert s.sound("purpose_done") == "Glass"  # untouched defaults survive


def test_unknown_setting_rejected():
    with pytest.raises(ValueError):
        load_settings({"dailly_budget": 3})
