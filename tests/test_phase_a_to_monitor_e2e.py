"""Stage-A offline end-to-end test: Phase A -> converter -> monitors.

Deterministic, fake-only, and network-free. Exercises the full path:
condition assignment -> message delivery -> fake receiver -> PhaseATrialResult
-> converter -> observable evidence -> M1/M2/M3/M4 via FakeClient -> parsing ->
persistence -> determinism, all under ``tmp_path`` only.

This demonstrates integration correctness only. It is NOT evidence about
monitor performance or real-agent behaviour.
"""

from apart import analysis_e as ae
from apart import e_annotate as ea
from apart.phase_a_conditions import INFORMATIVE_MESSAGE, NEUTRAL_MESSAGE, NO_MESSAGE
from apart.phase_a_receiver import DeterministicFakeReceiver
from apart.phase_a_runner import run_phase_a_trials_for_scenario
from apart.phase_a_to_trace import batch_valid_by_condition, convert_many
from apart.schema import DeliveryCondition

from monitors.client import FakeClient
from monitors.observable import observable
from monitors.prompts import render_view
from monitors.runner import run_corpus, write_id_map, write_scores

import json

import pytest

UTC = "2026-10-21T00:00:00Z"

VALID_OUTCOMES = {
    ("scenario-alpha", NO_MESSAGE): "A",
    ("scenario-alpha", NEUTRAL_MESSAGE): "A",
    ("scenario-alpha", INFORMATIVE_MESSAGE): "B",
    ("scenario-beta", NO_MESSAGE): "A",
    ("scenario-beta", NEUTRAL_MESSAGE): "B",
    ("scenario-beta", INFORMATIVE_MESSAGE): "B",
}

# "maybe" triggers the parser's hedge rule -> ambiguous.
AMBIGUOUS_OUTCOMES = {
    ("scenario-gamma", NO_MESSAGE): "maybe B",
    ("scenario-gamma", NEUTRAL_MESSAGE): "maybe B",
    ("scenario-gamma", INFORMATIVE_MESSAGE): "maybe B",
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network access attempted")),
    )


def _run_scenario(scenario_id, outcomes):
    receiver = DeterministicFakeReceiver(outcomes)
    return run_phase_a_trials_for_scenario(
        scenario_id=scenario_id, sender_action="B", receiver_fn=receiver.respond
    )


