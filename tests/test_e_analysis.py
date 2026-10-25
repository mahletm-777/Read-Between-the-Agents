"""Tests for the deprecated ``apart.e_analysis`` compatibility shim.

The approved E-based analysis lives in ``apart.analysis_e`` and has its own suite
(``tests/test_analysis_e.py``). These tests only verify that the shim

  * emits ``DeprecationWarning`` on import,
  * holds no scientific policy of its own (it re-exports the canonical one), and
  * converts the legacy E-record shape with the documented mapping.

No live calls, no network, no writes outside ``tmp_path``.
"""

import importlib
import warnings

import pytest

from apart import analysis_e as canonical


def _shim():
    """Import (or reload) the shim with its import-time warning suppressed."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import apart.e_analysis as shim
        return importlib.reload(shim)


def label(trace_id, e_value, e_status="resolved"):
    return {"trace_id": trace_id, "e_value": e_value, "e_status": e_status}


def scores_for(mapping):
    return [{"view": arm, "seed": seed, "trace_id": f"t-{seed}", "k": 0, "status": "ok",
             "score": value}
            for arm, by_seed in mapping.items()
            for seed, value in sorted(by_seed.items())]


BASE_SCORES = {
    "M1": {1: 90, 2: 10},
    "M2": {1: 10, 2: 90},
    "M3": {1: 90, 2: 10},
    "M4": {1: 90, 2: 10},
}


# ---------------------------------------------------------------- deprecation

def test_import_emits_deprecation_warning():
    import apart.e_analysis  # noqa: F401
    with pytest.warns(DeprecationWarning, match="DEPRECATED"):
        importlib.reload(apart.e_analysis)


def test_run_e_analysis_emits_deprecation_warning():
    shim = _shim()
    records = [{"trace_id": "t-1", "e": True, "evidence_status": "resolved"},
               {"trace_id": "t-2", "e": False, "evidence_status": "resolved"}]
    with pytest.warns(DeprecationWarning, match="deprecated"):
        shim.run_e_analysis(records, scores_for(BASE_SCORES), n_boot=10)


# ---------------------------------------------------------------- no own policy

def test_shim_holds_no_scientific_policy_of_its_own():
    shim = _shim()
    assert shim.DEPRECATED is True
    assert shim.CANONICAL_MODULE == "apart.analysis_e"
    assert shim.PRIMARY_CONTRAST == canonical.PRIMARY_CONTRAST == ("M3", "M1")
    assert shim.PRIMARY_CONTRAST_STATUS == canonical.PRIMARY_CONTRAST_STATUS == "APPROVED"
    assert shim.SECONDARY_CONTRASTS == canonical.SECONDARY_CONTRASTS
    assert shim.CONTRASTS == canonical.CONTRASTS
    assert shim.TAU == canonical.TAU == 50


def test_legacy_names_are_aliases_of_the_canonical_ones():
    shim = _shim()
    assert shim.PRIMARY_PAIR == canonical.PRIMARY_CONTRAST
    assert shim.run_analysis_e is canonical.run_analysis_e
    assert shim.paired_auroc_difference is canonical.paired_auroc_difference


# ---------------------------------------------------------------- adapter

def test_label_row_is_a_pass_through_of_the_contract_shape():
    shim = _shim()
    record = {"trace_id": "t-1", "e": True, "evidence_status": "resolved"}
    assert shim.to_label_row(record) == record


def test_obscured_does_not_exclude_a_positive_record():
    """Contract section 6: E = 1 may coexist with evidence_status = obscured."""
    shim = _shim()
    row = shim.to_label_row({"trace_id": "t-1", "e": True, "evidence_status": "obscured"})
    assert row == {"trace_id": "t-1", "e": True, "evidence_status": "obscured"}
    value, status = canonical.parse_e_label(row)
    assert value == 1
    assert status == "resolved"


def test_no_agent_message_is_kept_distinct_and_is_zero():
    shim = _shim()
    row = shim.to_label_row({"trace_id": "t-2", "e": False,
                             "evidence_status": "no_agent_message"})
    value, status = canonical.parse_e_label(row)
    assert value == 0
    assert status == "no_agent_message"


def test_unresolved_e_is_excluded_not_negative():
    shim = _shim()
    row = shim.to_label_row({"trace_id": "t-3", "e": None,
                             "evidence_status": "partial"})
    value, status = canonical.parse_e_label(row)
    assert value is None
    assert status == "unresolved"


def test_joint_action_is_passed_through_when_present():
    shim = _shim()
    row = shim.to_label_row({"trace_id": "t-1", "e": True,
                             "evidence_status": "resolved", "joint_action": "BB"})
    assert row["joint_action"] == "BB"


def test_missing_required_field_raises():
    shim = _shim()
    with pytest.raises(canonical.LabelError):
        shim.to_label_row({"trace_id": "t-1", "evidence_status": "resolved"})


def test_non_boolean_e_raises():
    shim = _shim()
    with pytest.raises(canonical.LabelError):
        canonical.parse_e_label({"trace_id": "t-1", "e": "yes",
                                 "evidence_status": "resolved"})


def test_unknown_evidence_status_raises():
    shim = _shim()
    with pytest.raises(canonical.LabelError):
        canonical.parse_e_label({"trace_id": "t-1", "e": True,
                                 "evidence_status": "maybe"})


# ---------------------------------------------------------------- delegation

def test_run_e_analysis_returns_the_canonical_report_shape():
    shim = _shim()
    records = [{"trace_id": "t-1", "e": True, "evidence_status": "resolved"},
               {"trace_id": "t-2", "e": False, "evidence_status": "resolved"}]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        report = shim.run_e_analysis(records, scores_for(BASE_SCORES), n_boot=50)
    assert report["designations"]["primary_contrast"] == ["M3", "M1"]
    assert report["primary"]["metric"] == "paired_auroc_difference"
    assert set(report["contrasts"]) == {
        "M3 vs M1", "M1 vs M2", "M3 vs M2", "M4 vs M3", "M1 vs M4"}


def test_run_e_analysis_matches_direct_canonical_call():
    shim = _shim()
    records = [{"trace_id": "t-1", "e": True, "evidence_status": "resolved"},
               {"trace_id": "t-2", "e": False, "evidence_status": "resolved"}]
    rows = [label("t-1", 1), label("t-2", 0)]
    scores = scores_for(BASE_SCORES)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        via_shim = shim.run_e_analysis(records, scores, n_boot=50)
    direct = canonical.run_analysis_e(rows, scores, n_boot=50)
    assert via_shim["per_arm"] == direct["per_arm"]
    assert via_shim["contrasts"] == direct["contrasts"]
