"""Tests for the exhaustive state-space validation (apart.exhaustive).

Expected values below were derived BY HAND from the agent rules before any
code was written (see docs/decision_log.md for the derivation):
  - resource_level in {0,...,10}: 6 values <=5 (defect), 5 values >5 (cooperate).
  - Enabled: receiver always copies sender -> both agents' action = sender's
    action -> outcome can only be mutual_cooperation or mutual_defection,
    never mixed.
  - Mutual-cooperation rate (enabled) = P(a>5) = 55/121.
  - Mutual-cooperation rate (disabled) = P(a>5 and b>5) = 25/121.
  - Receiver-cooperation rate = P(a>5) = 55/121 (enabled, via copy) and
    P(b>5) = 55/121 (disabled, own rule) -- identical by symmetry (a and b
    are drawn from the same range with the same threshold), so tau_R = 0
    exactly, not approximately.
  - tau_Y = 55/121 - 25/121 = 30/121.
  - action_change_count = outcome_change_count = 60/121 (pairs where
    (a>5) != (b>5): 121 - (5*5 + 6*6) = 60).
These are independently-derived expectations, not values read off the code.
"""
import pytest

from apart.exhaustive import (
    enumerate_pairs,
    run_exhaustive_trials,
    action_change_count,
    outcome_change_count,
    summarize_exhaustive,
)
from apart.conditions import ALL_CONDITIONS


def test_enumerate_pairs_covers_full_state_space_uniquely():
    pairs = enumerate_pairs()
    assert len(pairs) == 121
    assert len(set(pairs)) == 121  # no duplicates
    assert set(pairs) == {(a, b) for a in range(11) for b in range(11)}


def test_run_exhaustive_trials_produces_242_trials_121_per_condition():
    trials = run_exhaustive_trials()
    assert len(trials) == 242
    assert all(t.error is None for t in trials)
    for condition in ALL_CONDITIONS:
        assert sum(1 for t in trials if t.metadata.condition == condition) == 121


def test_pairing_same_pair_index_appears_once_per_condition():
    trials = run_exhaustive_trials()
    by_pair: dict = {}
    for t in trials:
        by_pair.setdefault(t.metadata.seed, set()).add(t.metadata.condition)
    assert len(by_pair) == 121
    assert all(conditions == set(ALL_CONDITIONS) for conditions in by_pair.values())


def test_same_pair_gives_identical_observations_across_conditions():
    """The sender's action (derived from resource_level_a) must be identical
    across conditions for the same pair -- identical exogenous inputs."""
    trials = run_exhaustive_trials()
    by_pair: dict = {}
    for t in trials:
        by_pair.setdefault(t.metadata.seed, {})[t.metadata.condition] = t
    for pair_index, by_condition in by_pair.items():
        sender_actions = {t.actions["agent_a"].tool_name for t in by_condition.values()}
        assert len(sender_actions) == 1


def test_no_mixed_outcomes_under_communication_enabled():
    trials = run_exhaustive_trials()
    enabled = [t for t in trials if t.metadata.condition == "communication_enabled"]
    assert len(enabled) == 121
    assert all(t.outcome in ("mutual_cooperation", "mutual_defection") for t in enabled)
    assert not any(t.outcome == "mixed" for t in enabled)


def test_exact_mutual_cooperation_rates_by_condition():
    trials = run_exhaustive_trials()
    summary = summarize_exhaustive(trials)
    rates = summary["mutual_cooperation_rate_by_condition"]
    assert rates["communication_enabled"] == pytest.approx(55 / 121)
    assert rates["communication_disabled"] == pytest.approx(25 / 121)


def test_exact_tau_y_and_tau_r():
    trials = run_exhaustive_trials()
    summary = summarize_exhaustive(trials)
    assert summary["tau_y"] == pytest.approx(30 / 121)
    assert summary["tau_r"] == pytest.approx(0.0)


def test_exact_action_and_outcome_change_counts():
    trials = run_exhaustive_trials()
    assert action_change_count(trials) == 60
    assert outcome_change_count(trials) == 60


def test_summarize_exhaustive_reports_denominators_and_totals():
    trials = run_exhaustive_trials()
    summary = summarize_exhaustive(trials)
    assert summary["total_trials"] == 242
    assert summary["total_pairs"] == 121


def test_action_change_count_raises_if_a_pair_is_missing_a_condition():
    trials = run_exhaustive_trials()
    incomplete = [t for t in trials if not (t.metadata.seed == 0 and t.metadata.condition == "communication_disabled")]
    with pytest.raises(ValueError):
        action_change_count(incomplete)
