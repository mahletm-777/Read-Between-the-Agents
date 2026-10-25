"""Unit tests for the Phase A -> Trace converter.

Covers: scenario-only deterministic seed, opaque unique trace IDs, the three
delivery conditions, strict exclusion of ambiguous/invalid/failed/rejected
trials, delivery-consistency rejection, and leakage (condition/scenario must
not appear in rendered monitor views).
"""

from collections import Counter

from apart.phase_a_conditions import (
    INFORMATIVE_MESSAGE,
    NEUTRAL_MESSAGE,
    NO_MESSAGE,
)
from apart.phase_a_models import PhaseATrialMetadata, PhaseATrialResult
from apart.phase_a_to_trace import (
    convert_many,
    convert_phase_a_result,
    derive_scenario_pairing_key,
    derive_seed,
    derive_trace_id,
)
from apart.schema import DeliveryCondition, Message

from monitors.observable import observable
from monitors.prompts import render_view

NEUTRAL_CONTENT = (
    "[neutral message: designed not to reveal the sender's action; this "
    "placeholder content is not evidence that a real neutral message "
    "conveys no information]"
)


def make_result(
    scenario_id="scenario-alpha",
    condition=NO_MESSAGE,
    *,
    sender_action="B",
    message_content="",
    message_delivered=False,
    parsed_action="A",
    validity="valid",
    reason=None,
    error=None,
    error_stage=None,
    receiver_raw_output=None,
):
    metadata = PhaseATrialMetadata(
        scenario_id=scenario_id,
        condition=condition,
        trial_id=f"{scenario_id}_{condition}",
    )
    return PhaseATrialResult(
        metadata=metadata,
        sender_action=sender_action,
        message_content=message_content,
        message_delivered=message_delivered,
        receiver_raw_output=(
            receiver_raw_output if receiver_raw_output is not None else parsed_action
        ),
        parsed_action=parsed_action,
        validity=validity,
        reason=reason,
        error=error,
        error_stage=error_stage,
    )


def test_derive_seed_is_scenario_only_and_deterministic():
    assert derive_seed("scenario-alpha") == derive_seed("scenario-alpha")
    assert isinstance(derive_seed("scenario-alpha"), int)


def test_three_conditions_map_to_traces_with_shared_seed_and_unique_ids():
    no_msg = make_result(condition=NO_MESSAGE)
    neutral = make_result(
        condition=NEUTRAL_MESSAGE,
        message_content=NEUTRAL_CONTENT,
        message_delivered=True,
        parsed_action="A",
    )
    informative = make_result(
        condition=INFORMATIVE_MESSAGE,
        message_content="B",
        message_delivered=True,
        parsed_action="B",
    )

    for res in (no_msg, neutral, informative):
        c = convert_phase_a_result(res)
        assert c.status == "valid", c.failures
        assert c.trace is not None
        c.trace.validate()

    seeds = {convert_phase_a_result(r).trace.seed for r in (no_msg, neutral, informative)}
    assert len(seeds) == 1  # scenario-only seed shared across conditions

    ids = {convert_phase_a_result(r).trace.trace_id for r in (no_msg, neutral, informative)}
    assert len(ids) == 3  # unique per (scenario, condition)


def test_condition_maps_to_delivery_and_message_semantics():
    c = convert_phase_a_result(make_result(condition=NO_MESSAGE))
    assert c.trace.delivery_condition is DeliveryCondition.NO_MSG
    assert c.trace.messages == []

    c = convert_phase_a_result(
        make_result(
            condition=NEUTRAL_MESSAGE,
            message_content=NEUTRAL_CONTENT,
            message_delivered=True,
        )
    )
    assert c.trace.delivery_condition is DeliveryCondition.NEUTRAL_MSG
    assert c.trace.messages == [Message("agent_1", "agent_2", NEUTRAL_CONTENT)]

    c = convert_phase_a_result(
        make_result(
            condition=INFORMATIVE_MESSAGE,
            message_content="B",
            message_delivered=True,
            parsed_action="B",
        )
    )
    assert c.trace.delivery_condition is DeliveryCondition.INFORM_MSG
    assert c.trace.messages == [Message("agent_1", "agent_2", "B")]
    assert c.trace.raw_actions == {"agent_1": "B", "agent_2": "B"}


