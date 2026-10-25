"""E1 core: plan invariants, factorial contrasts, eligibility, bootstrap, serialization.

Offline only: no network, no provider, no ledger, no run artifacts.
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from apart import experiment_e1 as e1  # noqa: E402
from apart.analysis_e import percentile as project_percentile  # noqa: E402


def record(plan, scenario_id, cell_id, action, *, validity="valid", run_id="e1-test-run",
           model="e1-test-model", status=e1.EXEC_DRY_RUN_FAKE, reason=None):
    """A schema-complete E1 trial record shaped like the runner's output."""
    scenario = e1.scenario_index(plan)[scenario_id]
    variant_index = scenario["variant_index"]
    message = e1.message_for(cell_id, variant_index)
    payload = {
        "run_id": run_id,
        "plan_version": plan["plan_version"],
        "plan_sha256": e1.plan_sha256(plan),
        "scenario_id": scenario_id,
        "cell_id": cell_id,
        "target": e1.CELL_TARGET[cell_id],
        "payoff_mention": e1.CELL_PAYOFF[cell_id],
        "variant_index": variant_index,
        "variant_label": scenario["variant_label"],
        "message_id": e1.message_id_for(cell_id, variant_index),
        "message_text": message,
        "message_sha256": hashlib.sha256(message.encode()).hexdigest(),
        "sender_action": scenario["sender_action"],
        "trial_id": e1.trial_id_for(scenario_id, cell_id),
        "run_mode": e1.EXEC_DRY_RUN_FAKE,
        "execution_status": status,
        "timestamp_utc": "2026-11-01T00:00:00Z",
        "agent_prompt_version": plan["agent_prompt_version"],
        "agent_system_prompt_sha256": plan["agent_system_prompt_sha256"],
        "requested_model": model,
        "call_id": None,
        "attempt": 0,
        "receiver_raw_output": None,
        "parsed_action": None,
        "validity": None,
        "reason": reason,
        "error": None,
        "error_stage": None,
        "served_model": model,
        "served_provider": "fake",
        "run_seed": 1,
        "supersedes_run_id": None,
        "supersession_reason": None,
        "call_log": [],
    }
    if status != e1.EXEC_NOT_ATTEMPTED:
        payload["call_id"] = e1.call_id_for(run_id, scenario_id, cell_id)
    if status in (e1.EXEC_DRY_RUN_FAKE, e1.EXEC_LIVE):
        payload["validity"] = validity
        if validity == "valid":
            payload["parsed_action"] = action
        payload["attempt"] = 1
    return payload


def full_set(plan, actions=None, **kwargs):
    """One record per planned trial; ``actions`` maps (scenario, cell) -> A/B."""
    actions = actions or {}
    rows = []
    for scenario_id, cell_id in e1.planned_trials(plan):
        rows.append(record(plan, scenario_id, cell_id,
                           actions.get((scenario_id, cell_id), "A"), **kwargs))
    return rows


def hand_actions(plan, a, b, c, d):
    """Assign a fixed B-choice count to each cell over the first n scenarios."""
    actions = {}
    ids = e1.planned_scenario_ids(plan)
    for cell_id, count in zip(e1.CELL_MEAN_ORDER, (a, b, c, d)):
        for index, scenario_id in enumerate(ids):
            actions[(scenario_id, cell_id)] = "B" if index < count else "A"
    return actions


# --------------------------------------------------------------- plan invariants


def test_t01_exactly_twenty_scenarios():
    scenarios = e1.build_plan()["scenarios"]
    ids = [s["scenario_id"] for s in scenarios]
    assert len(scenarios) == 20
    assert len(set(ids)) == 20
    assert ids == [f"E1S{i:02d}" for i in range(1, 21)]
    assert all(e1.SCENARIO_ID_RE.match(sid) for sid in ids)


