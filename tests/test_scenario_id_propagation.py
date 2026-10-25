"""Focused tests: scenario_id propagation for the scenario-cluster interval.

Prerequisite for the approved scenario-cluster bootstrap
(``docs/e_analysis_plan_v1_0.md`` section 3): scenario identity must reach the
trace, the monitor score rows and the E-label rows, must survive serialisation,
must keep ``trace_id`` as the record-level join key, and must never be inferred
from ``seed`` or ``trace_id`` when it is absent.

Offline only: Phase A trials run against a deterministic fixture receiver, monitor
scoring uses ``FakeClient``, and every artifact is written under pytest's
``tmp_path``. The guide annotations below are synthetic fixtures written for these
tests; they are not human annotations and say nothing about real messages,
monitors or collusion.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from apart import analysis_e as ae
from apart.e_annotate import label_trace_from_guide
from apart.e_label import (
    EValidationError,
    MessageSlot,
    MessageStatus,
    label_trace,
    label_traces,
)
from apart.phase_a_conditions import (
    INFORMATIVE_MESSAGE,
    NEUTRAL_MESSAGE,
    NO_MESSAGE,
)
from apart.phase_a_runner import run_phase_a_trial
from apart.phase_a_to_trace import (
    batch_valid_by_condition,
    convert_phase_a_result,
    convert_many,
)
from apart.schema import MONITOR_FORBIDDEN_FIELDS, DeliveryCondition, Trace, trace_from_dict
from monitors.client import FakeClient
from monitors.observable import observable
from monitors.prompts import render_view
from monitors.runner import run_corpus

ROOT = Path(__file__).resolve().parents[1]
FROZEN_PILOT = ROOT / "data" / "frozen" / "pilot_v2_traces.jsonl"
UTC = "2026-10-23T00:00:00Z"
ALPHA = "scenario-alpha"
BETA = "scenario-beta"
VIEWS = ("M1", "M2", "M3", "M4")


# --------------------------------------------------------------- helpers

def _trial(scenario_id: str, condition: str, receiver_action: str):
    """One Phase A trial against a deterministic fixture receiver (no network)."""

    def receiver(_scenario_id: str, _condition: str, _delivered) -> str:
        return receiver_action

    return run_phase_a_trial(scenario_id=scenario_id, condition_name=condition,
                             sender_action="B", receiver_fn=receiver)


def _valid_trial(scenario_id: str, condition: str):
    """A trial that converts to a scored trace under its condition."""
    return _trial(scenario_id, condition,
                  receiver_action="B" if condition == INFORMATIVE_MESSAGE else "A")


def _scenario_traces(scenario_id: str) -> list[Trace]:
    """The three condition-traces of one scenario (all valid)."""
    traces, _ = convert_many([_valid_trial(scenario_id, condition)
                              for condition in (NO_MESSAGE, NEUTRAL_MESSAGE,
                                                INFORMATIVE_MESSAGE)])
    return traces


def _guide(attempt: str, target: str, primary: str, message_id: str) -> dict:
    """One synthetic guide annotation record (fixture only)."""
    return {"message_id": message_id, "coordination_attempt": attempt, "target": target,
            "speech_act_primary": primary, "speech_act_secondary": "BLANK",
            "uncertainty_or_notes": "synthetic test fixture"}


def _empty_slot(slot_id: str = "m1") -> MessageSlot:
    """A single valid non-delivered slot (the E contract requires >= 1 slot)."""
    return MessageSlot(slot_id, MessageStatus.NOT_SENT_BY_DESIGN)


# ------------------------------------------------- 1/2/3: trace-level identity

def test_scenario_id_survives_trace_serialisation():
    trace = convert_phase_a_result(_valid_trial(ALPHA, INFORMATIVE_MESSAGE)).trace
    assert trace.scenario_id == ALPHA

    payload = trace.to_dict()
    assert payload["scenario_id"] == ALPHA

    rebuilt = trace_from_dict(json.loads(json.dumps(payload)))
    assert rebuilt.scenario_id == ALPHA
    assert rebuilt.to_dict() == payload
    assert rebuilt.trace_id == trace.trace_id
    assert rebuilt.seed == trace.seed


def test_traces_of_one_scenario_share_the_scenario_id():
    traces = _scenario_traces(ALPHA)
    assert len(traces) == 3
    assert {t.scenario_id for t in traces} == {ALPHA}
    # trace_id stays per (scenario, condition); the pairing key is shared
    assert len({t.trace_id for t in traces}) == 3
    assert len({t.seed for t in traces}) == 1
    # the identifier is the original string, not the derived pairing key
    assert str(traces[0].seed) != ALPHA
    assert all(t.trace_id.startswith("p1-") for t in traces)


def test_different_scenarios_stay_distinguishable():
    alpha, beta = _scenario_traces(ALPHA), _scenario_traces(BETA)
    assert {t.scenario_id for t in alpha} == {ALPHA}
    assert {t.scenario_id for t in beta} == {BETA}
    assert not ({t.trace_id for t in alpha} & {t.trace_id for t in beta})
    assert not ({t.seed for t in alpha} & {t.seed for t in beta})


def test_scenario_id_never_reaches_a_monitor_view():
    trace = convert_phase_a_result(_valid_trial(ALPHA, INFORMATIVE_MESSAGE)).trace
    assert "scenario_id" in MONITOR_FORBIDDEN_FIELDS
    record = observable(trace)
    assert ALPHA not in json.dumps(asdict(record))
    assert ALPHA not in render_view(record, "M1", "S-01")


# ------------------------------------------------- 4: trace_id joins unchanged

def test_trace_level_joins_still_use_trace_id(tmp_path):
    # One condition per call: several conditions of a scenario share a seed.
    informative = [convert_phase_a_result(_valid_trial(s, INFORMATIVE_MESSAGE)).trace
                   for s in (ALPHA, BETA)]
    records, _ = run_corpus(informative, FakeClient(), created_utc=UTC,
                            raw_dir=tmp_path / "raw", views=VIEWS)

    by_trace = {t.trace_id: t.scenario_id for t in informative}
    assert len(records) == 2 * len(VIEWS)
    assert {r["trace_id"] for r in records} == set(by_trace)
    assert {r["scenario_id"] for r in records} == {ALPHA, BETA}
    assert all(r["scenario_id"] == by_trace[r["trace_id"]] for r in records)

    scores, _ = ae.aggregate_arm_scores(records, arms=("M1", "M3"))
    assert set(scores["M1"]) == set(by_trace) == set(scores["M3"])


# ------------------------------------------- 5: absence and invalid values

def test_missing_scenario_id_is_explicit_and_never_derived(tmp_path):
    row = json.loads(FROZEN_PILOT.read_text().splitlines()[0])
    assert "scenario_id" not in row          # the frozen corpus records no scenario

    trace = trace_from_dict(row)
    assert trace.scenario_id is None
    assert "scenario_id" not in trace.to_dict()

    records, _ = run_corpus([trace], FakeClient(), created_utc=UTC,
                            raw_dir=tmp_path / "raw", views=("M1",))
    record = records[0]
    assert record["scenario_id"] is None
    # no fallback of any kind, in either direction
    assert record["scenario_id"] != str(record["seed"])
    assert record["scenario_id"] != record["trace_id"]
    assert ae.scenario_id_of(record, "score") is None

    label = label_trace(trace.trace_id, [_empty_slot()], trace.raw_actions)
    assert label.scenario_id is None
    assert "scenario_id" not in label.to_dict()

    report = ae.scenario_identity_report([label.to_dict()], records)
    assert report["status"] == ae.SCENARIO_IDENTITY_UNRESOLVED
    assert report["trace_ids_without_scenario_id"] == [trace.trace_id]
    assert report["n_traces_with_scenario_id"] == 0


def test_invalid_scenario_ids_are_rejected_not_silently_dropped():
    base = dict(trace_id="t-1", seed=1, messages=[],
                raw_actions={"agent_1": "A", "agent_2": "A"},
                delivery_condition=DeliveryCondition.NO_MSG)
    for invalid in ("", "   ", 5, True):
        with pytest.raises(ValueError):
            Trace(**base, scenario_id=invalid).validate()

    for invalid in (5, "", "   "):
        with pytest.raises(ae.LabelError):
            ae.scenario_id_of({"trace_id": "t-1", "scenario_id": invalid}, "score")

    with pytest.raises(EValidationError):
        label_trace("t-1", [_empty_slot()], {}, scenario_id="  ")


def test_inconsistent_scenario_ids_are_reported_not_silently_merged():
    labels = [{"trace_id": "t-1", "e": True, "evidence_status": "resolved",
               "scenario_id": ALPHA},
              {"trace_id": "t-2", "e": False, "evidence_status": "resolved",
               "scenario_id": ALPHA}]
    scores = [{"trace_id": "t-1", "view": "M1", "k": 0, "scenario_id": ALPHA},
              {"trace_id": "t-1", "view": "M3", "k": 0, "scenario_id": BETA},
              {"trace_id": "t-2", "view": "M1", "k": 0, "scenario_id": None}]
    report = ae.scenario_identity_report(labels, scores)
    assert report["status"] == ae.SCENARIO_IDENTITY_UNRESOLVED
    assert report["conflicting_score_trace_ids"] == ["t-1"]
    assert report["label_score_mismatch_trace_ids"] == ["t-1", "t-2"]
    assert report["scenario_sizes"] == {ALPHA: 2}


# ------------------------------- 6: score and label rows agree on the scenario

def test_score_and_label_rows_can_be_tied_to_the_same_scenario(tmp_path):
    traces = _scenario_traces(ALPHA) + _scenario_traces(BETA)
    assert len(traces) == 6

    # Monitor scoring: one condition per call, as the converter contract requires.
    records: list[dict] = []
    for condition, batch in batch_valid_by_condition(traces).items():
        batch_records, _ = run_corpus(batch, FakeClient(), created_utc=UTC,
                                      raw_dir=tmp_path / condition.value / "raw",
                                      views=("M1", "M2", "M3"))
        records.extend(batch_records)

    # E labels from synthetic fixture annotations (not human annotations).
    label_rows = []
    for trace in traces:
        if trace.delivery_condition is DeliveryCondition.INFORM_MSG:
            cell = (("YES", "B", "PROPOSAL") if trace.scenario_id == ALPHA
                    else ("NO", "NOT_APPLICABLE", "EXPRESSION_OF_INTENTION"))
            guides = [_guide(*cell, f"m{i}") for i in range(1, len(trace.messages) + 1)]
        else:
            guides = []
        label_rows.append(label_trace_from_guide(trace, guides,
                                                 annotator="synthetic").to_dict())

    report = ae.scenario_identity_report(label_rows, records)
    assert report["status"] == ae.SCENARIO_IDENTITY_AVAILABLE
    assert report["n_labeled_traces"] == 6
    assert report["n_scenarios"] == 2
    assert report["scenario_sizes"] == {ALPHA: 3, BETA: 3}
    assert report["conflicting_score_trace_ids"] == []
    assert report["label_score_mismatch_trace_ids"] == []
    assert report["trace_ids_without_scenario_id"] == []
    # every label row resolves to the same scenario as all of its score rows
    by_trace: dict[str, set] = {}
    for row in records:
        by_trace.setdefault(row["trace_id"], set()).add(row["scenario_id"])
    for row in label_rows:
        assert by_trace[row["trace_id"]] == {row["scenario_id"]}
    # input readiness only: the sampling unit is not asserted, and the readiness report
    # itself performs no resampling (the interval lives in the paired-comparison blocks).
    assert report["sampling_unit_independence"] == "UNRESOLVED"
    assert "no resampling" in report["note"]


# ---------------------------------------- 7/8: legacy and batch compatibility

LABEL_KEYS_WITHOUT_SCENARIO = {
    "trace_id", "e", "e_state", "evidence_status", "joint_action",
    "objective_conflicting_behaviour", "x", "reasons", "n_slots", "n_delivered",
    "n_obscuring", "objective_id", "label_version",
}


def test_legacy_records_and_labels_stay_byte_compatible():
    # A corpus row written before scenario grouping existed round-trips unchanged.
    row = json.loads(FROZEN_PILOT.read_text().splitlines()[0])
    assert trace_from_dict(row).to_dict() == row

    # A label row without a scenario id keeps exactly its original key set.
    assert set(label_trace("t-1", [_empty_slot()], {}).to_dict()) == LABEL_KEYS_WITHOUT_SCENARIO

    # label_traces accepts both shapes and never invents an id.
    records = label_traces([("t-1", [_empty_slot()], {}),
                            ("t-2", [_empty_slot()], {}, ALPHA)])
    assert [r.scenario_id for r in records] == [None, ALPHA]
    assert set(records[1].to_dict()) == LABEL_KEYS_WITHOUT_SCENARIO | {"scenario_id"}
    with pytest.raises(EValidationError):
        label_traces([("t-3", [_empty_slot()], {}, ALPHA, "extra")])
