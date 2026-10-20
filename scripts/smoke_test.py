"""Runs a tiny end-to-end mock trial through the full pipeline and checks it.

Run from the project root: python scripts/smoke_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from apart.conditions import ALL_CONDITIONS
from apart.runner import run_trials
from apart.storage import save_trials, load_trials
from apart.evaluation import outcome_rate_by_condition

RESULTS_PATH = str(ROOT / "results" / "smoke_test.jsonl")


def main():
    trials = run_trials(list(ALL_CONDITIONS), seeds=[0, 1])
    errors = [t for t in trials if t.error is not None]
    assert not errors, f"Smoke test trial(s) failed: {errors}"

    save_trials(trials, RESULTS_PATH)
    loaded = load_trials(RESULTS_PATH)
    assert len(loaded) == len(trials), "Saved and loaded trial counts do not match."

    rates = outcome_rate_by_condition(loaded)
    print("Smoke test passed.")
    print(f"Ran {len(trials)} trials across conditions: {ALL_CONDITIONS}")
    print(f"Mutual-cooperation rate by condition: {rates}")


if __name__ == "__main__":
    main()
