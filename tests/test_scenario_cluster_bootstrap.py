"""Focused tests: the approved paired percentile SCENARIO-CLUSTER bootstrap interval.

Covers WP2 of the approved method (``docs/e_analysis_plan_v1_0.md`` section 3): resampling
over scenarios (not individual traces), retention of the paired M1/M3 observations,
reproducibility, the percentile calculation, the counting and exclusion of single-class
replicates, missing/invalid scenario ids, missing predictions and excluded labels, explicit
non-estimable reporting, and regression compatibility with the Holm family and the
unresolved-label gate.

Offline and synthetic only: no provider calls, no network, no ledger, no writes.
"""

from __future__ import annotations

import random

import pytest

from apart import analysis_e as ae

ALPHA, BETA = "sc-alpha", "sc-beta"
TOL = 1e-12


# --------------------------------------------------------------- fixtures

def label_row(trace_id: str, e_value, scenario_id=None, e_status="resolved") -> dict:
    row = {"trace_id": trace_id, "e_value": e_value, "e_status": e_status}
    if scenario_id is not None:
        row["scenario_id"] = scenario_id
    return row


def score_row(arm: str, trace_id: str, value: float, scenario_id=None) -> dict:
    row = {"view": arm, "trace_id": trace_id, "k": 0, "status": "ok", "score": value}
    if scenario_id is not None:
        row["scenario_id"] = scenario_id
    return row


def rows_for(truth, scores, scenario_of=None, arms=("M1", "M3")):
    """Build label rows and score rows for ``{trace_id: e}`` and ``{arm: {trace_id: score}}``."""
    scenario_of = scenario_of or {}
    labels = [label_row(t, e, scenario_of.get(t)) for t, e in sorted(truth.items())]
    scores_rows = [score_row(arm, t, v, scenario_of.get(t))
                   for arm in arms for t, v in sorted(scores.get(arm, {}).items())]
    return labels, scores_rows


def arms_of(scores_rows, truth, scenario_of=None):
    scores, _ = ae.aggregate_arm_scores(scores_rows, arms=("M1", "M3"),
                                        trace_ids_of_interest=truth)
    return scores


class FixedRng:
    """Deterministic ``randrange`` source: replays a fixed sequence of scenario indices."""

    def __init__(self, picks):
        self._picks = list(picks)
        self._i = 0

    def randrange(self, n):
        value = self._picks[self._i % len(self._picks)]
        self._i += 1
        assert 0 <= value < n
        return value


class AlwaysFirstRng:
    """Always draws the first (sorted) scenario, so every replicate is single-class."""

    def randrange(self, n):
        return 0


# ------------------------------------------------- clustering (not per-trace)

#: Two scenarios, each internally single-class: drawing only one of them yields a
#: single-class replicate, which trace-level resampling could never produce.
PURE_TRUTH = {"t-a1": 1, "t-a2": 1, "t-b1": 0, "t-b2": 0}
PURE_SCORES = {"M3": {"t-a1": 90, "t-a2": 80, "t-b1": 10, "t-b2": 20},
               "M1": {"t-a1": 10, "t-a2": 20, "t-b1": 90, "t-b2": 80}}
PURE_SCENARIOS = {"t-a1": ALPHA, "t-a2": ALPHA, "t-b1": BETA, "t-b2": BETA}


def test_scenario_cluster_sample_draws_clusters_with_replacement():
    clusters = {"sc-1": ["t-1", "t-2"], "sc-2": ["t-3"]}
    names = sorted(clusters)
    # Two draws of the same scenario contribute that scenario's observations twice.
    assert ae.scenario_cluster_sample(names, clusters, FixedRng([0, 0])) == ["t-1", "t-2",
                                                                             "t-1", "t-2"]
    # Unequal cluster sizes mean the replicate size reflects the clusters drawn.
    assert ae.scenario_cluster_sample(names, clusters, FixedRng([0, 1])) == ["t-1", "t-2",
                                                                            "t-3"]
    assert ae.scenario_cluster_sample(names, clusters, FixedRng([1, 1])) == ["t-3", "t-3"]


def test_resampling_unit_is_the_scenario_not_the_trace():
    labels, scores_rows = rows_for(PURE_TRUTH, PURE_SCORES, PURE_SCENARIOS)
    truth, _ = ae.build_reference(labels)
    scores = arms_of(scores_rows, truth)
    block = ae.scenario_cluster_bootstrap_interval(scores["M3"], scores["M1"], truth,
                                                   PURE_SCENARIOS, n_boot=200, seed=0)
    assert block["resampling_unit"] == "scenario"
    assert block["n_scenarios"] == 2
    assert block["scenario_sizes"] == {ALPHA: 2, BETA: 2}
    assert block["status"] == ae.INTERVAL_STATUS_ESTIMATED
    # Cluster sampling can draw one scenario twice, leaving a single-class replicate.
    # Resampling individual traces within reference class could never do that.
    assert block["n_boot_invalid_single_class"] > 0
    assert block["n_boot_valid"] + block["n_boot_invalid_single_class"] == 200


