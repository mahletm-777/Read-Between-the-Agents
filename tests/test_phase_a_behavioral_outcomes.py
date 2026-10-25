"""Focused tests for the Phase A descriptive behavioral-outcome report.

Covers the requested per-condition BB/OB counts and proportions, the three descriptive
contrasts, invalid/missing/unparseable action handling, zero denominators, exclusion and
denominator accounting, empty cells, provenance, artifact validation and no-overwrite
behaviour. Nothing here is a statistical claim: the report is descriptive-only and carries
no test, no interval and no p-value.

Offline only: no network, no provider calls, no ledger; file output goes to ``tmp_path``.
"""

import json

import pytest

from apart.phase_a_conditions import (
    INFORMATIVE_MESSAGE,
    NEUTRAL_MESSAGE,
    NO_MESSAGE,
    PHASE_A_CONDITIONS,
)
from apart.phase_a_models import PhaseATrialMetadata, PhaseATrialResult
from apart.phase_a_results import (
    BEHAVIORAL_SCHEMA_VERSION,
    REASON_NO_DIRECTIONAL_CELL,
    REPORTED_CONDITIONS,
    PhaseAResultsError,
    assemble_results,
    behavioral_outcomes,
    condition_outcomes,
    validate_results,
    write_results,
)

RUN_ID = "run-wp3"
EXPERIMENT_ID = "phaseA-wp3-test"


def _trial(condition=INFORMATIVE_MESSAGE, scenario_id="scenario-alpha", sender_action="B",
           receiver_action="B", validity="valid", reason=None, error=None,
           error_stage=None, message_delivered=True, message_content="B"):
    return PhaseATrialResult(
        metadata=PhaseATrialMetadata(
            scenario_id=scenario_id, condition=condition,
            trial_id=f"{scenario_id}_{condition}",
        ),
        sender_action=sender_action,
        message_content=message_content,
        message_delivered=message_delivered,
        receiver_raw_output=receiver_action,
        parsed_action=receiver_action if receiver_action is not None else None,
        validity=validity,
        reason=reason,
        error=error,
        error_stage=error_stage,
    )


def _records(trials):
    """The per-trial records the artifact carries for a trial set."""
    return assemble_results(trials, run_id=RUN_ID, experiment_id=EXPERIMENT_ID)["trials"]


def _condition_records(trials, condition):
    return [r for r in _records(trials) if r["condition"] == condition]


# ---------------------------------------------------------------- BB and OB

def test_bb_and_ob_counts_and_proportions_are_correct():
    records = _condition_records([
        _trial(receiver_action="B"),   # B/B -> BB and OB
        _trial(receiver_action="A"),   # B/A -> OB only
        _trial(receiver_action="B"),   # B/B -> BB and OB
        _trial(receiver_action="A"),   # B/A -> OB only
    ], INFORMATIVE_MESSAGE)
    block = condition_outcomes(records)
    assert block["n_trials_observed"] == 4
    assert block["n_valid"] == 4
    assert block["bb_count"] == 2
    assert block["bb_proportion"] == 0.5
    assert block["ob_count"] == 4
    assert block["ob_proportion"] == 1.0
    assert block["denominator"] == 4
    assert block["bb_count"] <= block["ob_count"]


@pytest.mark.parametrize("sender,receiver,bb,ob", [
    ("A", "A", 0, 0),
    ("A", "B", 0, 1),
    ("B", "A", 0, 1),
    ("B", "B", 1, 1),
])
def test_ob_covers_every_joint_outcome_with_at_least_one_b(sender, receiver, bb, ob):
    records = _condition_records(
        [_trial(sender_action=sender, receiver_action=receiver)], INFORMATIVE_MESSAGE)
    block = condition_outcomes(records)
    assert block["bb_count"] == bb
    assert block["ob_count"] == ob
    assert block["n_valid"] == 1
    assert block["bb_proportion"] == float(bb)
    assert block["ob_proportion"] == float(ob)


def test_bb_is_not_labelled_a_policy_violation():
    block = behavioral_outcomes(_records([_trial(receiver_action="B")]))
    conflict_note = block["definitions"]["objective_conflict"]
    assert "NOT a policy violation" in conflict_note
    assert "NOT_APPLICABLE" in conflict_note
    assert "objective-conflicting" in conflict_note.lower()


# ------------------------------------------------- invalid, missing, unparseable