def test_ambiguous_trial_is_excluded_from_scoring():
    c = convert_phase_a_result(
        make_result(
            condition=NEUTRAL_MESSAGE,
            message_content=NEUTRAL_CONTENT,
            message_delivered=True,
            parsed_action=None,
            validity="ambiguous",
            reason="hedged_or_unresolved",
            receiver_raw_output="maybe B",
        )
    )
    assert c.status == "ambiguous"
    assert c.trace is None
    assert c.provenance.validity == "ambiguous"
    assert c.provenance.receiver_raw_output is not None


def test_invalid_trial_is_excluded():
    c = convert_phase_a_result(
        make_result(
            condition=NO_MESSAGE,
            parsed_action=None,
            validity="invalid",
            reason="empty_input",
        )
    )
    assert c.status == "invalid"
    assert c.trace is None


def test_failed_trial_is_excluded():
    c = convert_phase_a_result(make_result(error="boom"))
    assert c.status == "failed"
    assert c.trace is None
    assert c.provenance.error == "boom"


def test_rejected_unknown_condition():
    c = convert_phase_a_result(make_result(condition="bogus"))
    assert c.status == "rejected"
    assert c.trace is None


def test_rejected_non_ab_sender_action():
    c = convert_phase_a_result(make_result(condition=NO_MESSAGE, sender_action="C"))
    assert c.status == "rejected"
    assert c.trace is None


def test_rejected_inconsistent_delivery():
    c = convert_phase_a_result(
        make_result(condition=NEUTRAL_MESSAGE, message_content="", message_delivered=False)
    )
    assert c.status == "rejected"
    assert c.trace is None


def test_conversion_is_deterministic():
    res = make_result(
        condition=INFORMATIVE_MESSAGE,
        message_content="B",
        message_delivered=True,
        parsed_action="B",
    )
    a = convert_phase_a_result(res)
    b = convert_phase_a_result(res)
    assert a.status == b.status == "valid"
    assert a.trace.to_dict() == b.trace.to_dict()
    assert derive_trace_id("scenario-alpha", INFORMATIVE_MESSAGE) == \
        derive_trace_id("scenario-alpha", INFORMATIVE_MESSAGE)


def test_convert_many_partitions_valid_and_sidecar():
    valid = make_result(condition=NO_MESSAGE)
    ambiguous = make_result(
        condition=NO_MESSAGE,
        parsed_action=None,
        validity="ambiguous",
        reason="no_recognizable_token",
    )
    traces, converted = convert_many([valid, ambiguous])
    assert [c.status for c in converted] == ["valid", "ambiguous"]
    assert len(traces) == 1
    assert traces[0].delivery_condition is DeliveryCondition.NO_MSG


def test_no_condition_or_scenario_leak_in_rendered_views():
    res = make_result(
        condition=NEUTRAL_MESSAGE,
        message_content=NEUTRAL_CONTENT,
        message_delivered=True,
        parsed_action="A",
    )
    c = convert_phase_a_result(res)
    assert c.status == "valid"
    trace = c.trace

    record = observable(trace)
    rendered = {
        view: render_view(record, view, "case-test")
        for view in ("M1", "M2", "M3", "M4")
    }
    forbidden = [
        "scenario-alpha",
        "no_message", "neutral_message", "informative_message",
        "NO_MSG", "NEUTRAL_MSG", "INFORM_MSG",
        str(trace.seed),
        trace.trace_id,
        "delivery_condition", "claims", "template_ids", "private_plans",
    ]
    for view, text in rendered.items():
        for token in forbidden:
            assert token not in text, f"{view} leaked {token!r}: {text!r}"

    assert "chose" not in rendered["M1"]
    assert "[Not shown in this record]" in rendered["M1"]
    assert NEUTRAL_CONTENT not in rendered["M2"]
    assert "[Not shown in this record]" in rendered["M2"]


# --- Regression tests for conversion integrity and provenance metadata.
#
# Confirmed defects (fixed 2026-10-22): every exception was labelled "receiver
# error", and the provenance carried no record of the deterministic pairing key,
# so an auditor could not tell it apart from a random-number-generator seed.

CLOSED_STATUSES = {"valid", "ambiguous", "invalid", "failed", "rejected"}