# ------------------------------------------------- paired observations retained

def test_paired_observations_are_retained_and_unpaired_ones_excluded():
    truth = {"t-1": 1, "t-2": 0, "t-3": 1}
    scenario_of = {"t-1": ALPHA, "t-2": ALPHA, "t-3": "sc-gamma"}
    # t-3 has an M3 score but no M1 score, and its M3 score is deliberately extreme
    # (a positive below the negative). If the arms were scored on DIFFERENT samples,
    # M3's AUROC would fall from 1.0 to 0.5 here; paired scoring keeps it at 1.0.
    labels, scores_rows = rows_for(truth, {"M3": {"t-1": 90, "t-2": 10, "t-3": 5},
                                           "M1": {"t-1": 10, "t-2": 90}}, scenario_of)
    reference, _ = ae.build_reference(labels)
    scores = arms_of(scores_rows, reference)
    block = ae.paired_auroc_difference(scores["M3"], scores["M1"], reference, n_boot=50,
                                       seed=1, scenario_of=scenario_of)
    # Only the intersection of the two arms is paired.
    assert block["n_pairs"] == 2
    assert block["arm_loss"]["only_first_has_score"] == ["t-3"]
    assert block["arm_loss"]["only_second_has_score"] == []
    # Both AUROCs come from the same two traces: M3 separates, M1 inverts.
    assert block["auroc_first"] == 1.0
    assert block["auroc_second"] == 0.0
    assert block["delta"] == 1.0
    # The unpaired trace contributes no cluster: it never enters the resampling.
    assert "sc-gamma" not in (block["scenario_sizes"] or {})
    assert sum(block["scenario_sizes"].values()) == block["n_pairs"]


def test_excluded_labels_do_not_enter_the_interval():
    labels, scores_rows = rows_for(PURE_TRUTH, PURE_SCORES, PURE_SCENARIOS)
    extra_labels = labels + [label_row("t-x", None, ALPHA, e_status="unresolved"),
                             label_row("t-y", 0, ALPHA, e_status="no_agent_message")]
    extra_rows = scores_rows + [score_row("M3", "t-x", 99, ALPHA),
                                score_row("M1", "t-x", 1, ALPHA),
                                score_row("M3", "t-y", 99, ALPHA),
                                score_row("M1", "t-y", 1, ALPHA)]
    base_report = ae.run_analysis_e(labels, scores_rows, n_boot=100, seed=5)
    extra_report = ae.run_analysis_e(extra_labels, extra_rows, n_boot=100, seed=5)
    base = base_report["contrasts"]["M3 vs M1"]["paired_auroc_difference"]
    extra = extra_report["contrasts"]["M3 vs M1"]["paired_auroc_difference"]
    assert extra["n_pairs"] == base["n_pairs"]
    assert extra["delta"] == base["delta"]
    assert extra["ci95"] == base["ci95"]
    assert extra["n_boot_invalid_single_class"] == base["n_boot_invalid_single_class"]


# ------------------------------------------------- reproducibility and the interval

#: Two scenarios whose internal structure is identical, so every mixture yields the
#: same delta: the interval must collapse onto that single value.
CONST_TRUTH = {"t-1": 1, "t-2": 0, "t-3": 1, "t-4": 0}
CONST_SCORES = {"M3": {"t-1": 90, "t-2": 10, "t-3": 80, "t-4": 20},
                "M1": {"t-1": 10, "t-2": 90, "t-3": 20, "t-4": 80}}
CONST_SCENARIOS = {"t-1": "sc-1", "t-2": "sc-1", "t-3": "sc-2", "t-4": "sc-2"}

#: A second configuration whose replicates take several distinct values.
TWO_TRUTH = {"t-1": 1, "t-2": 0, "t-3": 1, "t-4": 0}
TWO_SCORES = {"M3": {"t-1": 90, "t-2": 10, "t-3": 90, "t-4": 10},
              "M1": {"t-1": 10, "t-2": 90, "t-3": 90, "t-4": 10}}
TWO_SCENARIOS = dict(CONST_SCENARIOS)


