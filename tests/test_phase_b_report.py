"""Tests for the synthetic Phase B report driver (``scripts/run_phase_b_report.py``).

Offline only: no network, no provider, no credentials, no spend, no ledger. The
monitor is ``monitors.client.FakeClient`` with a declared table stand-in, and the
labels come from hard-coded fixture annotations through the approved adapter.

These tests verify that the *pipeline* is wired correctly. They are not evidence
about collusion detection, model performance, or the research question.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from apart import analysis_e as ae

ROOT = Path(__file__).resolve().parents[1]


def _load_driver():
    spec = importlib.util.spec_from_file_location(
        "run_phase_b_report", ROOT / "scripts" / "run_phase_b_report.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def driver():
    return _load_driver()


@pytest.fixture(scope="module")
def pipeline(driver):
    traces = driver.build_synthetic_corpus()
    label_rows = driver.build_label_rows(traces)
    records = driver.run_monitor_arms(traces, "2026-10-23T00:00:00Z")
    return {"traces": traces, "label_rows": label_rows, "records": records}


# ------------------------------------------------------------------ fixture integrity


def test_corpus_traces_are_schema_valid_with_unique_ids(driver):
    traces = driver.build_synthetic_corpus()
    for trace in traces:
        trace.validate()
    ids = [t.trace_id for t in traces]
    assert len(ids) == len(set(ids)) == 10
    assert all(isinstance(i, str) and i for i in ids)


def test_fixture_produces_all_four_reference_states(driver):
    """4 resolved positives, 4 resolved negatives, 1 no_agent_message, 1 partial."""
    rows = driver.build_label_rows(driver.build_synthetic_corpus())
    by_status: dict[str, int] = {}
    positives = negatives = unresolved = 0
    for row in rows:
        by_status[row["evidence_status"]] = by_status.get(row["evidence_status"], 0) + 1
        if row["e"] is True:
            positives += 1
        elif row["e"] is None:
            unresolved += 1
        else:
            negatives += 1
    assert positives == 4
    assert negatives == 5          # 4 resolved negatives + 1 no_agent_message
    assert unresolved == 1
    assert by_status == {"resolved": 8, "no_agent_message": 1, "partial": 1}


def test_an_unresolved_reference_is_none_and_never_zero(driver):
    """``e`` must stay ``None``; an unresolved trace is never read as a negative."""
    rows = {r["trace_id"]: r for r in driver.build_label_rows(driver.build_synthetic_corpus())}
    unresolved = rows["synth-010"]
    assert unresolved["e"] is None
    assert unresolved["e_state"] == "unresolved"
    assert unresolved["evidence_status"] == "partial"   # the slot-level status

    resolved_negative = rows["synth-005"]
    assert resolved_negative["e"] is False
    assert resolved_negative["e_state"] == "negative"
    assert resolved_negative["evidence_status"] == "resolved"


def test_labels_are_marked_synthetic(driver):
    rows = driver.build_label_rows(driver.build_synthetic_corpus())
    assert all(row["synthetic"] is True for row in rows)


def test_no_agent_message_trace_has_no_annotation(driver):
    """A trace with no delivered slot must not be forced into a negative."""
    rows = {r["trace_id"]: r for r in driver.build_label_rows(driver.build_synthetic_corpus())}
    no_msg = rows["synth-009"]
    assert no_msg["evidence_status"] == "no_agent_message"
    assert no_msg["e"] is False          # the *status* carries the exclusion
    assert no_msg["n_delivered"] == 0


def test_the_fixture_responder_is_deterministic(driver):
    responder = driver.make_responder("M1")
    messages = [{"role": "user", "content": driver.PROPOSAL_MESSAGE}]
    first = responder(messages, 0.0, None, 0)
    second = responder(messages, 0.0, None, 0)
    assert first == second


# ------------------------------------------------------------------ arm coverage


def test_all_four_arms_run_and_are_distinct(driver, pipeline):
    views = {r["view"] for r in pipeline["records"]}
    assert views == set(ae.ARMS) == {"M1", "M2", "M3", "M4"}


def test_every_arm_scores_the_same_eligible_population(driver, pipeline):
    """Same trace population per arm -- the whole point of the comparison."""
    by_view: dict[str, set[str]] = {}
    for record in pipeline["records"]:
        by_view.setdefault(record["view"], set()).add(record["trace_id"])
    assert len(set(map(frozenset, by_view.values()))) == 1
    assert by_view["M1"] == {t.trace_id for t in pipeline["traces"]}


def test_records_are_k0_only_and_unique_per_trace_and_view(driver, pipeline):
    keys = [(r["trace_id"], r["view"], r["k"]) for r in pipeline["records"]]
    assert len(keys) == len(set(keys)) == 40
    assert all(r["k"] == 0 for r in pipeline["records"])


def test_trace_identity_survives_the_monitor_pipeline(driver, pipeline):
    for record in pipeline["records"]:
        assert isinstance(record["trace_id"], str) and record["trace_id"]
        assert record["status"] == "ok"
        assert isinstance(record["score"], int)


def test_the_arms_are_not_byte_identical_by_construction(driver, pipeline):
    """M2 sees no message text, so its scores must differ from M1's somewhere."""
    m1 = {r["trace_id"]: r["score"] for r in pipeline["records"] if r["view"] == "M1"}
    m2 = {r["trace_id"]: r["score"] for r in pipeline["records"] if r["view"] == "M2"}
    assert m1 != m2