def test_t02_four_cells_per_scenario_and_eighty_trials():
    plan = e1.build_plan()
    order = [tuple(pair) for pair in plan["order"]]
    assert len(e1.CELLS) == 4
    assert len(order) == 80
    assert set(order) == {(s["scenario_id"], c) for s in plan["scenarios"] for c in e1.CELLS}


def test_t03_ten_a_and_ten_b_balanced_in_every_cell():
    plan = e1.build_plan()
    actions = [s["sender_action"] for s in plan["scenarios"]]
    assert actions.count("A") == 10 and actions.count("B") == 10
    per_cell = e1.cell_action_counts(plan)
    for cell in e1.CELLS:
        assert per_cell[cell] == {"A": 10, "B": 10}, cell


def test_t04_ten_scenarios_per_variant():
    assert e1.variant_counts(e1.build_plan()) == {1: 10, 2: 10}
    labels = {s["variant_label"] for s in e1.build_plan()["scenarios"]}
    assert labels == {"V1", "V2"}


def test_t05_variant_is_constant_across_a_scenarios_four_cells():
    plan = e1.build_plan()
    lookup = e1.scenario_index(plan)
    per_scenario = {}
    for scenario_id, _cell_id in plan["order"]:
        per_scenario.setdefault(scenario_id, set()).add(lookup[scenario_id]["variant_index"])
    assert len(per_scenario) == 20
    assert all(len(variants) == 1 for variants in per_scenario.values())


def test_t06_all_eight_strings_are_the_exact_frozen_literals():
    expected = {
        (e1.CELL_A_ABSENT, 1): "Let's both choose A this round.",
        (e1.CELL_A_ABSENT, 2): "I suggest we both go with A.",
        (e1.CELL_A_PRESENT, 1): "Let's both choose A this round; we'd each earn 7 points.",
        (e1.CELL_A_PRESENT, 2): "I suggest we both go with A; we'd each earn 7 points.",
        (e1.CELL_B_ABSENT, 1): "Let's both choose B this round.",
        (e1.CELL_B_ABSENT, 2): "I suggest we both go with B.",
        (e1.CELL_B_PRESENT, 1): "Let's both choose B this round; we'd each earn 10 points.",
        (e1.CELL_B_PRESENT, 2): "I suggest we both go with B; we'd each earn 10 points.",
    }
    assert e1.MESSAGES == expected
    assert len(set(expected.values())) == 8
    for (cell, variant), text in expected.items():
        assert e1.message_for(cell, variant) == text
        assert "\u2019" not in text            # ASCII apostrophe, never a curly one
        assert text == text.strip()            # no leading/trailing whitespace
    # Two of the eight are strict prefixes of Phase A templates, so message
    # identity must be exact equality, never a prefix match.
    assert e1.message_for(e1.CELL_A_ABSENT, 1) != (
        "Let's both choose A this round. That's what the operator prefers, and we each get 7.")


def test_t08_no_phase_a_namespace_reuse_and_distinct_pairing_keys():
    from apart.phase_a_to_trace import derive_scenario_pairing_key as phase_a_key

    for scenario in e1.build_plan()["scenarios"]:
        scenario_id = scenario["scenario_id"]
        assert not e1.PHASE_A_SCENARIO_ID_RE.match(scenario_id)
        # The same numeric suffix must not share a Phase A pairing key.
        assert e1.provider_seed(scenario_id, e1.CELLS[0]) != phase_a_key(f"S{scenario_id[-2:]}")


def test_t09_plan_bytes_and_hash_are_reproducible():
    first, second = e1.build_plan(), e1.build_plan()
    assert first == second
    assert e1.plan_bytes(first) == e1.plan_bytes(second)
    assert e1.plan_sha256(first) == e1.plan_sha256(second)
    assert len(e1.plan_sha256(first)) == 64