def _block(truth, scores, scenario_of, **kwargs):
    labels, scores_rows = rows_for(truth, scores, scenario_of)
    reference, _ = ae.build_reference(labels)
    arms = arms_of(scores_rows, reference)
    return ae.scenario_cluster_bootstrap_interval(arms["M3"], arms["M1"], reference,
                                                  scenario_of, **kwargs)


def test_interval_is_exact_on_a_constant_delta_fixture():
    block = _block(CONST_TRUTH, CONST_SCORES, CONST_SCENARIOS, n_boot=100, seed=0)
    # Every cluster mixture separates both arms the same way: delta is constant at 1.0.
    assert block["status"] == ae.INTERVAL_STATUS_ESTIMATED
    assert block["n_boot_valid"] == 100
    assert block["n_boot_invalid_single_class"] == 0
    assert block["ci95"] == [1.0, 1.0]


def test_interval_is_the_percentile_of_the_valid_replicates():
    block = _block(TWO_TRUTH, TWO_SCORES, TWO_SCENARIOS, n_boot=200, seed=11,
                   keep_replicates=True)
    reps = sorted(block["replicates"])
    assert len(reps) == block["n_boot_valid"]
    assert block["ci95"] == [ae.percentile(reps, 0.025), ae.percentile(reps, 0.975)]
    assert block["ci95"][0] <= block["ci95"][1]
    # The fixtures produce more than one replicate value, so the interval is not degenerate.
    assert len(set(reps)) > 1


def test_reproducible_with_a_fixed_seed():
    first = _block(TWO_TRUTH, TWO_SCORES, TWO_SCENARIOS, n_boot=100, seed=42,
                   keep_replicates=True)
    second = _block(TWO_TRUTH, TWO_SCORES, TWO_SCENARIOS, n_boot=100, seed=42,
                    keep_replicates=True)
    assert first == second
    assert first["boot_seed"] == 42
    assert first["n_boot_requested"] == 100

    labels, rows = rows_for(TWO_TRUTH, TWO_SCORES, TWO_SCENARIOS)
    report_a = ae.run_analysis_e(labels, rows, n_boot=64, seed=7)
    report_b = ae.run_analysis_e(labels, rows, n_boot=64, seed=7)
    assert report_a["interval_contract"] == report_b["interval_contract"]
    assert report_a["contrasts"] == report_b["contrasts"]


def test_single_class_replicates_are_counted_and_never_imputed():
    block = _block(PURE_TRUTH, PURE_SCORES, PURE_SCENARIOS, n_boot=300, seed=3)
    total = block["n_boot_valid"] + block["n_boot_invalid_single_class"]
    assert total == 300
    assert block["n_boot_invalid_single_class"] > 0
    assert block["status"] == ae.INTERVAL_STATUS_ESTIMATED
    assert block["ci95"] is not None


def test_every_replicate_single_class_reports_no_interval(monkeypatch):
    monkeypatch.setattr(ae.random, "Random", lambda seed=None: AlwaysFirstRng())
    block = _block(PURE_TRUTH, PURE_SCORES, PURE_SCENARIOS, n_boot=25, seed=0)
    assert block["status"] == ae.INTERVAL_STATUS_NOT_ESTIMABLE
    assert block["reason"] == ae.INTERVAL_REASON_ALL_REPLICATES_INVALID
    assert block["ci95"] is None
    assert block["n_boot_valid"] == 0
    assert block["n_boot_invalid_single_class"] == 25


# ------------------------------------------------- scenario-id availability

def test_scenario_clusters_refuses_missing_and_invalid_identifiers():
    # No mapping at all: the paired observations cannot be clustered.
    assert ae.scenario_clusters(["t-1"], None) == (None, ae.INTERVAL_REASON_NO_SCENARIO_ID,
                                                    ["t-1"])
    # A partial mapping is never padded: the uncovered observation is named.
    assert ae.scenario_clusters(["t-1", "t-2"], {"t-1": ALPHA}) == (
        None, ae.INTERVAL_REASON_NO_SCENARIO_ID, ["t-2"])
    # An explicit null is treated as absent, never substituted.
    assert ae.scenario_clusters(["t-1"], {"t-1": None}) == (
        None, ae.INTERVAL_REASON_NO_SCENARIO_ID, ["t-1"])
    # Blank and non-string values are refused outright.
    for bad in ("", "   ", 5, True):
        assert ae.scenario_clusters(["t-1"], {"t-1": bad}) == (
            None, ae.INTERVAL_REASON_INVALID_SCENARIO_ID, ["t-1"])
    # Nothing paired at all.
    assert ae.scenario_clusters([], {}) == (None, ae.INTERVAL_REASON_NO_SCENARIOS, [])