def test_full_offline_path_with_strict_ambiguous_exclusion(tmp_path):
    valid_trials = []
    for sid in ("scenario-alpha", "scenario-beta"):
        valid_trials.extend(_run_scenario(sid, VALID_OUTCOMES))
    ambiguous_trials = _run_scenario("scenario-gamma", AMBIGUOUS_OUTCOMES)

    scored_traces, converted = convert_many(
        valid_trials + ambiguous_trials, execution_status="offline-e2e"
    )

    # 2 valid scenarios x 3 conditions -> 6 scored traces.
    assert len(scored_traces) == 6
    assert len(converted) == 9

    # Strict ambiguous exclusion: ambiguous trials stay in the sidecar only.
    gamma = [c for c in converted if c.provenance.scenario_id == "scenario-gamma"]
    assert len(gamma) == 3
    assert all(c.status == "ambiguous" and c.trace is None for c in gamma)
    assert all(t.raw_actions["agent_2"] in ("A", "B") for t in scored_traces)

    # Leakage: no condition/scenario/seed/forbidden field in rendered views.
    forbidden = [
        "scenario-alpha", "scenario-beta", "scenario-gamma",
        "no_message", "neutral_message", "informative_message",
        "NO_MSG", "NEUTRAL_MSG", "INFORM_MSG",
        "delivery_condition", "claims", "template_ids", "private_plans",
    ]
    for trace in scored_traces:
        record = observable(trace)
        for view in ("M1", "M2", "M3", "M4"):
            text = render_view(record, view, "case-test")
            for token in forbidden:
                assert token not in text, f"{view} leaked {token!r}"
        assert "chose" not in render_view(record, "M1", "case-test")
        assert "[Not shown in this record]" in render_view(record, "M2", "case-test")

    # Monitor scoring: one run_corpus per condition (seeds unique per batch).
    batches = batch_valid_by_condition(scored_traces)
    assert set(batches) == {
        DeliveryCondition.NO_MSG,
        DeliveryCondition.NEUTRAL_MSG,
        DeliveryCondition.INFORM_MSG,
    }

    per_condition_records = {}
    for condition, traces in batches.items():
        assert len(traces) == 2
        seeds = [t.seed for t in traces]
        assert len(seeds) == len(set(seeds))  # unique seeds within the batch

        out_dir = tmp_path / condition.value
        out_dir.mkdir(parents=True, exist_ok=True)
        records, ensembles = run_corpus(
            traces, FakeClient(), created_utc=UTC, raw_dir=out_dir / "raw"
        )
        write_scores(records, out_dir / "scores_v1.jsonl")
        write_id_map(records, out_dir / "id_map_v1.csv")
        per_condition_records[condition] = records

        # Raw responses landed under this condition's own directory.
        assert (out_dir / "raw").exists()
        assert any((out_dir / "raw").iterdir())

    # Determinism: a second identical run reproduces the same records.
    for condition, traces in batches.items():
        records_again, _ = run_corpus(
            traces,
            FakeClient(),
            created_utc=UTC,
            raw_dir=tmp_path / "again" / condition.value / "raw",
        )
        assert records_again == per_condition_records[condition]

    # Each condition's records are distinguishable by (seed, view, k) within
    # its own file; the scenario-level seed may repeat across condition runs.
    for records in per_condition_records.values():
        keys = [(r["seed"], r["view"], r["k"]) for r in records]
        assert len(keys) == len(set(keys))

    # E-label -> score join across conditions, keyed by trace_id (not seed).
    # SYNTHETIC annotations written for this test only: they are not human
    # annotations, not validated, and say nothing about real messages or monitors.
    def synthetic(attempt, target, primary, message_id):
        return {"message_id": message_id, "coordination_attempt": attempt,
                "target": target, "speech_act_primary": primary,
                "speech_act_secondary": "BLANK",
                "uncertainty_or_notes": "synthetic test fixture"}

    trace_scenario = {c.trace.trace_id: c.provenance.scenario_id
                      for c in converted if c.trace is not None}
    labels = []
    for trace in scored_traces:
        if trace.delivery_condition is DeliveryCondition.INFORM_MSG:
            if trace_scenario[trace.trace_id] == "scenario-alpha":
                cell = ("YES", "B", "PROPOSAL")
            else:
                cell = ("NO", "NOT_APPLICABLE", "EXPRESSION_OF_INTENTION")
            records_for = [synthetic(*cell, f"m{i}") for i in range(1, len(trace.messages) + 1)]
        else:
            records_for = []   # NO_MSG / NEUTRAL_MSG: no agent speech to annotate
        labels.append(ea.label_trace_from_guide(trace, records_for, annotator="synthetic"))
    label_rows = [r.to_dict() for r in labels]

    by_condition = {t.trace_id: t.delivery_condition for t in scored_traces}
    for row in label_rows:
        if by_condition[row["trace_id"]] is DeliveryCondition.INFORM_MSG:
            assert row["evidence_status"] != "no_agent_message"
        else:
            assert row["evidence_status"] == "no_agent_message"

    all_scores = [r for records in per_condition_records.values() for r in records]
    # Seeds repeat across conditions; trace_ids do not.
    assert len({r["seed"] for r in all_scores}) == 2
    assert {r["trace_id"] for r in all_scores} == {row["trace_id"] for row in label_rows}
    keys = [(r["trace_id"], r["view"], r["k"]) for r in all_scores]
    assert len(keys) == len(set(keys))

    alpha_inform = next(t.trace_id for t in scored_traces
                        if t.delivery_condition is DeliveryCondition.INFORM_MSG
                        and trace_scenario[t.trace_id] == "scenario-alpha")
    score_rows = [r for r in all_scores
                  if r["k"] == 0 and r["view"] in ae.ARMS
                  and not (r["view"] == "M1" and r["trace_id"] == alpha_inform)]

    report = ae.run_analysis_e(label_rows, score_rows, n_boot=50)
    reference = report["reference"]
    assert reference["n_label_rows"] == 6
    assert reference["n_usable"] == 2
    assert reference["excluded_by_status"] == {"no_agent_message": 4}
    assert set(reference["excluded_trace_ids"]) <= set(by_condition)
    missing = report["missingness"]
    assert missing["absent_trace_ids"]["M1"] == [alpha_inform]   # missing, not negative
    assert missing["absent_trace_ids"]["M3"] == []
    assert alpha_inform not in ae.aggregate_arm_scores(score_rows)[0]["M1"]
    assert report["contrasts"]["M3 vs M1"]["paired_auroc_difference"]["n_pairs"] == 1
    assert json.dumps(ae._json_safe(report))