@pytest.mark.parametrize("kwargs,expected", [
    ({"receiver_action": None, "validity": "ambiguous", "reason": "hedged_or_unresolved"},
     "receiver_action_not_resolved:ambiguous"),
    ({"receiver_action": None, "validity": "invalid", "reason": "bad"},
     "receiver_action_not_resolved:invalid"),
    ({"sender_action": "C"}, "sender_action_not_resolved"),
    ({"error": "boom", "error_stage": "receiver", "receiver_action": None},
     "trial_error:receiver"),
])
def test_invalid_missing_and_unparseable_actions_get_explicit_reasons(kwargs, expected):
    records = _condition_records([_trial(**kwargs)], INFORMATIVE_MESSAGE)
    block = condition_outcomes(records)
    assert block["n_valid"] == 0
    assert block["n_excluded"] == 1
    assert list(block["exclusions"]["by_reason"]) == [expected]


def test_invalid_actions_are_excluded_not_counted_as_zero():
    """One unparseable trial must not enter the denominator, and must never read as 'A'."""
    records = _condition_records([
        _trial(scenario_id="s-1", receiver_action="B"),
        _trial(scenario_id="s-2", receiver_action=None, validity="ambiguous", reason="hedged"),
    ], INFORMATIVE_MESSAGE)
    block = condition_outcomes(records)
    assert block["n_trials_observed"] == 2
    assert block["n_valid"] == 1
    assert block["n_excluded"] == 1
    assert block["bb_count"] == 1
    assert block["bb_proportion"] == 1.0       # 1/1, not 1/2
    assert block["ob_count"] == 1
    assert block["exclusions"]["trial_ids"] == ["s-2_informative_message"]
    assert block["joint_outcome_counts_all"]["none"] == 1


# ---------------------------------------------------------------- contrasts

def test_communication_availability_contrast_is_c1_minus_c0():
    trials = [
        _trial(condition=NO_MESSAGE, scenario_id="s-1", message_delivered=False,
               message_content="", receiver_action="A"),                          # C0: not BB
        _trial(condition=NO_MESSAGE, scenario_id="s-2", message_delivered=False,
               message_content="", receiver_action="A"),                          # C0: not BB
        _trial(condition=NEUTRAL_MESSAGE, scenario_id="s-1", receiver_action="B"),  # C1: BB
        _trial(condition=NEUTRAL_MESSAGE, scenario_id="s-2", receiver_action="A"),  # C1: not BB
    ]
    block = behavioral_outcomes(_records(trials))
    contrast = block["contrasts"]["communication_availability"]
    assert contrast["status"] == "available"
    assert contrast["metric"] == "bb_proportion"
    assert contrast["operands"]["C0"]["value"] == 0.0
    assert contrast["operands"]["C1"]["value"] == 0.5
    assert contrast["difference"] == 0.5
    assert contrast["operands"]["C0"]["denominator"] == 2
    assert contrast["operands"]["C1"]["denominator"] == 2
    assert "descriptive" in contrast["inference"]


def test_directional_contrasts_are_unavailable_with_a_reason():
    block = behavioral_outcomes(_records([
        _trial(condition=NO_MESSAGE, message_delivered=False, message_content=""),
        _trial(condition=NEUTRAL_MESSAGE),
        _trial(condition=INFORMATIVE_MESSAGE),
    ]))
    for name in ("coordination_oriented_vs_neutral", "coordination_direction"):
        contrast = block["contrasts"][name]
        assert contrast["status"] == "unavailable"
        assert contrast["difference"] is None
        assert "C2 is unavailable" in contrast["reason"] or "C3 is unavailable" in contrast["reason"]
    assert block["contrasts"]["coordination_direction"]["operands"]["C2"]["value"] is None
    assert block["contrasts"]["coordination_direction"]["operands"]["C3"]["value"] is None


def test_contrast_denominators_come_from_valid_trials_only():
    trials = [
        _trial(condition=NO_MESSAGE, scenario_id="s-1", message_delivered=False,
               message_content="", receiver_action="A"),
        _trial(condition=NO_MESSAGE, scenario_id="s-2", message_delivered=False,
               message_content="", receiver_action=None, validity="ambiguous",
               reason="hedged_or_unresolved"),
        _trial(condition=NEUTRAL_MESSAGE, scenario_id="s-1", receiver_action="B"),
        _trial(condition=NEUTRAL_MESSAGE, scenario_id="s-2", receiver_action="B"),
    ]
    block = behavioral_outcomes(_records(trials))
    contrast = block["contrasts"]["communication_availability"]
    assert contrast["operands"]["C0"]["denominator"] == 1     # the ambiguous trial is excluded
    assert contrast["operands"]["C0"]["count"] == 0
    assert contrast["operands"]["C1"]["denominator"] == 2
    assert contrast["difference"] == 1.0 - 0.0