def test_every_trial_gets_an_explicit_status_and_no_trial_is_dropped():
    trials = [
        make_result(condition=NO_MESSAGE),  # valid
        make_result(
            condition=NO_MESSAGE, parsed_action=None, validity="ambiguous",
            reason="no_recognizable_token",
        ),
        make_result(
            condition=NO_MESSAGE, parsed_action=None, validity="invalid",
            reason="empty_input",
        ),
        make_result(condition=NO_MESSAGE, error="boom", error_stage="receiver"),  # failed
        make_result(condition="bogus"),  # rejected: unknown condition
        make_result(condition=NO_MESSAGE, sender_action="C"),  # rejected: bad sender action
        make_result(  # rejected: delivery inconsistent with the condition
            condition=NEUTRAL_MESSAGE, message_content="", message_delivered=False,
            parsed_action=None, validity=None,
        ),
    ]

    traces, converted = convert_many(trials)

    assert len(converted) == len(trials)  # nothing is silently dropped
    assert [c.status for c in converted] == [
        "valid", "ambiguous", "invalid", "failed", "rejected", "rejected", "rejected",
    ]
    assert len(traces) == 1  # only the valid trial reaches monitor scoring

    for c in converted:
        assert c.status in CLOSED_STATUSES
        if c.status == "valid":
            assert c.trace is not None
        else:
            assert c.trace is None
            assert c.failures, "every exclusion must carry a reason"


def test_missingness_and_exclusions_are_reportable_by_condition():
    trials = (
        [make_result(scenario_id=f"s{i}", condition=NO_MESSAGE) for i in range(3)]
        + [
            make_result(
                scenario_id=f"t{i}", condition=NEUTRAL_MESSAGE, parsed_action=None,
                validity="ambiguous", reason="hedged_or_unresolved",
            )
            for i in range(2)
        ]
        + [
            make_result(
                scenario_id="u0", condition=INFORMATIVE_MESSAGE, message_content="B",
                message_delivered=True, parsed_action="B",
            )
        ]
    )
    _, converted = convert_many(trials)

    by_condition = Counter(c.provenance.condition for c in converted)
    by_condition_status = Counter((c.provenance.condition, c.status) for c in converted)

    assert by_condition == {NO_MESSAGE: 3, NEUTRAL_MESSAGE: 2, INFORMATIVE_MESSAGE: 1}
    assert by_condition_status[(NO_MESSAGE, "valid")] == 3
    assert by_condition_status[(NEUTRAL_MESSAGE, "ambiguous")] == 2
    assert by_condition_status[(INFORMATIVE_MESSAGE, "valid")] == 1
    # Denominators reconcile: every submitted trial is counted exactly once.
    assert sum(by_condition.values()) == len(trials)
    assert sum(by_condition_status.values()) == len(trials)


def test_failed_trial_error_stage_is_preserved_for_auditing():
    receiver_failure = convert_phase_a_result(
        make_result(error="boom", error_stage="receiver")
    )
    assert receiver_failure.status == "failed"
    assert receiver_failure.provenance.error_stage == "receiver"
    assert receiver_failure.failures == ["receiver error: boom"]

    condition_failure = convert_phase_a_result(
        make_result(error="bad condition", error_stage="condition")
    )
    assert condition_failure.provenance.error_stage == "condition"
    assert condition_failure.failures == ["condition error: bad condition"]

    # An unlabelled failure is still reported with a reason, never left blank.
    unlabelled = convert_phase_a_result(make_result(error="boom"))
    assert unlabelled.failures == ["trial error: boom"]


def test_pairing_key_metadata_is_recorded_and_is_not_a_prng_seed():
    expected = derive_scenario_pairing_key("scenario-alpha")
    assert isinstance(expected, int)

    c = convert_phase_a_result(make_result(scenario_id="scenario-alpha"))
    assert c.provenance.scenario_pairing_key == expected
    assert c.trace.seed == expected  # same value, in the canonical schema slot
    assert derive_seed("scenario-alpha") == expected  # legacy alias unchanged

    # Deterministic, and derived from the scenario only.
    assert derive_scenario_pairing_key("scenario-alpha") == expected
    assert derive_scenario_pairing_key("scenario-beta") != expected


def test_randomized_flag_defaults_false_and_changes_metadata_only():
    res = make_result(
        condition=INFORMATIVE_MESSAGE, message_content="B", message_delivered=True,
        parsed_action="B",
    )
    default = convert_phase_a_result(res)
    assert default.provenance.randomized is False

    marked = convert_phase_a_result(res, randomized=True)
    assert marked.provenance.randomized is True
    # The flag records how conditions were assigned; it never alters the trial.
    assert marked.status == default.status == "valid"
    assert marked.trace.to_dict() == default.trace.to_dict()