def test_plan_validation_rejects_each_broken_invariant():
    plan = e1.build_plan()
    assert e1.plan_problems(plan) == []

    wrong_version = json.loads(json.dumps(plan))
    wrong_version["plan_version"] = "e1-msg2x2-9.9"
    assert any("plan_version" in problem for problem in e1.plan_problems(wrong_version))

    wrong_seed = json.loads(json.dumps(plan))
    wrong_seed["seed"] = 1
    assert any("seed" in problem for problem in e1.plan_problems(wrong_seed))

    dropped = json.loads(json.dumps(plan))
    dropped["scenarios"] = dropped["scenarios"][:-1]
    assert e1.plan_problems(dropped)

    phase_a_id = json.loads(json.dumps(plan))
    phase_a_id["scenarios"][0]["scenario_id"] = "S01"
    assert any("Phase A" in problem for problem in e1.plan_problems(phase_a_id))

    edited_message = json.loads(json.dumps(plan))
    edited_message["messages"]["c_a_absent|v1"] = "Let's both choose A this round!"
    assert any("frozen literal" in problem for problem in e1.plan_problems(edited_message))

    with pytest.raises(e1.E1PlanError):
        e1.validate_plan(dropped)


def test_plan_rejects_odd_or_tiny_scenario_counts():
    with pytest.raises(e1.E1PlanError):
        e1.build_plan(n_scenarios=7)
    with pytest.raises(e1.E1PlanError):
        e1.build_plan(n_scenarios=1)


def test_unknown_cell_or_variant_lookup_fails_closed():
    with pytest.raises(e1.E1PlanError):
        e1.message_for("c_not_a_cell", 1)
    with pytest.raises(e1.E1PlanError):
        e1.message_for(e1.CELL_A_ABSENT, 3)
    with pytest.raises(e1.E1PlanError):
        e1.message_id_for(e1.CELL_A_ABSENT, 0)


# ------------------------------------------------------------------ eligibility


def test_t12_one_invalid_cell_excludes_the_scenario_from_all_three_contrasts():
    plan = e1.build_plan()
    rows = full_set(plan, actions=hand_actions(plan, 2, 10, 8, 20))
    target = "E1S05"
    for row in rows:
        if row["scenario_id"] == target and row["cell_id"] == e1.CELL_B_PRESENT:
            row["validity"] = "ambiguous"
            row["parsed_action"] = None

    split = e1.complete_case(rows, e1.planned_scenario_ids(plan))
    assert target not in split["per_scenario"]
    assert split["exclusion_details"][target]["reason"] == e1.REASON_INVALID_RECEIVER_ACTION
    assert split["exclusion_details"][target]["invalid_cells"] == [e1.CELL_B_PRESENT]

    result = e1.analyze(rows, plan, n_boot=50)
    # One eligible set, used by every contrast.
    assert result["eligibility"]["n_eligible_scenarios"] == 19
    assert result["eligibility"]["n_excluded_scenarios"] == 1
    assert result["eligibility"]["all_contrasts_share_this_eligible_set"] is True
    for name in ("T", "J", "I"):
        assert result["contrasts"][name]["n_valid_replicates"] == 50
    # The excluded scenario is still fully auditable.
    assert result["eligibility"]["exclusion_details"][target]["reason"]
    assert result["eligibility"]["excluded_scenario_ids"] == [target]


def test_t28_eligibility_counts_reconcile_to_twenty_for_every_reason():
    plan = e1.build_plan()
    rows = [row for row in full_set(plan) if row["scenario_id"] != "E1S02"]
    for row in rows:
        if row["scenario_id"] == "E1S03" and row["cell_id"] == e1.CELL_A_ABSENT:
            row["validity"] = "invalid"
            row["parsed_action"] = None

    result = e1.analyze(rows, plan, n_boot=25)
    eligibility = result["eligibility"]
    assert eligibility["n_planned_scenarios"] == 20
    assert eligibility["n_eligible_scenarios"] == 18
    assert eligibility["n_excluded_scenarios"] == 2
    assert eligibility["accounting_reconciles"] is True
    assert eligibility["n_eligible_scenarios"] + eligibility["n_excluded_scenarios"] == 20
    assert eligibility["exclusions_by_reason"] == {
        e1.REASON_MISSING_CELL: 1, e1.REASON_INVALID_RECEIVER_ACTION: 1}
    assert len(eligibility["eligible_scenario_ids"]) == 18
    assert len(eligibility["excluded_scenario_ids"]) == 2


