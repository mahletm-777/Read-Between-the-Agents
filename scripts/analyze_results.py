"""Reads saved trial results and reports trial counts, outcome rates, and
communication-effect estimands (tau_y, tau_r) by condition.

Run from the project root: python scripts/analyze_results.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from config import ExperimentConfig
from apart.storage import load_trials
from apart.evaluation import outcome_rate_by_condition, tau_y, tau_r


def counts_by_condition(trials: list[dict]) -> dict:
    """Returns {condition: {"total": n, "completed": n, "failed": n}}.

    A plain count/group-by, not a rate computation, so it's kept here
    rather than added to evaluation.py.
    """
    conditions = sorted({t["metadata"]["condition"] for t in trials})
    counts = {}
    for condition in conditions:
        subset = [t for t in trials if t["metadata"]["condition"] == condition]
        completed = [t for t in subset if t.get("error") is None]
        failed = [t for t in subset if t.get("error") is not None]
        counts[condition] = {
            "total": len(subset),
            "completed": len(completed),
            "failed": len(failed),
        }
    return counts


def main():
    config = ExperimentConfig()
    results_path = str(ROOT / config.output_dir / "raw_trials.jsonl")
    trials = load_trials(results_path)

    total_failed = [t for t in trials if t.get("error") is not None]

    print(f"Input file: {results_path}")
    print(f"Total trials: {len(trials)}")
    print(f"Completed trials: {len(trials) - len(total_failed)}")
    print(f"Failed trials: {len(total_failed)}")

    print("\nTrial counts by condition:")
    for condition, c in counts_by_condition(trials).items():
        print(f"  {condition}: total={c['total']}, completed={c['completed']}, failed={c['failed']}")

    # outcome_rate_by_condition and tau_y/tau_r each exclude errored trials
    # internally, per condition, and report (rather than silently drop) a
    # condition that has zero completed trials -- so the FULL trial list is
    # passed in below, not a pre-filtered one. Pre-filtering before calling
    # them would make an all-failed condition disappear from the output
    # instead of being reported as N/A (see
    # tests/test_evaluation.py::test_all_errored_condition_maps_to_none for
    # the documented contract this relies on).
    rates = outcome_rate_by_condition(trials)
    print("\nMutual-cooperation rate by condition:")
    for condition, rate in sorted(rates.items()):
        if rate is None:
            print(f"  {condition}: N/A (no completed trials)")
        else:
            print(f"  {condition}: {rate:.3f}")

    if total_failed:
        print("\nWARNING: Failed trials were excluded from the rate and estimand computations above.")

    print("\nEstimated communication effects:")
    for name, estimator in [
        ("tau_y (joint cooperation)", tau_y),
        ("tau_r (receiver cooperation)", tau_r),
    ]:
        try:
            estimate = estimator(trials)
            print(f"  {name}: {estimate:.3f}")
        except ValueError as exc:
            print(f"  {name}: N/A ({exc})")


if __name__ == "__main__":
    main()
