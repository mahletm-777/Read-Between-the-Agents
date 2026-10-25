"""Scenario-cluster paired percentile bootstrap for the monitor contrasts (Phase B).

Canonical: ``src/apart/analysis_e.py`` -- ``scenario_cluster_bootstrap_interval``.

Draw discipline (approved 2026-10-24):

* the resampling unit is the **scenario**, not the trace;
* scenarios are drawn **with replacement**, ``len(scenarios)`` per replicate, and
  **multiplicity is preserved** -- a scenario drawn *k* times contributes all of its
  paired observations *k* times;
* both monitor arms are scored on the **same** sampled observations, and the
  replicate statistic is ``AUROC(first) - AUROC(second)`` computed **inside** the
  replicate (fixed cell estimates are never reused);
* ``n_boot = 2000``, ``seed = 0``, percentile interval at ``[0.025, 0.975]`` using
  the repository's linear-interpolation percentile;
* a replicate in which either arm sees only one E class is **invalid** and is
  counted, never imputed.

Interpretation boundary: the interval quantifies resampling variability of the
contrast across the scenarios of *this* corpus under one fixed game and prompt set.
The scenarios are not independent games, and nothing here supports generalisation.
"""
from __future__ import annotations

import random

from common import N_BOOT, PERCENTILES, auroc, percentile


def scenario_of(monitor: str, receiver: str) -> dict[str, str]:
    """``trace_id -> scenario_id`` as recorded in the monitor score rows."""
    from common import CONDITIONS, scores
    out: dict[str, str] = {}
    for cond in CONDITIONS:
        for row in scores(monitor, receiver, cond):
            if row.get("trace_id") and row.get("scenario_id"):
                out[row["trace_id"]] = row["scenario_id"]
    return out


def paired_bootstrap_interval(sa: dict[str, float], sb: dict[str, float],
                              truth: dict[str, str], scen: dict[str, str],
                              n_boot: int = N_BOOT, seed: int = 0,
                              percentiles=PERCENTILES) -> dict:
    """Percentile interval for ``AUROC(sa) - AUROC(sb)`` resampling scenarios."""
    ids = sorted(set(sa) & set(sb))
    grouped: dict[str, list[str]] = {}
    for trace_id in ids:
        grouped.setdefault(scen.get(trace_id, trace_id), []).append(trace_id)
    scenario_ids = sorted(grouped)
    if not scenario_ids:
        return {"status": "not_estimable", "reason": "no paired observations"}

    rng = random.Random(seed)
    deltas: list[float] = []
    n_invalid = 0
    for _ in range(n_boot):
        drawn: list[str] = []
        for _ in range(len(scenario_ids)):
            drawn.extend(grouped[scenario_ids[rng.randrange(len(scenario_ids))]])
        pos = [t for t in drawn if truth.get(t) == "positive"]
        neg = [t for t in drawn if truth.get(t) == "negative"]
        if not pos or not neg:
            n_invalid += 1
            continue
        deltas.append(auroc([sa[t] for t in pos], [sa[t] for t in neg])
                      - auroc([sb[t] for t in pos], [sb[t] for t in neg]))
    if not deltas:
        return {"status": "not_estimable", "reason": "all replicates invalid",
                "n_boot_invalid": n_invalid}
    ordered = sorted(deltas)
    return {
        "status": "estimated",
        "method": "scenario_cluster_bootstrap_percentile_paired",
        "resampling_unit": "scenario",
        "n_scenarios": len(scenario_ids),
        "n_pairs": len(ids),
        "n_boot_requested": n_boot,
        "n_boot_valid": len(deltas),
        "n_boot_invalid_single_class": n_invalid,
        "seed": seed,
        "percentiles": list(percentiles),
        "ci95": [percentile(ordered, percentiles[0]), percentile(ordered, percentiles[1])],
    }


def e1_bootstrap(per_scenario: dict[str, dict[str, int]],
                 n_boot: int = N_BOOT, seed: int = 0) -> dict:
    """Exploratory follow-up: shared-draw scenario bootstrap for T, J and I.

    One RNG produces one set of resamples per receiver model; the **same** multiset
    yields T, J and I, so the three intervals are mutually consistent.
    """
    ids = sorted(per_scenario)
    if not ids:
        return {"status": "not_estimable", "reason": "empty eligible population"}
    rng = random.Random(seed)
    size = len(ids)
    values = {k: [] for k in ("T", "J", "I")}
    for _ in range(n_boot):
        totals = {k: 0.0 for k in ("T", "J", "I")}
        for _ in range(size):
            row = per_scenario[ids[rng.randrange(size)]]
            a, b = row["c_a_absent"], row["c_b_absent"]
            c, d = row["c_a_present"], row["c_b_present"]
            totals["T"] += 0.5 * ((d - c) + (b - a))
            totals["J"] += 0.5 * ((c - a) + (d - b))
            totals["I"] += (d - c) - (b - a)
        for k in values:
            values[k].append(totals[k] / size)
    out = {}
    for k in ("T", "J", "I"):
        ordered = sorted(values[k])
        out[k] = {"ci95": [percentile(ordered, PERCENTILES[0]),
                           percentile(ordered, PERCENTILES[1])]}
    return {"status": "estimated", "n_scenarios": size, "n_boot": n_boot, "seed": seed,
            "intervals": out}
