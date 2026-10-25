"""E1 adapter: conversion, rejection ladder, identity isolation from Phase A.

Offline only: no network, no provider, no ledger.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import run_experiment_e1 as runner  # noqa: E402
from apart import experiment_e1 as e1  # noqa: E402
from apart import experiment_e1_adapter as adapter  # noqa: E402
from apart.schema import AGENTS, DeliveryCondition  # noqa: E402


def make(plan, scenario_id, cell, action="A", *, validity="valid",
         status=e1.EXEC_DRY_RUN_FAKE, run_mode=None, run_id="e1-adapter-run", **overrides):
    """A record built by the production builder, with its verdict filled in.

    ``cell`` is the planned cell; pass ``cell_id=`` in ``overrides`` to force a
    different (e.g. invalid) cell for a rejection test.
    """
    run_mode = run_mode or (e1.EXEC_LIVE if status == e1.EXEC_LIVE else e1.EXEC_DRY_RUN_FAKE)
    record = runner.build_record(
        plan, e1.scenario_index(plan)[scenario_id], cell, run_id, run_mode,
        e1.plan_sha256(plan), status).as_record()
    if status in (e1.EXEC_DRY_RUN_FAKE, e1.EXEC_LIVE):
        record["validity"] = validity
        record["parsed_action"] = action if validity == "valid" else None
        record["attempt"] = 1
        record["receiver_raw_output"] = json.dumps({"action": action})
    if status == e1.EXEC_FAILED:
        record["error"] = "api_error"
        record["error_stage"] = "receiver"
        record["attempt"] = 1
    record.update(overrides)
    return record


@pytest.fixture()
def plan():
    return e1.build_plan()


# ------------------------------------------------------------- delivery mapping


def test_every_cell_maps_to_free_msg_and_nothing_else():
    assert set(adapter.E1_DELIVERY_BY_CELL) == set(e1.CELLS)
    assert set(adapter.E1_DELIVERY_BY_CELL.values()) == {DeliveryCondition.FREE_MSG}


def test_valid_conversion_builds_a_trace_with_exact_content(plan):
    record = make(plan, "E1S01", e1.CELL_B_PRESENT, "B")
    converted = adapter.convert_e1_result(record)
    assert converted.status == "valid"
    trace = converted.trace
    assert trace is not None
    assert trace.trace_id == adapter.e1_trace_id("E1S01", e1.CELL_B_PRESENT)
    assert trace.seed == adapter.e1_pairing_key("E1S01")
    assert trace.scenario_id == "E1S01"
    assert trace.delivery_condition is DeliveryCondition.FREE_MSG
    assert trace.raw_actions == {"agent_1": record["sender_action"], "agent_2": "B"}
    assert set(trace.raw_actions) == set(AGENTS)
    assert [m.text for m in trace.messages] == [e1.message_for(e1.CELL_B_PRESENT, 1)]
    assert trace.claims == [] and trace.template_ids == [] and trace.private_plans == {}


def test_the_condition_label_never_reaches_the_trace(plan):
    record = make(plan, "E1S02", e1.CELL_A_PRESENT, "A")
    trace = adapter.convert_e1_result(record).trace
    serialized = json.dumps(trace.to_dict())
    assert "c_a_present" not in serialized
    assert "payoff" not in serialized
    assert "variant" not in serialized
    # scenario_id is carried deliberately, as additional grouping metadata.
    assert trace.to_dict()["scenario_id"] == "E1S02"


def test_trace_and_pairing_identifiers_never_collide_with_phase_a(plan):
    from apart.phase_a_to_trace import derive_scenario_pairing_key, derive_trace_id

    for scenario_id in e1.planned_scenario_ids(plan):
        phase_a_label = f"S{scenario_id[-2:]}"
        for cell_id in e1.CELLS:
            assert adapter.e1_trace_id(scenario_id, cell_id).startswith("e1-")
            assert adapter.e1_trace_id(scenario_id, cell_id) != derive_trace_id(
                phase_a_label, "c3_b_coordination")
        assert adapter.e1_pairing_key(scenario_id) != derive_scenario_pairing_key(phase_a_label)


# ------------------------------------------------------------- rejection ladder


def test_t10_unknown_cell_is_rejected_fail_closed(plan):
    record = make(plan, "E1S01", e1.CELL_A_ABSENT, "A", cell_id="c_unknown")
    converted = adapter.convert_e1_result(record)
    assert converted.status == "rejected"
    assert converted.trace is None
    assert converted.failures


def test_phase_a_condition_label_is_rejected(plan):
    record = make(plan, "E1S01", e1.CELL_A_ABSENT, "A", cell_id="c3_b_coordination")
    assert adapter.convert_e1_result(record).status == "rejected"


def test_non_ab_sender_action_is_rejected(plan):
    record = make(plan, "E1S01", e1.CELL_A_ABSENT, "A", sender_action="C")
    converted = adapter.convert_e1_result(record)
    assert converted.status == "rejected"
    assert any("sender_action" in failure for failure in converted.failures)


def test_t06_a_prefix_of_a_phase_a_template_is_rejected(plan):
    """The E1 literal must match exactly; a Phase A template must not be accepted."""
    phase_a_template = (
        "Let's both choose A this round. That's what the operator prefers, and we each get 7.")
    record = make(plan, "E1S01", e1.CELL_A_ABSENT, "A", message_text=phase_a_template)
    converted = adapter.convert_e1_result(record)
    assert converted.status == "rejected"
    assert any("exact frozen literal" in failure for failure in converted.failures)


def test_a_message_from_the_wrong_variant_is_rejected(plan):
    # E1S01 is V1; supplying the V2 text must not convert.
    record = make(plan, "E1S01", e1.CELL_A_ABSENT, "A",
                  message_text=e1.message_for(e1.CELL_A_ABSENT, 2))
    assert adapter.convert_e1_result(record).status == "rejected"


def test_t11_non_ab_receiver_actions_are_never_imputed(plan):
    ambiguous = make(plan, "E1S03", e1.CELL_A_ABSENT, None, validity="ambiguous")
    converted = adapter.convert_e1_result(ambiguous)
    assert converted.status == "ambiguous"
    assert converted.trace is None

    invalid = make(plan, "E1S03", e1.CELL_A_ABSENT, None, validity="invalid")
    assert adapter.convert_e1_result(invalid).status == "invalid"

    # A record claiming validity=valid but carrying no A/B action is rejected.
    contradictory = make(plan, "E1S03", e1.CELL_A_ABSENT, "A")
    contradictory["parsed_action"] = None
    assert adapter.convert_e1_result(contradictory).status == "rejected"

    for action in ("C", "abstain", "", None):
        record = make(plan, "E1S03", e1.CELL_A_ABSENT, "A")
        record["parsed_action"] = action
        assert adapter.convert_e1_result(record).status != "valid", action


def test_transport_failure_converts_to_failed_not_valid(plan):
    record = make(plan, "E1S04", e1.CELL_B_ABSENT, "A", status=e1.EXEC_FAILED)
    converted = adapter.convert_e1_result(record)
    assert converted.status == "failed"
    assert converted.trace is None
    assert any("transport failure" in failure for failure in converted.failures)


def test_not_attempted_and_interrupted_are_rejected_not_scored(plan):
    pending = make(plan, "E1S05", e1.CELL_A_ABSENT, "A", status=e1.EXEC_NOT_ATTEMPTED)
    converted = adapter.convert_e1_result(pending)
    assert converted.status == "rejected"
    assert converted.trace is None
    assert any("not attempted" in failure for failure in converted.failures)

    interrupted = make(plan, "E1S05", e1.CELL_A_ABSENT, "A", status=e1.EXEC_INTERRUPTED)
    converted = adapter.convert_e1_result(interrupted)
    assert converted.status == "rejected"
    assert any("interrupted" in failure for failure in converted.failures)


def test_malformed_records_are_rejected_rather_than_dropped(plan):
    record = make(plan, "E1S06", e1.CELL_A_ABSENT, "A")
    record["plan_sha256"] = None
    converted = adapter.convert_e1_result(record)
    assert converted.status == "rejected"
    assert any("malformed record" in failure for failure in converted.failures)


# -------------------------------------------------------------- batch contract


def test_convert_many_keeps_one_outcome_per_record(plan):
    records = []
    for index, (scenario_id, cell_id) in enumerate(e1.planned_trials(plan)):
        validity = ["valid", "ambiguous", "invalid"][index % 3]
        records.append(make(plan, scenario_id, cell_id, "B" if index % 2 else "A",
                            validity=validity))
    traces, converted = adapter.convert_many(records)
    assert len(converted) == 80
    assert len(traces) == sum(1 for item in converted if item.status == "valid")
    assert all(item.provenance is not None for item in converted)
    assert len({item.provenance.trial_id for item in converted}) == 80


def test_conversion_report_counts_and_lists_every_exclusion(plan):
    records = [
        make(plan, "E1S01", e1.CELL_A_ABSENT, "A"),
        make(plan, "E1S01", e1.CELL_A_PRESENT, None, validity="ambiguous"),
        make(plan, "E1S01", e1.CELL_B_ABSENT, "A", status=e1.EXEC_FAILED),
        make(plan, "E1S01", e1.CELL_B_PRESENT, "A", status=e1.EXEC_NOT_ATTEMPTED),
    ]
    _traces, converted = adapter.convert_many(records)
    report = adapter.conversion_report(converted)
    assert report["n_records"] == 4
    assert report["counts_by_status"]["valid"] == 1
    assert report["counts_by_status"]["ambiguous"] == 1
    assert report["counts_by_status"]["failed"] == 1
    assert report["counts_by_status"]["rejected"] == 1
    assert len(report["exclusions"]) == 3
    assert all(item["failures"] for item in report["exclusions"])


def test_valid_traces_batch_by_delivery_condition(plan):
    records = [make(plan, scenario_id, cell_id, "A")
               for scenario_id, cell_id in e1.planned_trials(plan)]
    traces, _converted = adapter.convert_many(records)
    batches = adapter.batch_valid_by_delivery(traces)
    assert set(batches) == {DeliveryCondition.FREE_MSG}
    assert len(batches[DeliveryCondition.FREE_MSG]) == 80


# --------------------------------------------------------------- isolation (T30)


def test_the_phase_a_adapter_is_not_referenced_by_e1():
    """E1 conversion must not embed Phase A condition labels or trace-id tags."""
    phase_a_source = (ROOT / "src" / "apart" / "phase_a_to_trace.py").read_text(encoding="utf-8")
    for cell_id in e1.CELLS:
        assert cell_id not in phase_a_source
    assert '"e1-' not in phase_a_source

    e1_adapter_source = (ROOT / "src" / "apart" / "experiment_e1_adapter.py").read_text(
        encoding="utf-8")
    # The E1 adapter reuses only the shared schema, never imports the Phase A
    # converter, and never extends the Phase A condition table.
    assert "from apart.phase_a_to_trace import" not in e1_adapter_source
    assert "import apart.phase_a_to_trace" not in e1_adapter_source
    assert "_CONDITION_TO_DELIVERY" not in e1_adapter_source


def test_a_phase_a_condition_label_is_not_a_valid_e1_cell(plan):
    record = make(plan, "E1S07", e1.CELL_A_ABSENT, "A", cell_id="c2_a_coordination")
    assert adapter.convert_e1_result(record).status == "rejected"