# ------------------------------------------------------------------ join


def test_labels_and_scores_join_on_trace_id(driver, pipeline):
    label_ids = {row["trace_id"] for row in pipeline["label_rows"]}
    score_ids = {row["trace_id"] for row in pipeline["records"]}
    assert label_ids == score_ids


def test_every_row_carries_a_non_empty_string_trace_id(driver, pipeline):
    for row in pipeline["label_rows"] + pipeline["records"]:
        assert isinstance(row.get("trace_id"), str) and row["trace_id"].strip()


def test_a_row_without_a_trace_id_is_refused(driver, pipeline):
    """No silent fallback to seed: identity is required, not inferred."""
    with pytest.raises(ae.LabelError):
        ae.build_reference([{"e": True, "evidence_status": "resolved"}])


def test_missing_prediction_stays_missing_and_is_not_a_negative(driver, pipeline):
    """Drop every M1 record for one trace; it must be reported absent, not scored 0."""
    victim = sorted({r["trace_id"] for r in pipeline["records"]})[0]
    trimmed = [r for r in pipeline["records"]
               if not (r["view"] == "M1" and r["trace_id"] == victim)]

    scores, missingness = ae.aggregate_arm_scores(
        trimmed, arms=ae.ARMS,
        trace_ids_of_interest=[r["trace_id"] for r in pipeline["label_rows"]],
    )
    assert victim not in scores["M1"]                       # not imputed
    assert victim in missingness["absent_trace_ids"]["M1"]
    assert missingness["absent_trace_ids"]["M3"] == []
    # The other arms are untouched.
    assert victim in scores["M2"] and victim in scores["M3"] and victim in scores["M4"]


def test_a_non_ok_score_record_contributes_no_score(driver, pipeline):
    victim = sorted({r["trace_id"] for r in pipeline["records"]})[0]
    broken = [dict(r, status="api_error", score=None)
              if (r["view"] == "M3" and r["trace_id"] == victim) else r
              for r in pipeline["records"]]
    scores, missingness = ae.aggregate_arm_scores(broken, arms=ae.ARMS)
    assert victim not in scores["M3"]
    assert missingness["n_non_ok"]["M3"] == 1


# ------------------------------------------------------------------ report


def test_report_contains_all_prespecified_contrasts(driver, pipeline):
    report = driver.build_report(pipeline["traces"], pipeline["records"], pipeline["label_rows"])
    assert set(report["contrasts"]) == {"M3 vs M1", "M1 vs M2", "M3 vs M2", "M4 vs M3", "M1 vs M4"}
    assert report["designations"]["primary_contrast"] == ["M3", "M1"]
    assert [list(c) for c in ae.SECONDARY_CONTRASTS] == report["designations"]["secondary_contrasts"]


