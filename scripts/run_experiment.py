"""Runs a small configurable mock experiment and saves raw trial results.

Run from the project root: python scripts/run_experiment.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from config import ExperimentConfig
from apart.runner import run_trials
from apart.storage import save_trials


def main():
    config = ExperimentConfig()
    seeds = list(range(config.num_seeds))
    trials = run_trials(list(config.conditions), seeds)

    output_path = str(ROOT / config.output_dir / "raw_trials.jsonl")
    save_trials(trials, output_path)

    failed = sum(1 for t in trials if t.error is not None)
    print(f"Ran {len(trials)} trials, {failed} failed. Saved to {output_path}.")


if __name__ == "__main__":
    main()