def test_missing_scenario_id_on_a_paired_observation_blocks_the_interval():
    partial = {k: v for k, v in PURE_SCENARIOS.items() if k != "t-b2"}
    block = _block(PURE_TRUTH, PURE_SCORES, partial, n_boot=50, seed=0)
    assert block["status"] == ae.INTERVAL_STATUS_NOT_ESTIMABLE
    assert block["reason"] == ae.INTERVAL_REASON_NO_SCENARIO_ID
    assert block["ci95"] is None
    assert block["trace_ids_without_scenario_id"] == ["t-b2"]
    assert block["n_scenarios"] is None


def test_rows_disagreeing_about_a_scenario_block_the_interval():
    labels, scores_rows = rows_for(PURE_TRUTH, PURE_SCORES, PURE_SCENARIOS)
    # One score row claims a different scenario for a paired trace.
    scores_rows = [dict(row, scenario_id="sc-other") if row["trace_id"] == "t-b2" else row
                   for row in scores_rows]
    report = ae.run_analysis_e(labels, scores_rows, n_boot=50)
    readiness = report["scenario_identity"]["readiness"]
    assert report["scenario_identity"]["source"] == "unavailable"
    assert "t-b2" in readiness["label_score_mismatch_trace_ids"]
    block = report["contrasts"]["M3 vs M1"]["paired_auroc_difference"]
    assert block["interval_status"] == ae.INTERVAL_STATUS_NOT_ESTIMABLE
    assert block["interval_reason"] == ae.INTERVAL_REASON_INCONSISTENT_SCENARIO_ID
    assert block["ci95"] is None
    assert report["interval_contract"]["scenario_unavailable_reason"] == (
        ae.INTERVAL_REASON_INCONSISTENT_SCENARIO_ID)


def test_malformed_scenario_id_is_refused_rather_than_ignored():
    labels, scores_rows = rows_for(PURE_TRUTH, PURE_SCORES, PURE_SCENARIOS)
    labels = [dict(row, scenario_id=7) if row["trace_id"] == "t-a1" else row
              for row in labels]
    with pytest.raises(ae.LabelError):
        ae.run_analysis_e(labels, scores_rows, n_boot=10)


# ------------------------------------------------- explicit non-estimable paths

def test_no_interval_is_requested_with_n_boot_zero():
    block = _block(CONST_TRUTH, CONST_SCORES, CONST_SCENARIOS, n_boot=0, seed=0)
    assert block["status"] == ae.INTERVAL_STATUS_NOT_ESTIMABLE
    assert block["reason"] == ae.INTERVAL_REASON_N_BOOT_ZERO
    assert block["ci95"] is None
    assert block["n_boot_valid"] == 0


def test_single_reference_class_in_the_observations_is_not_estimable():
    truth = {"t-1": 1, "t-2": 1}
    scores = {"M3": {"t-1": 90, "t-2": 80}, "M1": {"t-1": 10, "t-2": 20}}
    labels, scores_rows = rows_for(truth, scores, {"t-1": ALPHA, "t-2": ALPHA})
    reference, _ = ae.build_reference(labels)
    arms = arms_of(scores_rows, reference)
    block = ae.paired_auroc_difference(arms["M3"], arms["M1"], reference, n_boot=20,
                                       scenario_of={"t-1": ALPHA, "t-2": ALPHA})
    assert block["delta"] is None
    assert block["degenerate"] == ae.DEG_SINGLE_CLASS
    assert block["ci95"] is None
    assert block["interval_reason"] == ae.INTERVAL_REASON_SINGLE_CLASS


def test_no_paired_observations_is_not_estimable():
    block = ae.paired_auroc_difference({}, {}, {"t-1": 1, "t-2": 0}, n_boot=20,
                                       scenario_of={"t-1": ALPHA})
    assert block["n_pairs"] == 0
    assert block["degenerate"] == ae.DEG_NO_PAIRED_SEEDS
    assert block["ci95"] is None
    assert block["interval_reason"] == ae.INTERVAL_REASON_NO_PAIRS


def test_real_data_shaped_input_reports_no_interval_at_all():
    """No scenario ids anywhere: every contrast is explicitly not estimable."""
    labels, scores_rows = rows_for(PURE_TRUTH, PURE_SCORES)      # no scenario ids
    report = ae.run_analysis_e(labels, scores_rows, n_boot=100)
    contract = report["interval_contract"]
    assert contract["scenario_source"] == "unavailable"
    assert contract["scenario_unavailable_reason"] == ae.INTERVAL_REASON_NO_SCENARIO_ID
    for name, block in report["contrasts"].items():
        assert block["paired_auroc_difference"]["ci95"] is None, name
        assert block["paired_auroc_difference"]["interval_status"] == (
            ae.INTERVAL_STATUS_NOT_ESTIMABLE), name


