"""Tests for the protected Phase A storage wrapper: resolved-path
protection against the legacy results destination, and explicit
permission before overwriting existing Phase A results."""
import os

import pytest

from apart.phase_a_models import PhaseATrialMetadata, PhaseATrialResult
from apart.phase_a_storage import (
    _LEGACY_RESULTS_PATH,
    load_phase_a_trials,
    save_phase_a_trials,
)


def _make_trial(scenario_id="s1", condition="no_message"):
    metadata = PhaseATrialMetadata(
        scenario_id=scenario_id, condition=condition, trial_id=f"{scenario_id}_{condition}"
    )
    return PhaseATrialResult(
        metadata=metadata, sender_action="B", message_content="", message_delivered=False
    )


@pytest.fixture(autouse=True, scope="module")
def _verify_legacy_file_untouched():
    """Brackets every test in this module: the legacy results file's
    content and mtime must be identical before and after this module's
    tests run, regardless of what the Phase A storage wrapper is tested
    against."""
    before_mtime = os.path.getmtime(_LEGACY_RESULTS_PATH)
    with open(_LEGACY_RESULTS_PATH, "r", encoding="utf-8") as f:
        before_content = f.read()
    yield
    after_mtime = os.path.getmtime(_LEGACY_RESULTS_PATH)
    with open(_LEGACY_RESULTS_PATH, "r", encoding="utf-8") as f:
        after_content = f.read()
    assert after_mtime == before_mtime
    assert after_content == before_content


def test_round_trip_through_wrapper(tmp_path):
    path = str(tmp_path / "phase_a_results.jsonl")
    trials = [_make_trial()]
    save_phase_a_trials(trials, path=path)
    loaded = load_phase_a_trials(path=path)
    assert len(loaded) == 1
    assert loaded[0]["sender_action"] == "B"


def test_rejects_direct_legacy_path():
    with pytest.raises(ValueError):
        save_phase_a_trials([_make_trial()], path=_LEGACY_RESULTS_PATH)


def test_rejects_legacy_path_via_indirect_route():
    indirect = os.path.join("results", "..", "results", "raw_trials.jsonl")
    assert os.path.realpath(indirect) == os.path.realpath(_LEGACY_RESULTS_PATH)
    with pytest.raises(ValueError):
        save_phase_a_trials([_make_trial()], path=indirect)


def test_refuses_to_overwrite_existing_file_without_flag(tmp_path):
    path = str(tmp_path / "phase_a_results.jsonl")
    save_phase_a_trials([_make_trial()], path=path)
    with pytest.raises(FileExistsError):
        save_phase_a_trials([_make_trial(scenario_id="s2")], path=path)


def test_overwrite_true_replaces_existing_file(tmp_path):
    path = str(tmp_path / "phase_a_results.jsonl")
    save_phase_a_trials([_make_trial(scenario_id="s1")], path=path)
    save_phase_a_trials([_make_trial(scenario_id="s2")], path=path, overwrite=True)
    loaded = load_phase_a_trials(path=path)
    assert len(loaded) == 1
    assert loaded[0]["metadata"]["scenario_id"] == "s2"


def test_new_path_requires_no_overwrite_flag(tmp_path):
    path = str(tmp_path / "brand_new.jsonl")
    save_phase_a_trials([_make_trial()], path=path)  # should not raise
