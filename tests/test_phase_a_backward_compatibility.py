"""Behavioral regression tests confirming Phase A's addition does not
change legacy behavior. These deliberately duplicate a subset of existing
assertions from test_conditions.py and test_exhaustive.py, scoped
specifically to this change -- if Phase A's presence ever altered legacy
behavior, this file catches it independently of the original tests."""
import pytest

from apart.conditions import ALL_CONDITIONS, build_message, get_condition
from apart.exhaustive import run_exhaustive_trials, summarize_exhaustive


def test_legacy_all_conditions_unchanged():
    assert ALL_CONDITIONS == ("communication_enabled", "communication_disabled")


def test_legacy_condition_behavior_unchanged():
    enabled = get_condition("communication_enabled")
    message = build_message("agent_a", "agent_b", "cooperate", enabled)
    assert message.withheld is False
    assert message.message_type == "deliver"

    disabled = get_condition("communication_disabled")
    message = build_message("agent_a", "agent_b", "cooperate", disabled)
    assert message.withheld is True
    assert message.message_type == "withheld"


def test_legacy_exhaustive_baseline_unchanged():
    trials = run_exhaustive_trials()
    assert len(trials) == 242
    summary = summarize_exhaustive(trials)
    assert summary["total_trials"] == 242
    assert summary["total_pairs"] == 121
    rates = summary["mutual_cooperation_rate_by_condition"]
    assert rates["communication_enabled"] == pytest.approx(55 / 121)
    assert rates["communication_disabled"] == pytest.approx(25 / 121)
    assert summary["tau_y"] == pytest.approx(30 / 121)
    assert summary["tau_r"] == pytest.approx(0.0)
    assert summary["action_change_count"] == 60
    assert summary["outcome_change_count"] == 60
