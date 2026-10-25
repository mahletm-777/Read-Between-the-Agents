"""Tests for the Phase A behavioral-results layer (``apart.phase_a_results``).

Offline only: no network, no provider calls, no spend, no ledger. Everything is
deterministic and file output goes to ``tmp_path``.

These tests exercise the *results* layer, not any statistical claim. The module
is deliberately descriptive-only, and several tests below pin that property so a
later change cannot quietly introduce an estimand.
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
from apart.phase_a_receiver import DeterministicFakeReceiver
from apart.phase_a_results import (
    ANALYSIS_STATUS,
    RESULTS_SCHEMA_VERSION,
    PhaseAResultsError,
    assemble_results,
    behavioral_outcome,
    joint_outcome,
    validate_results,
    write_results,
)
from apart.phase_a_runner import run_phase_a_trials_for_scenario

RUN_ID = "run-test"
EXPERIMENT_ID = "phaseA-test"


def _trial(scenario_id="scenario-alpha", condition=INFORMATIVE_MESSAGE,
           sender_action="B", message_content="B", message_delivered=True,
           receiver_raw_output="B", parsed_action="B", validity="valid", reason=None,
           error=None, error_stage=None):
    return PhaseATrialResult(
        metadata=PhaseATrialMetadata(
            scenario_id=scenario_id, condition=condition,
            trial_id=f"{scenario_id}_{condition}",
        ),
        sender_action=sender_action,
        message_content=message_content,
        message_delivered=message_delivered,
        receiver_raw_output=receiver_raw_output,
        parsed_action=parsed_action,
        validity=validity,
        reason=reason,
        error=error,
        error_stage=error_stage,
    )


def _three_conditions(scenario_id="scenario-alpha", receiver_action="B"):
    """One complete scenario: NO_MESSAGE / NEUTRAL_MESSAGE / INFORMATIVE_MESSAGE."""
    return [
        _trial(scenario_id=scenario_id, condition=NO_MESSAGE, sender_action="B",
               message_content="", message_delivered=False,
               receiver_raw_output=receiver_action, parsed_action=receiver_action),
        _trial(scenario_id=scenario_id, condition=NEUTRAL_MESSAGE, sender_action="B",
               message_content="neutral notice", message_delivered=True,
               receiver_raw_output=receiver_action, parsed_action=receiver_action),
        _trial(scenario_id=scenario_id, condition=INFORMATIVE_MESSAGE, sender_action="B",
               message_content="B", message_delivered=True,
               receiver_raw_output=receiver_action, parsed_action=receiver_action),
    ]


# ------------------------------------------------------------------ outcomes


@pytest.mark.parametrize("sender,receiver,expected", [
    ("A", "A", "A/A"),
    ("A", "B", "A/B"),
    ("B", "A", "B/A"),
    ("B", "B", "B/B"),
])
def test_joint_outcome_uses_sender_receiver_order(sender, receiver, expected):
    assert joint_outcome(sender, receiver) == expected


@pytest.mark.parametrize("sender,receiver", [
    (None, "B"), ("B", None), (None, None),
    ("X", "B"), ("B", "b"), ("", "B"),
])
def test_joint_outcome_is_none_when_not_established(sender, receiver):
    """An unresolved action is None, never a defaulted 'A'."""
    assert joint_outcome(sender, receiver) is None


def test_behavioral_outcome_does_not_turn_a_missing_parse_into_a_negative():
    """The core fail-closed property: unresolved receiver action -> None, not False."""
    outcome = behavioral_outcome(_trial(receiver_raw_output="maybe B",
                                        parsed_action=None, validity="ambiguous",
                                        reason="hedged_or_unresolved"))
    assert outcome["receiver_action"] is None
    assert outcome["receiver_chose_b"] is None
    assert outcome["joint_outcome"] is None
    assert outcome["receiver_output_present"] is True  # the raw text did exist


def test_behavioral_outcome_records_receiver_b_explicitly():
    assert behavioral_outcome(_trial(parsed_action="B"))["receiver_chose_b"] is True
    assert behavioral_outcome(_trial(parsed_action="A"))["receiver_chose_b"] is False


def test_behavioral_outcome_hashes_a_delivered_message_but_not_a_withheld_one():
    delivered = behavioral_outcome(_trial(message_content="hello", message_delivered=True))
    withheld = behavioral_outcome(_trial(message_content="", message_delivered=False))
    assert delivered["message_sha256"] is not None and delivered["message_chars"] == 5
    assert withheld["message_sha256"] is None and withheld["message_chars"] == 0


# ------------------------------------------------------------------ assembly


def test_assemble_records_run_identity_and_descriptive_only_status():
    artifact = assemble_results(
        _three_conditions(), run_id=RUN_ID, experiment_id=EXPERIMENT_ID,
        created_utc="2026-10-23T00:00:00Z",
    )
    assert artifact["results_schema_version"] == RESULTS_SCHEMA_VERSION
    assert artifact["run_id"] == RUN_ID
    assert artifact["experiment_id"] == EXPERIMENT_ID
    assert artifact["created_utc"] == "2026-10-23T00:00:00Z"
    # No estimand may be adopted here.
    assert artifact["analysis_status"]["mode"] == "descriptive-only"
    assert artifact["analysis_status"]["primary_outcome"] is None
    assert artifact["analysis_status"]["statistical_procedure"] is None
    assert "p_value" not in json.dumps(artifact)


def test_assemble_counts_every_trial_across_conditions():
    artifact = assemble_results(_three_conditions(), run_id=RUN_ID, experiment_id=EXPERIMENT_ID)
    assert artifact["counts"]["n_trials"] == 3
    assert artifact["counts"]["n_scored"] == 3
    assert artifact["counts"]["n_excluded"] == 0
    assert artifact["counts"]["n_conditions"] == len(PHASE_A_CONDITIONS)
    assert artifact["counts"]["joint_outcome_counts_all"] == {"B/B": 3}
    assert set(artifact["by_condition"]) == set(PHASE_A_CONDITIONS)


def test_assemble_reports_message_delivery_per_condition():
    artifact = assemble_results(_three_conditions(), run_id=RUN_ID, experiment_id=EXPERIMENT_ID)
    assert artifact["by_condition"][NO_MESSAGE]["message_delivered_counts"] == {"false": 1}
    assert artifact["by_condition"][NEUTRAL_MESSAGE]["message_delivered_counts"] == {"true": 1}
    assert artifact["by_condition"][INFORMATIVE_MESSAGE]["message_delivered_counts"] == {"true": 1}


def test_a_failed_trial_is_excluded_with_an_explicit_reason_not_dropped():
    trials = _three_conditions() + [
        _trial(scenario_id="scenario-beta", condition=INFORMATIVE_MESSAGE,
               message_content="", message_delivered=False,
               receiver_raw_output=None, parsed_action=None,
               error="boom", error_stage="receiver")
    ]
    artifact = assemble_results(trials, run_id=RUN_ID, experiment_id=EXPERIMENT_ID)
    assert artifact["counts"]["n_trials"] == 4
    assert artifact["counts"]["n_excluded"] == 1
    assert artifact["exclusions"]["n_excluded"] == 1
    assert artifact["exclusions"]["by_reason"] == {"trial_error:receiver": 1}
    assert artifact["exclusions"]["trial_ids"] == ["scenario-beta_informative_message"]
    # The failed trial is still present in the per-trial list.
    failed = [r for r in artifact["trials"] if not r["scored"]]
    assert len(failed) == 1 and failed[0]["error"] == "boom"


def test_an_ambiguous_receiver_output_is_excluded_but_the_raw_text_is_kept():
    trials = _three_conditions() + [
        _trial(scenario_id="scenario-beta", condition=INFORMATIVE_MESSAGE,
               message_content="B", message_delivered=True,
               receiver_raw_output="maybe B", parsed_action=None,
               validity="ambiguous", reason="hedged_or_unresolved")
    ]
    artifact = assemble_results(trials, run_id=RUN_ID, experiment_id=EXPERIMENT_ID)
    excluded = [r for r in artifact["trials"] if not r["scored"]]
    assert len(excluded) == 1
    assert excluded[0]["conversion_status"] == "ambiguous"
    assert excluded[0]["parse_reason"] == "hedged_or_unresolved"
    assert excluded[0]["exclusion_reason"] == "conversion_ambiguous"
    assert excluded[0]["outcome"]["receiver_chose_b"] is None


def test_missing_cells_are_reported_when_an_explicit_grid_is_supplied():
    """A cell with no trial must appear under missingness, not vanish."""
    expected = [(sid, cond) for sid in ("scenario-alpha", "scenario-beta")
                for cond in PHASE_A_CONDITIONS]
    artifact = assemble_results(
        _three_conditions("scenario-alpha"), run_id=RUN_ID, experiment_id=EXPERIMENT_ID,
        expected_cells=expected,
    )
    assert artifact["missingness"]["n_expected_cells"] == 6
    assert artifact["missingness"]["n_missing_cells"] == 3
    # Cells are stored as JSON lists (the artifact is a JSON document, and JSON
    # has no tuple type), so compare in round-tripped form.
    round_tripped = json.loads(json.dumps(artifact))
    assert ["scenario-beta", NO_MESSAGE] in round_tripped["missingness"]["missing_cells"]
    assert round_tripped["missingness"]["missing_cells"] == sorted(
        round_tripped["missingness"]["missing_cells"]
    )
    assert round_tripped["missingness"]["unexpected_cells"] == []


def test_duplicate_trial_ids_are_reported_not_silently_merged():
    trials = _three_conditions() + _three_conditions()
    artifact = assemble_results(trials, run_id=RUN_ID, experiment_id=EXPERIMENT_ID)
    assert artifact["counts"]["n_trials"] == 6  # nothing dropped
    assert sorted(artifact["integrity"]["duplicate_trial_ids"]) == sorted(
        f"scenario-alpha_{c}" for c in PHASE_A_CONDITIONS
    )


# ------------------------------------------------------------------ validation


def test_validate_rejects_a_missing_required_section():
    artifact = assemble_results(_three_conditions(), run_id=RUN_ID, experiment_id=EXPERIMENT_ID)
    broken = dict(artifact)
    broken.pop("exclusions")
    with pytest.raises(PhaseAResultsError, match="missing"):
        validate_results(broken)


def test_validate_refuses_to_let_an_estimand_into_the_artifact():
    artifact = assemble_results(_three_conditions(), run_id=RUN_ID, experiment_id=EXPERIMENT_ID)
    broken = json.loads(json.dumps(artifact))
    broken["analysis_status"]["statistical_procedure"] = "mcnemar"
    with pytest.raises(PhaseAResultsError, match="descriptive-only"):
        validate_results(broken)


def test_an_unscored_trial_without_a_reason_is_refused():
    """Pins the anti-silent-drop property at the validation boundary."""
    artifact = assemble_results(
        _three_conditions() + [
            _trial(scenario_id="scenario-beta", condition=INFORMATIVE_MESSAGE,
                   receiver_raw_output=None, parsed_action=None,
                   error="boom", error_stage="receiver")
        ],
        run_id=RUN_ID, experiment_id=EXPERIMENT_ID,
    )
    broken = json.loads(json.dumps(artifact))
    for record in broken["trials"]:
        if not record["scored"]:
            record["exclusion_reason"] = None
    with pytest.raises(PhaseAResultsError, match="no exclusion_reason"):
        validate_results(broken)


def test_assemble_refuses_empty_input_and_blank_identity():
    with pytest.raises(PhaseAResultsError, match="zero trials"):
        assemble_results([], run_id=RUN_ID, experiment_id=EXPERIMENT_ID)
    with pytest.raises(PhaseAResultsError, match="run_id"):
        assemble_results(_three_conditions(), run_id="  ", experiment_id=EXPERIMENT_ID)
    with pytest.raises(PhaseAResultsError, match="experiment_id"):
        assemble_results(_three_conditions(), run_id=RUN_ID, experiment_id="")


def test_analysis_status_constant_is_descriptive_only():
    assert ANALYSIS_STATUS["mode"] == "descriptive-only"
    assert ANALYSIS_STATUS["statistical_procedure"] is None


# ------------------------------------------------------------------ persistence


def test_write_results_creates_the_file_once(tmp_path):
    artifact = assemble_results(_three_conditions(), run_id=RUN_ID, experiment_id=EXPERIMENT_ID)
    path = write_results(artifact, tmp_path / "nested" / "phase_a_results.json")
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == RUN_ID


def test_write_results_refuses_to_overwrite_a_previous_run(tmp_path):
    artifact = assemble_results(_three_conditions(), run_id=RUN_ID, experiment_id=EXPERIMENT_ID)
    path = tmp_path / "phase_a_results.json"
    write_results(artifact, path)
    original = path.read_bytes()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_results(artifact, path)
    assert path.read_bytes() == original


# ------------------------------------------------------------------ end to end


def test_end_to_end_offline_path_from_trials_to_artifact(tmp_path):
    """runner -> PhaseATrialResult -> converter -> results artifact on disk.

    Fully offline: a DeterministicFakeReceiver, no network, no provider, no ledger.
    """
    outcomes = {
        ("scenario-alpha", NO_MESSAGE): "A",
        ("scenario-alpha", NEUTRAL_MESSAGE): "A",
        ("scenario-alpha", INFORMATIVE_MESSAGE): "B",
        ("scenario-beta", NO_MESSAGE): "A",
        ("scenario-beta", NEUTRAL_MESSAGE): "B",
        ("scenario-beta", INFORMATIVE_MESSAGE): "B",
    }
    receiver = DeterministicFakeReceiver(outcomes)
    trials = []
    for scenario_id in ("scenario-alpha", "scenario-beta"):
        trials.extend(run_phase_a_trials_for_scenario(
            scenario_id=scenario_id, sender_action="B", receiver_fn=receiver.respond,
        ))
    assert len(trials) == 6

    expected = [(sid, cond) for sid in ("scenario-alpha", "scenario-beta")
                for cond in PHASE_A_CONDITIONS]
    artifact = assemble_results(
        trials, run_id=RUN_ID, experiment_id=EXPERIMENT_ID,
        created_utc="2026-10-23T00:00:00Z", expected_cells=expected,
        designation="development",
        source={"corpus": "offline-fixture", "note": "engineering fixture, not evidence"},
    )
    path = write_results(artifact, tmp_path / "phase_a_results.json")
    loaded = json.loads(path.read_text(encoding="utf-8"))

    assert loaded["counts"]["n_trials"] == 6
    assert loaded["counts"]["n_scored"] == 6
    assert loaded["counts"]["n_excluded"] == 0
    assert loaded["missingness"]["n_missing_cells"] == 0
    assert loaded["integrity"]["duplicate_trial_ids"] == []
    assert loaded["designation"] == "development"
    # The three conditions really did differ in what the receiver saw.
    assert loaded["by_condition"][NO_MESSAGE]["message_delivered_counts"] == {"false": 2}
    assert loaded["by_condition"][INFORMATIVE_MESSAGE]["joint_outcome_counts"] == {"B/B": 2}
    assert loaded["by_condition"][NEUTRAL_MESSAGE]["joint_outcome_counts"] == {"B/A": 1, "B/B": 1}


def test_results_layer_does_not_import_the_statistics_module():
    """Phase A statistics stay opt-in; the results layer must not apply a test.

    Checked via the import graph rather than source text, so a docstring mention
    of the statistics module does not make this test pass or fail spuriously.
    """
    import ast
    import inspect

    import apart.phase_a_results as results_module

    tree = ast.parse(inspect.getsource(results_module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    assert "apart.phase_a_stats" not in imported
    assert not hasattr(results_module, "mcnemar_test")
    assert not hasattr(results_module, "estimate_power_mcnemar")