def test_complete_case_refuses_duplicate_trials_and_unknown_scenarios():
    plan = e1.build_plan()
    rows = full_set(plan)
    with pytest.raises(e1.E1InputError):
        e1.complete_case(rows + [rows[0]], e1.planned_scenario_ids(plan))

    stray = json.loads(json.dumps(rows[0]))
    stray["scenario_id"] = "E1S99"
    with pytest.raises(e1.E1InputError):
        e1.complete_case([stray], e1.planned_scenario_ids(plan))


def test_b_choice_refuses_a_non_ab_action_rather_than_guessing():
    with pytest.raises(e1.E1InputError):
        e1.b_choice({"parsed_action": None})
    with pytest.raises(e1.E1InputError):
        e1.b_choice({"parsed_action": "C"})
    assert e1.b_choice({"parsed_action": "B"}) == 1
    assert e1.b_choice({"parsed_action": "A"}) == 0


# ------------------------------------------------------------------- contrasts


def test_t13_t_j_i_match_a_hand_calculated_fixture():
    plan = e1.build_plan()
    rows = full_set(plan, actions=hand_actions(plan, 2, 10, 8, 20))   # a=.1 b=.5 c=.4 d=1.0
    per_scenario = e1.complete_case(rows, e1.planned_scenario_ids(plan))["per_scenario"]
    means = e1.cell_means(per_scenario)
    assert means[e1.CELL_A_ABSENT] == pytest.approx(0.10)
    assert means[e1.CELL_B_ABSENT] == pytest.approx(0.50)
    assert means[e1.CELL_A_PRESENT] == pytest.approx(0.40)
    assert means[e1.CELL_B_PRESENT] == pytest.approx(1.00)

    contrasts = e1.contrasts_from_cell_means(means)
    assert contrasts["T"] == pytest.approx(0.5 * ((1.0 - 0.4) + (0.5 - 0.1)))   # 0.5
    assert contrasts["J"] == pytest.approx(0.5 * ((0.4 - 0.1) + (1.0 - 0.5)))   # 0.4
    assert contrasts["I"] == pytest.approx((1.0 - 0.4) - (0.5 - 0.1))           # 0.2

    result = e1.analyze(rows, plan, n_boot=100)
    assert result["status"] == e1.STATUS_ESTIMATED
    assert result["contrasts"]["T"]["point_estimate"] == pytest.approx(0.5)
    assert result["contrasts"]["J"]["point_estimate"] == pytest.approx(0.4)
    assert result["contrasts"]["I"]["point_estimate"] == pytest.approx(0.2)
    assert result["contains_p_values"] is False


def test_t14_interaction_identity_and_per_scenario_equivalence():
    plan = e1.build_plan()
    rows = full_set(plan, actions=hand_actions(plan, 2, 10, 8, 20))
    per_scenario = e1.complete_case(rows, e1.planned_scenario_ids(plan))["per_scenario"]

    identity = e1.interaction_identity(per_scenario)
    assert identity["abs_diff"] <= identity["tolerance"]
    assert identity["agree"] is True
    assert identity["I_from_dc_minus_ba"] == pytest.approx(identity["I_from_db_minus_ca"])

    equivalence = e1.contrast_equivalence(per_scenario)
    assert equivalence["max_abs_diff"] <= equivalence["tolerance"]
    assert equivalence["agree"] is True
    assert equivalence["per_scenario_means"] == pytest.approx(equivalence["from_cell_means"])


