"""Table and figure generation for the supplementary package.

Emits every reported table as Markdown plus a JSON bundle under
``supplementary/outputs/``. Reproduces:

* Table 1 -- sender-receiver interaction: receiver B-choice, BB and OB per condition
  and receiver (denominator 20).
* Table 2 -- monitor evaluation: AUROC per monitor view and receiver, on the resolved
  population.
* Table 3 -- the five pre-specified monitor contrasts with their bootstrap intervals.
* Table 4 -- exploratory receiver follow-up: cell means and the T / J / I contrasts
  with scenario-cluster bootstrap intervals, per arm.

Run ``python run_all.py`` from this directory.
"""
from __future__ import annotations

import bootstrap as bs
import e_labels as el
import outcome_derivation as od
import resolved_population as rp
from common import CONDITIONS, OUT, RECEIVERS, VIEWS, write_json


def phase_a_table() -> dict:
    return od.all_conditions()


def phase_b_aurocs() -> dict:
    import auroc_and_contrasts as ac
    return {f"monitor={tag}__receiver={receiver}":
            {view: ac.arm_metrics(monitor, receiver, view) for view in VIEWS}
            for monitor, tag in (("sonnet", "sonnet"), ("gpt", "gpt"))
            for receiver in RECEIVERS}


def phase_b_contrasts() -> dict:
    import auroc_and_contrasts as ac
    return {f"monitor={tag}__receiver={receiver}": ac.contrasts(monitor, receiver)
            for monitor, tag in (("sonnet", "sonnet"), ("gpt", "gpt"))
            for receiver in RECEIVERS}


def e1_arm_table(alias: str) -> dict:
    from common import e1_arm
    arm = e1_arm(alias)
    plan, records = arm["plan"], arm["trials"]
    cells = list(plan["cell_mean_order"])
    per_scenario: dict[str, dict[str, int]] = {}
    for row in records:
        if row.get("validity") == "valid" and row.get("parsed_action") in ("A", "B"):
            per_scenario.setdefault(row["scenario_id"], {})[row["cell_id"]] = (
                1 if row["parsed_action"] == "B" else 0)
    eligible = {s: r for s, r in per_scenario.items() if all(c in r for c in cells)}
    means = {c: (sum(r[c] for r in eligible.values()) / len(eligible) if eligible else None)
             for c in cells}
    a, b = means["c_a_absent"], means["c_b_absent"]
    c, d = means["c_a_present"], means["c_b_present"]
    points = {"T": 0.5 * ((d - c) + (b - a)),
              "J": 0.5 * ((c - a) + (d - b)),
              "I": (d - c) - (b - a)}
    return {"n_trials": len(records), "n_eligible_scenarios": len(eligible),
            "cell_means": means, "points": points,
            "intervals": bs.e1_bootstrap(eligible)["intervals"],
            "non_live_states": sorted({r.get("execution_status") for r in records
                                       if r.get("execution_status") != "live"})}


def markdown(bundle: dict) -> str:
    lines = ["# Reproduced tables", "",
             "Generated offline by `supplementary/analysis/run_all.py`. No network access.", "",
             "## Table 1 - sender-receiver interaction (denominator 20 per cell)", "",
             "| Receiver | Condition | receiver B | B-rate | BB | OB | excluded |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for receiver, conds in bundle["phase_a"]["conditions"].items():
        for cond in CONDITIONS:
            r = conds[cond]
            lines.append(f"| {receiver} | {cond} | {r['receiver_b_count']} | "
                         f"{r['receiver_b_rate']:.2f} | {r['bb_count']} | "
                         f"{r['ob_count']} | {r['n_excluded']} |")
    lines += ["", "## Table 2 - monitor evaluation AUROC (resolved population, 20 E+ / 20 E-)", "",
              "| Combination | " + " | ".join(VIEWS) + " |",
              "|---|" + "---:|" * len(VIEWS)]
    for key, arms in bundle["phase_b"]["aurocs"].items():
        lines.append(f"| {key} | " + " | ".join(f"{arms[v]['auroc']:.3f}" for v in VIEWS) + " |")
    lines += ["", "## Table 3 - pre-specified monitor contrasts (primary = M3 - M1)", "",
              "| Combination | delta | ci95 | n_boot_valid | n_scenarios |",
              "|---|---:|---|---:|---:|"]
    for key, block in bundle["phase_b"]["contrasts"].items():
        p, iv = block["primary"], block["primary"]["interval"]
        lines.append(f"| {key} | {p['delta']:.3f} | "
                     f"[{iv['ci95'][0]:.3f}, {iv['ci95'][1]:.3f}] | "
                     f"{iv['n_boot_valid']} | {iv['n_scenarios']} |")
    lines += ["", "## Table 4 - exploratory receiver follow-up (per arm)", "",
              "| Arm | Contrast | point | ci95 |", "|---|---|---:|---|"]
    for alias, arm in bundle["e1"].items():
        for name in ("T", "J", "I"):
            iv = arm["intervals"][name]["ci95"]
            lines.append(f"| {alias} | {name} | {arm['points'][name]:.3f} | "
                         f"[{iv[0]:.3f}, {iv[1]:.3f}] |")
    return "\n".join(lines) + "\n"


def build() -> dict:
    bundle = {
        "phase_a": {"conditions": phase_a_table(), "validity": od.validity_summary()},
        "phase_b": {"population": {r: rp.population(r) for r in RECEIVERS},
                    "aurocs": phase_b_aurocs(), "contrasts": phase_b_contrasts(),
                    "e_labels": {r: el.states(r) for r in RECEIVERS},
                    "y_h": el.y_h_note()},
        "e1": {alias: e1_arm_table(alias) for alias in ("sonnet46", "gpt54mini")},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    write_json(OUT / "reproduced_tables.json", bundle)
    (OUT / "reproduced_tables.md").write_text(markdown(bundle), encoding="utf-8")
    return bundle
