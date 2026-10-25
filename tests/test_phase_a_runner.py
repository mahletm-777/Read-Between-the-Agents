"""Tests for Phase A trial orchestration: pairing, condition semantics,
and the end-to-end pipeline using a deterministic fake receiver."""
import pytest

from apart.phase_a_conditions import INFORMATIVE_MESSAGE, NEUTRAL_MESSAGE, NO_MESSAGE
from apart.phase_a_receiver import DeterministicFakeReceiver
from apart.phase_a_runner import group_by_scenario, run_phase_a_trial, run_phase_a_trials_for_scenario


def test_single_trial_no_message_withholds_and_records_no_delivery():
    receiver = DeterministicFakeReceiver({("s1", NO_MESSAGE): "A"})
    result = run_phase_a_trial(
        scenario_id="s1", condition_name=NO_MESSAGE, sender_action="B", receiver_fn=receiver.respond,
    )
    assert result.message_delivered is False
    assert result.error is None
    assert result.validity == "valid"
    assert result.parsed_action == "A"


def test_pairing_completeness_raises_when_a_condition_is_missing():
    trials = run_phase_a_trials_for_scenario(
        scenario_id="s1",
        sender_action="B",
        receiver_fn=DeterministicFakeReceiver(
            {("s1", NO_MESSAGE): "A", ("s1", NEUTRAL_MESSAGE): "A", ("s1", INFORMATIVE_MESSAGE): "B"}
        ).respond,
    )
    incomplete = [t for t in trials if t.metadata.condition != INFORMATIVE_MESSAGE]
    with pytest.raises(ValueError):
        group_by_scenario(incomplete)


def test_end_to_end_condition_semantics_receiver_outcomes_logging_and_pairing():
    """End-to-end: one scenario's predefined outcomes across all three
    conditions (including one ambiguous and one invalid output), run
    through the full pipeline, checking message delivery, receiver
    outcomes, logging completeness, and pairing together."""
    outcomes = {
        ("s1", NO_MESSAGE): "B",            # valid
        ("s1", NEUTRAL_MESSAGE): "maybe B",  # ambiguous (hedged)
        ("s1", INFORMATIVE_MESSAGE): "",     # invalid (empty)
    }
    receiver = DeterministicFakeReceiver(outcomes)
    trials = run_phase_a_trials_for_scenario(
        scenario_id="s1", sender_action="B", receiver_fn=receiver.respond,
    )
    by_condition = {t.metadata.condition: t for t in trials}

    # Condition-specific message delivery.
    assert by_condition[NO_MESSAGE].message_delivered is False
    assert by_condition[NEUTRAL_MESSAGE].message_delivered is True
    assert "B" not in by_condition[NEUTRAL_MESSAGE].message_content
    assert by_condition[INFORMATIVE_MESSAGE].message_delivered is True
    assert by_condition[INFORMATIVE_MESSAGE].message_content == "B"

    # Receiver outcomes (predefined, returned verbatim) and parsing.
    assert by_condition[NO_MESSAGE].receiver_raw_output == "B"
    assert by_condition[NO_MESSAGE].validity == "valid"
    assert by_condition[NO_MESSAGE].parsed_action == "B"

    assert by_condition[NEUTRAL_MESSAGE].receiver_raw_output == "maybe B"
    assert by_condition[NEUTRAL_MESSAGE].validity == "ambiguous"
    assert by_condition[NEUTRAL_MESSAGE].reason == "hedged_or_unresolved"

    assert by_condition[INFORMATIVE_MESSAGE].receiver_raw_output == ""
    assert by_condition[INFORMATIVE_MESSAGE].validity == "invalid"
    assert by_condition[INFORMATIVE_MESSAGE].reason == "empty_input"

    # Trial logging completeness.
    for t in trials:
        assert t.metadata.scenario_id == "s1"
        assert t.metadata.condition in (NO_MESSAGE, NEUTRAL_MESSAGE, INFORMATIVE_MESSAGE)
        assert t.sender_action == "B"
        assert t.error is None

    # Pairing.
    by_scenario = group_by_scenario(trials)
    assert set(by_scenario.keys()) == {"s1"}
    assert set(by_scenario["s1"].keys()) == {NO_MESSAGE, NEUTRAL_MESSAGE, INFORMATIVE_MESSAGE}


def test_receiver_exception_is_recorded_as_error_not_raised():
    def failing_receiver(scenario_id, condition_name, delivered_message):
        raise RuntimeError("boom")

    result = run_phase_a_trial(
        scenario_id="s1", condition_name=NO_MESSAGE, sender_action="B", receiver_fn=failing_receiver,
    )
    assert result.error == "boom"
    assert result.validity is None


# --- Regression tests for stage-tagged, fail-closed exclusion accounting.
#
# Confirmed defect (fixed 2026-10-22): every exception -- including an unknown
# condition name -- was recorded identically, so per-condition exclusion counts
# could misattribute a configuration problem to the receiver.


def test_receiver_failure_records_stage_receiver():
    def failing_receiver(scenario_id, condition_name, delivered_message):
        raise RuntimeError("boom")

    result = run_phase_a_trial(
        scenario_id="s1", condition_name=NO_MESSAGE, sender_action="B", receiver_fn=failing_receiver,
    )
    assert result.error == "boom"
    assert result.error_stage == "receiver"


def test_unknown_condition_is_recorded_fail_closed_with_stage_condition():
    result = run_phase_a_trial(
        scenario_id="s1",
        condition_name="not_a_condition",
        sender_action="B",
        receiver_fn=lambda *_: "B",
    )
    # Fail closed: a record is still returned, never an exception, and the stage
    # says the condition lookup failed rather than blaming the receiver.
    assert result.error is not None
    assert "not_a_condition" in result.error
    assert result.error_stage == "condition"
    assert result.validity is None


def test_successful_trial_has_no_error_stage():
    receiver = DeterministicFakeReceiver({("s1", NO_MESSAGE): "A"})
    result = run_phase_a_trial(
        scenario_id="s1", condition_name=NO_MESSAGE, sender_action="B", receiver_fn=receiver.respond,
    )
    assert result.error is None
    assert result.error_stage is None