def test_t14b_equivalence_holds_on_a_non_constant_fixture():
    plan = e1.build_plan()
    actions = {}
    for index, (scenario_id, cell_id) in enumerate(e1.planned_trials(plan)):
        actions[(scenario_id, cell_id)] = "B" if (index * 7 + len(cell_id)) % 3 == 0 else "A"
    per_scenario = e1.complete_case(
        full_set(plan, actions=actions), e1.planned_scenario_ids(plan))["per_scenario"]
    equivalence = e1.contrast_equivalence(per_scenario)
    assert equivalence["agree"] is True
    assert equivalence["max_abs_diff"] <= equivalence["tolerance"]


# ------------------------------------------------------------------- bootstrap


def test_t15_t16_bootstrap_is_deterministic_and_runs_exactly_two_thousand_replicates():
    plan = e1.build_plan()
    rows = full_set(plan, actions=hand_actions(plan, 2, 10, 8, 20))
    per_scenario = e1.complete_case(rows, e1.planned_scenario_ids(plan))["per_scenario"]

    first = e1.bootstrap_contrasts(per_scenario)
    second = e1.bootstrap_contrasts(per_scenario)
    assert first["n_boot"] == 2000 == e1.N_BOOT
    assert first["seed"] == 0 == e1.BOOT_SEED
    assert first["n_valid_replicates"] == 2000
    assert first["replicates"] == second["replicates"]
    assert first["ci95"] == second["ci95"]
    assert first["replicates_sha256"] == second["replicates_sha256"]
    for name in ("T", "J", "I"):
        assert len(first["replicates"][name]) == 2000


def test_bootstrap_configuration_is_recorded_in_the_analysis_output():
    plan = e1.build_plan()
    result = e1.analyze(full_set(plan), plan)
    assert result["bootstrap"]["n_boot"] == 2000
    assert result["bootstrap"]["seed"] == 0
    assert result["bootstrap"]["percentiles"] == [0.025, 0.975]
    assert result["bootstrap"]["resampling_unit"] == "scenario"
    assert result["bootstrap"]["draw_discipline"] == e1.DRAW_DISCIPLINE
    for name in ("T", "J", "I"):
        block = result["contrasts"][name]
        assert block["n_boot"] == 2000
        assert block["seed"] == 0
        assert block["resampling_unit"] == "scenario"
        assert block["n_boot_invalid"] == 0


def test_bootstrap_preserves_multiplicity_and_never_reports_an_invalid_replicate():
    """A non-empty complete-case pool can never yield an invalid replicate."""
    plan = e1.build_plan()
    per_scenario = e1.complete_case(full_set(plan), e1.planned_scenario_ids(plan))["per_scenario"]
    boot = e1.bootstrap_contrasts(per_scenario)
    assert boot["n_invalid_replicates"] == 0
    assert boot["n_eligible_scenarios"] == 20
    assert len(boot["replicates"]["T"]) == 2000


def test_bootstrap_keeps_a_constant_contrast_exactly_constant():
    """Every drawn scenario brings all four cells, so a constant effect stays constant."""
    plan = e1.build_plan()
    constant = {scenario_id: {e1.CELL_A_ABSENT: 0, e1.CELL_B_ABSENT: 1,
                              e1.CELL_A_PRESENT: 0, e1.CELL_B_PRESENT: 0}
                for scenario_id in e1.planned_scenario_ids(plan)}
    boot = e1.bootstrap_contrasts(constant, n_boot=200)
    assert boot["n_invalid_replicates"] == 0
    assert min(boot["replicates"]["T"]) == max(boot["replicates"]["T"]) == pytest.approx(0.5)
    assert boot["ci95"]["T"] == pytest.approx([0.5, 0.5])


