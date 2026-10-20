"""Tests for the trial-counting helper in scripts/analyze_results.py.

Uses synthetic trial dicts only -- nothing under results/ is read or
written by these tests.
"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from analyze_results import counts_by_condition


def _trial(condition, error=None):
    return {"metadata": {"condition": condition}, "error": error}


def test_counts_by_condition_splits_completed_and_failed():
    trials = [
        _trial("communication_enabled"),
        _trial("communication_enabled", error="boom"),
        _trial("communication_disabled"),
        _trial("communication_disabled"),
    ]
    counts = counts_by_condition(trials)
    assert counts["communication_enabled"] == {"total": 2, "completed": 1, "failed": 1}
    assert counts["communication_disabled"] == {"total": 2, "completed": 2, "failed": 0}


def test_counts_by_condition_handles_all_failed_condition():
    trials = [
        _trial("communication_enabled", error="boom"),
        _trial("communication_disabled"),
    ]
    counts = counts_by_condition(trials)
    assert counts["communication_enabled"] == {"total": 1, "completed": 0, "failed": 1}
    assert counts["communication_disabled"] == {"total": 1, "completed": 1, "failed": 0}


def test_counts_by_condition_empty_trials_returns_empty_dict():
    assert counts_by_condition([]) == {}