def test_report_reports_eligible_population_and_exclusions(driver, pipeline):
    report = driver.build_report(pipeline["traces"], pipeline["records"], pipeline["label_rows"])
    reference = report["reference"]
    assert reference["n_label_rows"] == 10
    assert reference["n_usable"] == 8
    assert reference["n_excluded"] == 2
    assert reference["excluded_by_status"] == {"no_agent_message": 1, "unresolved": 1}
    assert report["n_traces_reference"] == 8
    assert set(report["excluded_from_primary"]) == {"no_agent_message", "unresolved"}


def test_report_exposes_degeneracy_rather_than_hiding_it(driver, pipeline):
    report = driver.build_report(pipeline["traces"], pipeline["records"], pipeline["label_rows"])
    assert isinstance(report["warnings"], list) and report["warnings"]
    for block in report["per_arm"].values():
        assert "degenerate" in block
    for block in report["contrasts"].values():
        assert "paired_auroc_difference" in block


def test_report_records_sample_sizes_per_arm(driver, pipeline):
    report = driver.build_report(pipeline["traces"], pipeline["records"], pipeline["label_rows"])
    assert set(report["per_arm"]) == set(ae.ARMS)
    assert report["missingness"]["n_scored"] == {arm: 10 for arm in ae.ARMS}
    assert report["missingness"]["n_non_ok"] == {arm: 0 for arm in ae.ARMS}


def test_report_is_json_serialisable(driver, pipeline):
    report = driver.build_report(pipeline["traces"], pipeline["records"], pipeline["label_rows"])
    assert json.loads(json.dumps(ae._json_safe(report)))


def test_interval_method_is_reported_as_unresolved(driver, pipeline):
    report = driver.build_report(pipeline["traces"], pipeline["records"], pipeline["label_rows"])
    assert "UNRESOLVED" in report["designations"]["interval_method_status"]


# ------------------------------------------------------------------ provenance


def test_report_declares_itself_synthetic(driver, pipeline):
    report = driver.build_report(pipeline["traces"], pipeline["records"], pipeline["label_rows"])
    provenance = report["synthetic_provenance"]
    assert provenance["synthetic"] is True
    assert provenance["n_traces"] == 10
    assert "NOT evidence" in provenance["statement"]
    assert report["report_schema_version"] == driver.REPORT_SCHEMA_VERSION


def test_a_report_without_synthetic_provenance_is_refused(driver, pipeline):
    report = driver.build_report(pipeline["traces"], pipeline["records"], pipeline["label_rows"])
    broken = dict(report)
    broken.pop("synthetic_provenance")
    with pytest.raises(ValueError, match="synthetic provenance"):
        driver.assert_synthetic(broken)


# ------------------------------------------------------------------ persistence


def test_write_report_creates_once_and_refuses_overwrite(driver, pipeline, tmp_path):
    report = driver.build_report(pipeline["traces"], pipeline["records"], pipeline["label_rows"])
    path = driver.write_report(report, tmp_path / "nested" / "phase_b_report.json")
    assert path.exists()
    original = path.read_bytes()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        driver.write_report(report, path)
    assert path.read_bytes() == original


def test_cli_dry_run_writes_nothing(driver, tmp_path):
    target = tmp_path / "out" / "report.json"
    assert driver.main(["--dry-run", "--out", str(target)]) == 0
    assert not target.exists()
    assert not target.parent.exists()


def test_cli_writes_a_complete_report(driver, tmp_path):
    out = tmp_path / "report.json"
    scores = tmp_path / "scores.jsonl"
    assert driver.main(["--out", str(out), "--scores-out", str(scores),
                        "--created-utc", "2026-10-23T00:00:00Z"]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["synthetic_provenance"]["synthetic"] is True
    assert set(report["arms"]) == set(ae.ARMS)
    assert report["reference"]["n_usable"] == 8
    assert (tmp_path / "id_map.csv").exists()
    assert sum(1 for _ in scores.open()) == 40


