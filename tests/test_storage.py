"""Tests for JSONL save/load round-tripping."""
from apart.models import TrialMetadata, TrialResult
from apart.storage import save_trials, load_trials


def test_round_trip(tmp_path):
    metadata = TrialMetadata(trial_id="t1", condition="communication_enabled", seed=0)
    trial = TrialResult(metadata=metadata, outcome="mutual_cooperation")
    path = str(tmp_path / "trials.jsonl")

    save_trials([trial], path)
    loaded = load_trials(path)

    assert len(loaded) == 1
    assert loaded[0]["outcome"] == "mutual_cooperation"
    assert loaded[0]["metadata"]["trial_id"] == "t1"


def test_creates_missing_directory(tmp_path):
    metadata = TrialMetadata(trial_id="t1", condition="communication_enabled", seed=0)
    trial = TrialResult(metadata=metadata)
    path = tmp_path / "nested" / "trials.jsonl"

    save_trials([trial], str(path))

    assert path.exists()
    assert len(load_trials(str(path))) == 1