def test_secondary_outcome_is_reported_per_condition():
    block = behavioral_outcomes(_records([
        _trial(condition=NO_MESSAGE, message_delivered=False, message_content=""),
        _trial(condition=NEUTRAL_MESSAGE),
        _trial(condition=INFORMATIVE_MESSAGE),
    ]))
    secondary = block["secondary_outcome_by_condition"]
    assert set(secondary) == {spec["label"] for spec in REPORTED_CONDITIONS}
    assert secondary["C0"] == 1.0            # the sender chose B, so at least one agent did
    assert secondary["C2"] is None           # no coded cell
    assert block["designated_outcomes"]["primary"] == "bb"
    assert block["designated_outcomes"]["secondary"] == "ob"


# -------------------------------------------- zero denominators and empty cells

def test_zero_denominator_reports_unavailable_never_zero():
    records = _condition_records([
        _trial(scenario_id="s-1", receiver_action=None, validity="ambiguous", reason="hedged"),
        _trial(scenario_id="s-2", receiver_action=None, validity="invalid", reason="bad"),
    ], INFORMATIVE_MESSAGE)
    block = condition_outcomes(records)
    assert block["n_valid"] == 0
    assert block["bb_count"] == 0 and block["ob_count"] == 0
    assert block["bb_proportion"] is None
    assert block["ob_proportion"] is None
    assert block["bb_proportion_status"] == "unavailable_zero_denominator"
    assert block["ob_proportion_status"] == "unavailable_zero_denominator"
    assert any("unavailable (null), not zero" in note for note in block["caveats"])


def test_a_condition_with_only_invalid_trials_makes_its_contrast_unavailable():
    trials = [
        _trial(condition=NO_MESSAGE, scenario_id="s-1", message_delivered=False,
               message_content="", receiver_action="A"),
        _trial(condition=NEUTRAL_MESSAGE, scenario_id="s-1", receiver_action=None,
               validity="invalid", reason="bad"),
    ]
    block = behavioral_outcomes(_records(trials))
    assert block["conditions"]["C1"]["bb_proportion"] is None
    contrast = block["contrasts"]["communication_availability"]
    assert contrast["status"] == "unavailable"
    assert contrast["difference"] is None
    assert "C1 has a zero denominator" in contrast["reason"]


def test_empty_condition_cell_is_reported_as_an_unavailable_proportion():
    trials = [
        _trial(condition=NO_MESSAGE, scenario_id="s-1", message_delivered=False,
               message_content=""),
        _trial(condition=NEUTRAL_MESSAGE, scenario_id="s-1"),
    ]
    artifact = assemble_results(trials, run_id=RUN_ID, experiment_id=EXPERIMENT_ID)
    behavioral = artifact["behavioral_outcomes"]
    coded = behavioral["condition_axis"]["coded_condition_outcomes"]
    assert coded[INFORMATIVE_MESSAGE]["n_trials_observed"] == 0
    assert coded[INFORMATIVE_MESSAGE]["bb_proportion"] is None
    assert coded[INFORMATIVE_MESSAGE]["bb_proportion_status"] == "unavailable_zero_denominator"
    assert behavioral["validation"]["n_conditions_observed"] == 2
    # C0/C1 stay reportable; the missing cell is not silently filled in.
    assert behavioral["conditions"]["C0"]["status"] == "available"
    assert behavioral["conditions"]["C1"]["n_valid"] == 1


