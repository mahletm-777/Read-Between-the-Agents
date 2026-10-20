"""Exhaustive state-space validation: a separate experiment from the
seed-based positive control in runner.py.

Instead of a handful of seeds, this runs EVERY possible
(resource_level_a, resource_level_b) pair -- 11 x 11 = 121 pairs, since
each resource_level is drawn from {0, ..., 10} -- under both communication
conditions, using identical observations for the same pair across
conditions. It characterizes the deterministic mock agent's behavior
exactly over its full observation space, rather than over an arbitrary
small sample. It says nothing about real LLM agents: see
docs/experiment_design.md and docs/decision_log.md for that distinction.
"""
import dataclasses
from typing import Sequence

from apart.conditions import ALL_CONDITIONS
from apart.environment import TwoAgentEnvironment
from apart.evaluation import outcome_rate_by_condition, tau_y, tau_r
from apart.models import TrialResult
from apart.runner import run_trial_from_observations

RESOURCE_LEVELS = range(11)  # {0, ..., 10}, matching TwoAgentEnvironment's randint(0, 10) range.


def enumerate_pairs() -> list[tuple[int, int]]:
    """Returns all 121 ordered (resource_level_a, resource_level_b) pairs."""
    return [(a, b) for a in RESOURCE_LEVELS for b in RESOURCE_LEVELS]


def run_exhaustive_trials() -> list[TrialResult]:
    """Runs every (resource_level_a, resource_level_b) pair under both
    communication conditions, with identical observations for a given pair
    across both conditions (mirroring how the seed-based runner keeps one
    seed's observations fixed across conditions)."""
    # `env` is only used for its step() outcome-labeling logic (which ignores
    # self); it never redraws observations here, so any seed value is fine.
    env = TwoAgentEnvironment(seed=0)
    trials: list[TrialResult] = []
    for a, b in enumerate_pairs():
        observations = {"agent_a": {"resource_level": a}, "agent_b": {"resource_level": b}}
        pair_index = a * 11 + b  # Reuses TrialMetadata.seed as a deterministic pair index -- no RNG involved.
        for condition_name in ALL_CONDITIONS:
            trial_id = f"{condition_name}_pair_a{a}_b{b}"
            trials.append(
                run_trial_from_observations(
                    condition_name=condition_name,
                    observations=observations,
                    env=env,
                    trial_id=trial_id,
                    seed=pair_index,
                )
            )
    return trials


def _group_by_pair(trials: Sequence[TrialResult]) -> dict:
    """Groups trials by pair index (TrialMetadata.seed), keyed by condition
    within each pair. Raises if any pair is missing a condition's trial."""
    by_pair: dict = {}
    for t in trials:
        by_pair.setdefault(t.metadata.seed, {})[t.metadata.condition] = t
    for pair_index, by_condition in by_pair.items():
        if set(by_condition.keys()) != set(ALL_CONDITIONS):
            raise ValueError(f"Pair {pair_index} is missing one condition's trial.")
    return by_pair


def action_change_count(trials: Sequence[TrialResult], *, receiver_id: str = "agent_b") -> int:
    """Counts pairs (matched by TrialMetadata.seed, the pair index) where the
    receiver's action differs between the two conditions."""
    by_pair = _group_by_pair(trials)
    count = 0
    for by_condition in by_pair.values():
        actions = {c: t.actions[receiver_id].tool_name for c, t in by_condition.items()}
        if len(set(actions.values())) > 1:
            count += 1
    return count


def outcome_change_count(trials: Sequence[TrialResult]) -> int:
    """Counts pairs where the trial outcome label differs between the two
    conditions."""
    by_pair = _group_by_pair(trials)
    count = 0
    for by_condition in by_pair.values():
        outcomes = {c: t.outcome for c, t in by_condition.items()}
        if len(set(outcomes.values())) > 1:
            count += 1
    return count


def summarize_exhaustive(trials: Sequence[TrialResult]) -> dict:
    """Aggregates exhaustive-run metrics. Reuses evaluation.py's existing
    estimators unmodified for condition rates and tau_y/tau_r; adds the
    paired same-input comparisons evaluation.py doesn't do (it only
    computes per-condition marginal rates, not same-pair cross-condition
    comparisons)."""
    records = [dataclasses.asdict(t) for t in trials]
    return {
        "total_trials": len(trials),
        "total_pairs": len(trials) // len(ALL_CONDITIONS),
        "mutual_cooperation_rate_by_condition": outcome_rate_by_condition(records),
        "tau_y": tau_y(records),
        "tau_r": tau_r(records),
        "action_change_count": action_change_count(trials),
        "outcome_change_count": outcome_change_count(trials),
    }