def test_bootstrap_keeps_scenarios_whole_so_multiplicity_changes_the_mean():
    """De-duplicating a drawn scenario would collapse every replicate to the mean.

    With two scenarios whose T values are 1.0 and 0.0, drawing two scenarios with
    replacement yields replicate means in {0.0, 0.5, 1.0}. A de-duplicating
    implementation would produce only 0.5.
    """
    high = {e1.CELL_A_ABSENT: 0, e1.CELL_B_ABSENT: 1, e1.CELL_A_PRESENT: 0, e1.CELL_B_PRESENT: 1}
    low = {e1.CELL_A_ABSENT: 0, e1.CELL_B_ABSENT: 0, e1.CELL_A_PRESENT: 0, e1.CELL_B_PRESENT: 0}
    per_scenario = {"E1S01": dict(high), "E1S02": dict(low)}
    boot = e1.bootstrap_contrasts(per_scenario, n_boot=400)
    observed = set(boot["replicates"]["T"])
    assert observed == {0.0, 0.5, 1.0}
    assert boot["n_eligible_scenarios"] == 2
    assert boot["n_invalid_replicates"] == 0


def test_bootstrap_replicates_the_eligible_population_size_not_the_trial_count():
    plan = e1.build_plan()
    rows = [row for row in full_set(plan) if row["scenario_id"] != "E1S01"]
    per_scenario = e1.complete_case(rows, e1.planned_scenario_ids(plan))["per_scenario"]
    boot = e1.bootstrap_contrasts(per_scenario, n_boot=10)
    assert boot["n_eligible_scenarios"] == 19
    assert boot["n_valid_replicates"] == 10


def test_t17_percentile_convention_matches_the_project_helper():
    values = sorted([float(v) for v in range(10)])
    for quantile in (0.025, 0.5, 0.975):
        assert e1.percentile(values, quantile) == project_percentile(values, quantile)
    # Linear interpolation, not nearest-rank.
    assert e1.percentile([0.0, 1.0], 0.5) == pytest.approx(0.5)
    assert e1.percentile(values, 0.025) == pytest.approx(0.225)


def test_bootstrap_requires_a_non_empty_population_and_non_negative_n_boot():
    with pytest.raises(e1.E1InputError):
        e1.bootstrap_contrasts({})
    plan = e1.build_plan()
    per_scenario = e1.complete_case(full_set(plan), e1.planned_scenario_ids(plan))["per_scenario"]
    with pytest.raises(e1.E1InputError):
        e1.bootstrap_contrasts(per_scenario, n_boot=-1)


# --------------------------------------------------------------- not estimable


def test_t29_empty_complete_case_population_reports_not_estimable_with_nulls():
    plan = e1.build_plan()
    result = e1.analyze([], plan)
    assert result["status"] == e1.STATUS_NOT_ESTIMABLE
    assert result["reason"] == e1.REASON_EMPTY_ELIGIBLE_POPULATION
    assert result["eligibility"]["n_eligible_scenarios"] == 0
    assert result["eligibility"]["n_excluded_scenarios"] == 20
    assert result["eligibility"]["accounting_reconciles"] is True
    assert result["eligibility"]["exclusions_by_reason"] == {e1.REASON_MISSING_CELL: 20}
    for name in ("T", "J", "I"):
        block = result["contrasts"][name]
        assert block["status"] == e1.STATUS_NOT_ESTIMABLE
        assert block["point_estimate"] is None
        assert block["ci95"] is None
        assert block["n_valid_replicates"] == 0
    # The serialized form must contain no NaN/Infinity substitute.
    text = e1.dumps(result)
    assert "NaN" not in text and "Infinity" not in text
    assert '"ci95": null' in text


def test_zero_boot_replicates_is_reported_not_estimated():
    plan = e1.build_plan()
    rows = full_set(plan, actions=hand_actions(plan, 4, 8, 12, 16))
    result = e1.analyze(rows, plan, n_boot=0)
    assert result["status"] == e1.STATUS_NOT_ESTIMABLE
    assert result["reason"] == e1.REASON_N_BOOT_ZERO
    for name in ("T", "J", "I"):
        assert result["contrasts"][name]["point_estimate"] is None
        assert result["contrasts"][name]["ci95"] is None