# ------------------------------------------------- regression: Holm and the gate

GATE_SCORES = {
    "M1": {"t-1": 90, "t-2": 10, "t-3": 80, "t-4": 20},
    "M2": {"t-1": 10, "t-2": 90, "t-3": 20, "t-4": 80},
    "M3": {"t-1": 90, "t-2": 20, "t-3": 10, "t-4": 80},
    "M4": {"t-1": 70, "t-2": 60, "t-3": 30, "t-4": 40},
}
GATE_SCENARIOS = {"t-1": ALPHA, "t-2": ALPHA, "t-3": BETA, "t-4": BETA}
FOUR_ARMS = ("M1", "M2", "M3", "M4")


def test_holm_family_and_contrasts_are_unchanged_by_scenario_identity():
    truth = {"t-1": 1, "t-2": 0, "t-3": 1, "t-4": 0}
    labels_no, rows_no = rows_for(truth, GATE_SCORES, None, arms=FOUR_ARMS)
    labels_sc, rows_sc = rows_for(truth, GATE_SCORES, GATE_SCENARIOS, arms=FOUR_ARMS)
    plain = ae.run_analysis_e(labels_no, rows_no, n_boot=100, seed=5)
    with_scenarios = ae.run_analysis_e(labels_sc, rows_sc, n_boot=100, seed=5)

    # The Holm family, its inputs and its adjustments do not move.
    assert plain["multiplicity"] == with_scenarios["multiplicity"]
    assert plain["unresolved_gate"] == with_scenarios["unresolved_gate"]
    expected = {"M3 vs M1", "M1 vs M2", "M3 vs M2", "M4 vs M3", "M1 vs M4"}
    assert set(plain["contrasts"]) == set(with_scenarios["contrasts"]) == expected

    secondary = {"M1 vs M2", "M3 vs M2", "M4 vs M3", "M1 vs M4"}
    for name in expected:
        before = plain["contrasts"][name]["paired_auroc_difference"]
        after = with_scenarios["contrasts"][name]["paired_auroc_difference"]
        assert before["delta"] == after["delta"], name
        assert before["p_boot"] == after["p_boot"], name
        assert before["p_boot_source"] == ae.P_BOOT_SOURCE, name
        # Only the interval changes: unavailable without scenario identity, available with it.
        assert before["ci95"] is None, name
        # Holm and its estimability flags cover the four secondary contrasts only; the
        # primary contrast is a single test and is never corrected (plan section 7).
        if name in secondary:
            assert before["p_holm"] == after["p_holm"], name
            assert before["estimability"] == after["estimability"], name
        else:
            assert "estimability" not in before
    assert "p_holm" not in plain["contrasts"]["M3 vs M1"]["paired_auroc_difference"]
    assert plain["contrasts"]["M3 vs M1"]["multiplicity"]["note"].startswith("primary contrast")
    assert with_scenarios["contrasts"]["M3 vs M1"]["paired_auroc_difference"]["ci95"] is not None
    assert with_scenarios["interval_contract"]["status_by_contrast"]["M3 vs M1"] == (
        ae.INTERVAL_STATUS_ESTIMATED)


def test_unresolved_label_gate_still_triggers_with_scenario_identity():
    labels = [label_row("t-1", 1, ALPHA), label_row("t-2", 0, ALPHA),
              label_row("t-3", None, BETA, e_status="unresolved"),
              label_row("t-4", None, BETA, e_status="unresolved")]
    rows = [score_row(arm, trace_id, value, GATE_SCENARIOS[trace_id])
            for arm, values in GATE_SCORES.items()
            for trace_id, value in sorted(values.items())]
    report = ae.run_analysis_e(labels, rows, n_boot=50)
    gate = report["unresolved_gate"]
    assert gate["n_assessed"] == 4
    assert gate["n_unresolved"] == 2
    assert gate["rate"] > ae.UNRESOLVED_GATE_THRESHOLD
    assert gate["triggered"] is True
    assert any("TRIGGERED" in warning for warning in report["warnings"])
    # Unresolved rows stay excluded from the primary set and never become negatives.
    assert report["n_traces_reference"] == 2
    assert report["reference"]["n_excluded"] == 2
    primary = report["contrasts"]["M3 vs M1"]["paired_auroc_difference"]
    assert primary["n_pairs"] == 2
    assert "t-3" not in report["missingness"]["absent_trace_ids"].get("M3", [])
