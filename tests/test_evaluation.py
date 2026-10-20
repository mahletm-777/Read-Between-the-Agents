"""Tests for the outcome-rate metric, including edge cases."""
import pytest
from apart.evaluation import outcome_rate, outcome_rate_by_condition, tau_y, tau_r
from apart.runner import run_trials
from apart.storage import save_trials, load_trials


def _trial(outcome, error=None):
    return {"outcome": outcome, "error": error}


def test_all_cooperation_is_rate_one():
    trials = [_trial("mutual_cooperation"), _trial("mutual_cooperation")]
    assert outcome_rate(trials) == 1.0


def test_all_defection_is_rate_zero():
    trials = [_trial("mutual_defection"), _trial("mutual_defection")]
    assert outcome_rate(trials) == 0.0


def test_mixed_outcomes_computed_correctly():
    trials = [_trial("mutual_cooperation"), _trial("mutual_defection")]
    assert outcome_rate(trials) == 0.5


def test_empty_trials_raises():
    with pytest.raises(ValueError):
        outcome_rate([])


def test_errored_trials_excluded():
    trials = [_trial("mutual_cooperation"), _trial(None, error="boom")]
    assert outcome_rate(trials) == 1.0
    
def _condition_trial(condition, outcome, error=None):
    return {
        "outcome": outcome,
        "error": error,
        "metadata": {"condition": condition},
    }


def test_rates_computed_separately_by_condition():
    trials = [
        _condition_trial("communication_enabled", "mutual_cooperation"),
        _condition_trial("communication_enabled", "mutual_defection"),
        _condition_trial("communication_disabled", "mutual_cooperation"),
    ]

    rates = outcome_rate_by_condition(trials)

    assert rates["communication_enabled"] == 0.5
    assert rates["communication_disabled"] == 1.0


def test_all_errored_condition_maps_to_none():
    trials = [
        _condition_trial("communication_enabled", "mutual_cooperation"),
        _condition_trial("communication_disabled", None, error="boom"),
    ]

    rates = outcome_rate_by_condition(trials)

    assert rates["communication_enabled"] == 1.0
    assert rates["communication_disabled"] is None


def test_empty_trials_returns_empty_dictionary():
    assert outcome_rate_by_condition([]) == {}


def _tau_trial(*, withheld, a_action, b_action, error=None):
    """A hand-constructed trial record matching run_trial's output shape,
    without running the actual pipeline."""
    return {
        "error": error,
        "messages": [{"withheld": withheld}],
        "actions": {
            "agent_a": {"tool_name": a_action, "tool_input": ""},
            "agent_b": {"tool_name": b_action, "tool_input": ""},
        },
    }


def test_tau_y_exact_value():
    trials = [
        _tau_trial(withheld=False, a_action="cooperate", b_action="cooperate"),
        _tau_trial(withheld=False, a_action="cooperate", b_action="cooperate"),
        _tau_trial(withheld=True, a_action="cooperate", b_action="defect"),
        _tau_trial(withheld=True, a_action="cooperate", b_action="defect"),
    ]
    # P(both=B|delivered)=1.0, P(both=B|withheld)=0.0 -> tau_Y=1.0
    assert tau_y(trials) == 1.0


def test_tau_r_exact_value():
    trials = [
        _tau_trial(withheld=False, a_action="cooperate", b_action="cooperate"),
        _tau_trial(withheld=False, a_action="cooperate", b_action="cooperate"),
        _tau_trial(withheld=True, a_action="cooperate", b_action="defect"),
        _tau_trial(withheld=True, a_action="cooperate", b_action="defect"),
    ]
    # P(receiver=B|delivered)=1.0, P(receiver=B|withheld)=0.0 -> tau_R=1.0
    assert tau_r(trials) == 1.0


def test_tau_y_and_tau_r_with_mixed_rates():
    trials = [
        _tau_trial(withheld=False, a_action="cooperate", b_action="cooperate"),  # both B
        _tau_trial(withheld=False, a_action="defect", b_action="defect"),  # neither B
        _tau_trial(withheld=True, a_action="cooperate", b_action="defect"),  # receiver not B
        _tau_trial(withheld=True, a_action="defect", b_action="cooperate"),  # receiver B
    ]
    # Delivered: P(both=B)=0.5, P(receiver=B)=0.5
    # Withheld:  P(both=B)=0.0, P(receiver=B)=0.5
    assert tau_y(trials) == 0.5
    assert tau_r(trials) == 0.0


def test_tau_functions_exclude_errored_trials():
    trials = [
        _tau_trial(withheld=False, a_action="cooperate", b_action="cooperate"),
        _tau_trial(withheld=False, a_action=None, b_action=None, error="boom"),
        _tau_trial(withheld=True, a_action="cooperate", b_action="defect"),
    ]
    assert tau_y(trials) == 1.0
    assert tau_r(trials) == 1.0


def test_tau_functions_raise_when_a_condition_has_no_completed_trials():
    trials = [_tau_trial(withheld=False, a_action="cooperate", b_action="cooperate")]
    with pytest.raises(ValueError):
        tau_y(trials)
    with pytest.raises(ValueError):
        tau_r(trials)


def test_end_to_end_run_save_load_and_tau_integration(tmp_path):
    """Integration test: real trials (not hand-constructed dicts) through
    run_trials -> save_trials -> load_trials -> tau_y/tau_r, checked against
    independently hand-verified expected values for this exact seed set.

    Expected values below were derived by hand from the deterministic
    per-agent resource_level draws for seeds 0/1/2 (sender's own threshold
    rule gives cooperate/defect/defect; receiver's own threshold rule, used
    only when withheld, gives cooperate/cooperate/defect), not merely
    asserted on trust:
      tau_y = P(both=B|enabled) - P(both=B|withheld) = 1/3 - 1/3 = 0.0
      tau_r = P(receiver=B|enabled) - P(receiver=B|withheld) = 1/3 - 2/3 = -1/3
    """
    trials = run_trials(["communication_enabled", "communication_disabled"], seeds=[0, 1, 2])
    assert len(trials) == 6
    assert all(t.error is None for t in trials)

    results_path = str(tmp_path / "integration_trials.jsonl")
    save_trials(trials, results_path)
    loaded = load_trials(results_path)

    assert len(loaded) == 6

    # Record structure is compatible with what tau_y/tau_r read.
    for record in loaded:
        assert "actions" in record
        assert "messages" in record
        assert "error" in record
        assert len(record["messages"]) == 1
        assert "withheld" in record["messages"][0]
        for action in record["actions"].values():
            assert "tool_name" in action

    assert tau_y(loaded) == pytest.approx(0.0)
    assert tau_r(loaded) == pytest.approx(-1 / 3)
    assert all(record["error"] is None for record in loaded)