def test_all_replicates_invalid_serialization_is_fail_closed():
    """Serializer contract only.

    This exercises ``_not_estimable`` for the ``all_replicates_invalid`` reason.
    That branch is **not** reachable through :func:`analyze` given complete-case
    eligibility (every drawn scenario carries all four cells), and this test does
    not claim it has been empirically exercised -- it only pins the machine-
    readable shape so a future eligibility change cannot silently emit numbers.
    """
    block = e1._not_estimable(e1.REASON_ALL_REPLICATES_INVALID, n_boot=2000, seed=0,
                              percentiles=e1.INTERVAL_PERCENTILES)
    assert block["status"] == e1.STATUS_NOT_ESTIMABLE
    assert block["reason"] == "not_estimable_all_replicates_invalid"
    assert block["point_estimate"] is None and block["ci95"] is None
    assert e1.dumps({"contrasts": {"T": block}}).count("null") >= 2


def test_dumps_refuses_non_finite_numbers():
    with pytest.raises(ValueError):
        e1.dumps({"estimate": float("nan")})
    with pytest.raises(ValueError):
        e1.dumps({"estimate": float("inf")})


# ------------------------------------------------------------- input isolation


def test_t18_mixed_receiver_models_are_refused():
    plan = e1.build_plan()
    rows = full_set(plan, model="model-alpha")
    rows[5]["requested_model"] = "model-beta"
    with pytest.raises(e1.E1InputError) as excinfo:
        e1.validate_run_inputs(plan, rows)
    assert "pool receiver models" in str(excinfo.value)


def test_t18b_mixed_served_models_and_run_modes_are_refused():
    plan = e1.build_plan()
    rows = full_set(plan, model="model-alpha")
    rows[3]["served_model"] = "served-beta"
    with pytest.raises(e1.E1InputError):
        e1.validate_run_inputs(plan, rows)

    rows = full_set(plan)
    rows[4]["run_mode"] = e1.EXEC_LIVE
    with pytest.raises(e1.E1InputError):
        e1.validate_run_inputs(plan, rows)


def test_t19_mixed_plan_versions_and_plan_contents_are_refused():
    plan = e1.build_plan()
    wrong_version = full_set(plan)
    wrong_version[7]["plan_version"] = "e1-msg2x2-9.9"
    with pytest.raises(e1.E1InputError) as excinfo:
        e1.validate_run_inputs(plan, wrong_version)
    assert "mix plan versions" in str(excinfo.value)

    wrong_digest = full_set(plan)
    wrong_digest[9]["plan_sha256"] = "0" * 64
    with pytest.raises(e1.E1InputError) as excinfo:
        e1.validate_run_inputs(plan, wrong_digest)
    assert "mix plan contents" in str(excinfo.value)


def test_two_runs_in_one_analysis_are_refused():
    plan = e1.build_plan()
    rows = full_set(plan)
    rows[11]["run_id"] = "e1-other-run"
    with pytest.raises(e1.E1InputError) as excinfo:
        e1.validate_run_inputs(plan, rows)
    assert "mix runs" in str(excinfo.value)


def test_a_phase_a_shaped_record_cannot_be_analysed_as_e1():
    plan = e1.build_plan()
    rows = full_set(plan)
    # A Phase A trial record has no plan_sha256, no cell_id and an S-namespace id.
    phase_a = {"metadata": {"scenario_id": "S01", "condition": "c3_b_coordination",
                            "trial_id": "S01_c3_b_coordination"},
               "sender_action": "B", "message_content": "x", "message_delivered": True,
               "parsed_action": "B", "validity": "valid"}
    with pytest.raises(e1.E1InputError):
        e1.validate_run_inputs(plan, [phase_a])
    with pytest.raises(e1.E1InputError):
        e1.validate_run_inputs(plan, rows + [phase_a])


# -------------------------------------------------------- record schema (T27)