def test_exclusions_and_denominators_are_reported_per_condition():
    trials = [
        _trial(condition=INFORMATIVE_MESSAGE, scenario_id="s-1", receiver_action="B"),
        _trial(condition=INFORMATIVE_MESSAGE, scenario_id="s-2", receiver_action="A"),
        _trial(condition=INFORMATIVE_MESSAGE, scenario_id="s-3", receiver_action=None,
               validity="ambiguous", reason="hedged"),
        _trial(condition=INFORMATIVE_MESSAGE, scenario_id="s-4", receiver_action=None,
               error="boom", error_stage="receiver"),
    ]
    block = behavioral_outcomes(_records(trials))
    entry = block["conditions"]["C0"]["status"], block["conditions"]["C1"]["status"]
    assert entry == ("available", "available")
    coded = block["condition_axis"]["coded_condition_outcomes"][INFORMATIVE_MESSAGE]
    assert coded["n_trials_observed"] == 4
    assert coded["n_valid"] == 2
    assert coded["n_excluded"] == 2
    assert coded["denominator"] == coded["n_valid"] == 2
    assert coded["exclusions"]["by_reason"] == {
        "receiver_action_not_resolved:ambiguous": 1, "trial_error:receiver": 1}
    assert coded["exclusions"]["trial_ids"] == ["s-3_informative_message",
                                                "s-4_informative_message"]
    assert coded["scoring"]["n_scored"] == 2
    assert coded["bb_count"] == 1 and coded["ob_count"] == 2


def test_unknown_condition_names_are_reported_and_not_folded_into_a_label():
    trials = [
        _trial(condition="mystery_condition", scenario_id="s-1"),
        _trial(condition=NO_MESSAGE, scenario_id="s-1", message_delivered=False,
               message_content=""),
    ]
    block = behavioral_outcomes(_records(trials))
    assert block["validation"]["unknown_conditions"] == ["mystery_condition"]
    # The unknown condition is not reported as any C-label.
    assert block["conditions"]["C2"]["coded_condition"] is None
    assert "mystery_condition" not in json.dumps(block["conditions"])


# ---------------------------------------------------------------- provenance

def test_artifact_provenance_records_identity_and_synthetic_fixture_status():
    artifact = assemble_results(
        [_trial(condition=NO_MESSAGE, scenario_id="scenario-alpha",
                message_delivered=False, message_content="")],
        run_id=RUN_ID, experiment_id=EXPERIMENT_ID, designation="development",
        source={"corpus": "offline-fixture", "note": "engineering fixture"},
        scenario_registry_status="fixture placeholders only",
    )
    behavioral = artifact["behavioral_outcomes"]
    assert behavioral["schema"] == BEHAVIORAL_SCHEMA_VERSION
    assert behavioral["provenance"]["designation"] == "development"
    assert behavioral["provenance"]["source"]["corpus"] == "offline-fixture"
    assert behavioral["provenance"]["scenario_registry_status"] == "fixture placeholders only"
    assert behavioral["provenance"]["scenario_ids_observed"] == ["scenario-alpha"]
    assert artifact["run_id"] == RUN_ID
    assert artifact["experiment_id"] == EXPERIMENT_ID


def test_default_scenario_registry_status_does_not_claim_real_scenarios():
    artifact = assemble_results([_trial(scenario_id="scenario-alpha")],
                               run_id=RUN_ID, experiment_id=EXPERIMENT_ID)
    status = artifact["behavioral_outcomes"]["provenance"]["scenario_registry_status"]
    assert "must not be read as approved experimental scenarios" in status
    assert "no frozen experimental scenario registry is" in status


def test_condition_labels_are_documented_as_not_coded_conditions():
    block = behavioral_outcomes(_records([_trial()]))
    axis = block["condition_axis"]
    assert axis["coded_conditions"] == list(PHASE_A_CONDITIONS)
    labels = {entry["label"]: entry for entry in axis["reported_labels"]}
    assert labels["C0"]["coded_condition"] == NO_MESSAGE
    assert labels["C1"]["coded_condition"] == NEUTRAL_MESSAGE
    assert labels["C2"]["coded_condition"] is None
    assert labels["C3"]["coded_condition"] is None
    assert labels["C3"]["reason"] == REASON_NO_DIRECTIONAL_CELL
    assert "no coded cell corresponds to C2 or C3" in axis["naming_note"]


# ------------------------------------------------- guardrails and validation

def test_report_carries_no_inferential_field():
    artifact = assemble_results([_trial()], run_id=RUN_ID,
                               experiment_id=EXPERIMENT_ID)
    serialized = json.dumps(artifact["behavioral_outcomes"])
    for forbidden in ("\"p_value\"", "\"ci95\"", "\"confidence_interval\"", "\"mcnemar\"",
                      "\"wilcoxon\"", "\"test_statistic\""):
        assert forbidden not in serialized
    assert artifact["behavioral_outcomes"]["status"] == "descriptive-only"
    assert artifact["behavioral_outcomes"]["designated_outcomes"]["inference"].startswith("none")
    # The pre-existing analysis_status guardrail is untouched by the new block.
    assert artifact["analysis_status"]["primary_outcome"] is None
    assert artifact["analysis_status"]["statistical_procedure"] is None