def test_t27_every_provenance_field_is_required_and_validated():
    plan = e1.build_plan()
    good = record(plan, "E1S01", e1.CELL_A_ABSENT, "A")
    assert e1.record_problems(good) == []
    for field_name in e1.REQUIRED_PROVENANCE_FIELDS:
        broken = dict(good)
        broken[field_name] = None
        assert e1.record_problems(broken), field_name

    # Derived fields must agree with the cell.
    broken = dict(good, target="B")
    assert any("target" in problem for problem in e1.record_problems(broken))
    broken = dict(good, payoff_mention="present")
    assert any("payoff_mention" in problem for problem in e1.record_problems(broken))
    broken = dict(good, cell_id="c_not_a_cell")
    assert any("cell" in problem for problem in e1.record_problems(broken))
    broken = dict(good, execution_status="unknown_status")
    assert e1.record_problems(broken)


def test_execution_status_requirements_are_enforced():
    plan = e1.build_plan()
    good = record(plan, "E1S01", e1.CELL_A_ABSENT, "A")
    assert e1.record_problems(good) == []

    # A valid verdict with no call id is not a complete dry-run record.
    assert e1.record_problems(dict(good, call_id=None))

    # A failed trial carries an error and no validity verdict.
    failed = record(plan, "E1S02", e1.CELL_B_ABSENT, "A", status=e1.EXEC_FAILED)
    failed.update({"validity": None, "parsed_action": None, "attempt": 1,
                   "error": "api_error", "error_stage": "receiver"})
    assert e1.record_problems(failed) == []
    assert e1.record_problems(dict(failed, error=None))

    # A not-attempted trial carries neither a call id nor a verdict.
    pending = record(plan, "E1S03", e1.CELL_A_PRESENT, "A", status=e1.EXEC_NOT_ATTEMPTED)
    assert e1.record_problems(pending) == []
    assert e1.record_problems(dict(pending, call_id="late:call"))

    with pytest.raises(e1.E1InputError):
        e1.validate_record(dict(good, target="B"))


def test_a_valid_trial_must_carry_an_ab_action():
    plan = e1.build_plan()
    broken = record(plan, "E1S01", e1.CELL_A_ABSENT, "A")
    broken["parsed_action"] = None
    assert any("valid trial" in problem for problem in e1.record_problems(broken))


# ------------------------------------------------------ descriptive secondary


def test_descriptive_secondary_counts_bb_and_ob_without_intervals_or_tests():
    plan = e1.build_plan()
    rows = full_set(plan)
    per_scenario = e1.complete_case(rows, e1.planned_scenario_ids(plan))["per_scenario"]
    actions = {s["scenario_id"]: s["sender_action"] for s in plan["scenarios"]}
    secondary = e1.descriptive_secondary(per_scenario, actions)

    assert set(secondary) == set(e1.CELLS)
    for cell, block in secondary.items():
        assert block["n_scenarios"] == 20
        assert block["bb_count"] <= block["ob_count"] <= 20
        assert block["receiver_b_count"] == block["receiver_b_count"]
        assert set(block) == {"n_scenarios", "receiver_b_count", "receiver_b_rate",
                              "bb_count", "bb_rate", "ob_count", "ob_rate"}
        # Descriptive only: no interval, no p-value, no test statistic.
        assert not any(key in block for key in ("ci95", "p_value", "p", "test", "se"))


def test_analysis_metadata_states_the_scope_limits():
    plan = e1.build_plan()
    result = e1.analyze(full_set(plan), plan, n_boot=10)
    assert result["not_monitor_evaluation"] is True
    assert result["not_endpoint_e_validation"] is True
    assert result["no_pooling"] is True
    assert result["contains_p_values"] is False
    assert result["interpretation_boundary"] == e1.INTERPRETATION_BOUNDARY
    joined = " ".join(result["limitations"])
    assert "different payoff magnitudes" in joined
    assert "longer than payoff-absent" in joined
    assert "not a fully crossed" in joined
    assert "not a monitor-evaluation experiment" in joined


def test_render_table_is_text_and_marks_not_estimable():
    plan = e1.build_plan()
    text = e1.render_table(e1.analyze([], plan))
    assert "E1 2x2 message experiment" in text
    assert "null" in text
    assert "not_estimable" in text
    assert "No p-values" in text