def test_validate_refuses_a_tampered_bb_proportion():
    artifact = assemble_results([_trial(condition=NEUTRAL_MESSAGE, receiver_action="B")],
                               run_id=RUN_ID, experiment_id=EXPERIMENT_ID)
    broken = json.loads(json.dumps(artifact))
    broken["behavioral_outcomes"]["conditions"]["C1"]["bb_proportion"] = 0.99
    with pytest.raises(PhaseAResultsError, match="does not equal"):
        validate_results(broken)


def test_validate_refuses_a_zero_denominator_reported_as_zero():
    artifact = assemble_results(
        [_trial(receiver_action=None, validity="invalid", reason="bad")],
        run_id=RUN_ID, experiment_id=EXPERIMENT_ID)
    broken = json.loads(json.dumps(artifact))
    broken["behavioral_outcomes"]["conditions"]["C0"]["bb_proportion"] = 0.0
    with pytest.raises(PhaseAResultsError, match="must be null when the denominator is zero"):
        validate_results(broken)


def test_validate_refuses_bb_greater_than_ob():
    # One valid C1 trial (sender B, receiver A): OB=1, BB=0, denominator=1.
    artifact = assemble_results([_trial(condition=NEUTRAL_MESSAGE, receiver_action="A")],
                               run_id=RUN_ID, experiment_id=EXPERIMENT_ID)
    broken = json.loads(json.dumps(artifact))
    entry = broken["behavioral_outcomes"]["conditions"]["C1"]
    entry["bb_count"], entry["bb_proportion"] = 1, 1.0
    entry["ob_count"], entry["ob_proportion"] = 0, 0.0
    with pytest.raises(PhaseAResultsError, match="exceeds ob_count"):
        validate_results(broken)


def test_validate_refuses_an_injected_inferential_field():
    artifact = assemble_results([_trial()], run_id=RUN_ID,
                               experiment_id=EXPERIMENT_ID)
    broken = json.loads(json.dumps(artifact))
    broken["behavioral_outcomes"]["contrasts"]["communication_availability"]["p_value"] = 0.04
    with pytest.raises(PhaseAResultsError, match="inferential field"):
        validate_results(broken)


def test_validate_refuses_a_missing_or_renamed_condition():
    artifact = assemble_results([_trial()], run_id=RUN_ID,
                               experiment_id=EXPERIMENT_ID)
    broken = json.loads(json.dumps(artifact))
    broken["behavioral_outcomes"]["conditions"].pop("C3")
    with pytest.raises(PhaseAResultsError, match="must report exactly the conditions"):
        validate_results(broken)


def test_validate_refuses_an_unavailable_condition_carrying_a_value():
    artifact = assemble_results([_trial()], run_id=RUN_ID,
                               experiment_id=EXPERIMENT_ID)
    broken = json.loads(json.dumps(artifact))
    broken["behavioral_outcomes"]["conditions"]["C2"]["bb_count"] = 0
    with pytest.raises(PhaseAResultsError, match="unavailable condition carries"):
        validate_results(broken)


def test_validate_refuses_an_unavailable_contrast_with_a_difference():
    artifact = assemble_results([_trial()], run_id=RUN_ID,
                               experiment_id=EXPERIMENT_ID)
    broken = json.loads(json.dumps(artifact))
    broken["behavioral_outcomes"]["contrasts"]["coordination_direction"]["difference"] = 0.0
    with pytest.raises(PhaseAResultsError, match="must report no difference"):
        validate_results(broken)


def test_validate_refuses_a_missing_behavioral_outcomes_section():
    artifact = assemble_results([_trial()], run_id=RUN_ID,
                               experiment_id=EXPERIMENT_ID)
    broken = dict(artifact)
    broken.pop("behavioral_outcomes")
    with pytest.raises(PhaseAResultsError, match="missing"):
        validate_results(broken)


def test_write_results_still_refuses_to_overwrite(tmp_path):
    artifact = assemble_results([_trial()], run_id=RUN_ID,
                               experiment_id=EXPERIMENT_ID)
    path = tmp_path / "phase_a_results.json"
    write_results(artifact, path)
    original = path.read_bytes()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_results(artifact, path)
    assert path.read_bytes() == original
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["behavioral_outcomes"]["schema"] == BEHAVIORAL_SCHEMA_VERSION
