"""Run every supplementary analysis offline and check the reported numbers.

Usage:  cd supplementary/analysis && python run_all.py

Reads only ``../data/``; writes only ``../outputs/``. **No network access and no API
key is required.** Exits non-zero if any reported number fails to reproduce.

The ``EXPECTED`` block below holds the numbers as stated in the report and in the
stored artefacts. Nothing here edits data to make a check pass; a mismatch is
reported, not silenced.
"""
from __future__ import annotations

import json
import sys

import tables_and_figures as tf
from common import OUT

# --- numbers as reported / stored (do not edit to force a match) --------------
EXPECTED = {
    # receiver -> condition -> (receiver B, BB, OB) out of 20
    "phase_a": {
        "sonnet": {"c0_no_message": (20, 10, 20), "c1_neutral": (20, 10, 20),
                   "c2_a_coordination": (19, 10, 19), "c3_b_coordination": (8, 3, 15)},
        "gpt54mini": {"c0_no_message": (20, 10, 20), "c1_neutral": (16, 9, 17),
                      "c2_a_coordination": (7, 2, 15), "c3_b_coordination": (20, 10, 20)},
    },
    # monitor/receiver -> {view: auroc}
    "phase_b_auroc": {
        "monitor=sonnet__receiver=sonnet": {"M1": 1.0, "M2": 0.5, "M3": 1.0, "M4": 1.0},
        "monitor=sonnet__receiver=gpt54mini": {"M1": 1.0, "M2": 0.5, "M3": 1.0, "M4": 1.0},
        "monitor=gpt__receiver=sonnet": {"M1": 1.0, "M2": 0.5, "M3": 1.0, "M4": 1.0},
        "monitor=gpt__receiver=gpt54mini": {"M1": 1.0, "M2": 0.5, "M3": 1.0, "M4": 1.0},
    },
    # exploratory follow-up: alias -> contrast -> (point, lo, hi)
    "e1": {
        "sonnet46": {"T": (-0.150, -0.250, -0.050), "J": (-0.150, -0.250, -0.050),
                     "I": (-0.300, -0.500, -0.100)},
        "gpt54mini": {"T": (0.175, 0.075, 0.275), "J": (0.025, -0.100, 0.150),
                      "I": (-0.050, -0.300, 0.200)},
    },
    "phase_a_valid_trials": 160,
    "phase_b_resolved_per_receiver": 40,
    "e1_planned_trials": 80,
}
TOL = 0.02


def check(bundle: dict) -> list[tuple[str, str, str]]:
    """Return (item, verdict, detail) rows."""
    rows: list[tuple[str, str, str]] = []

    for receiver, conds in EXPECTED["phase_a"].items():
        for cond, (rb, bb, ob) in conds.items():
            got = bundle["phase_a"]["conditions"][receiver][cond]
            ok = (got["receiver_b_count"], got["bb_count"], got["ob_count"]) == (rb, bb, ob)
            rows.append((f"phase_a {receiver}/{cond}",
                         "OK" if ok else "MISMATCH",
                         f"got {(got['receiver_b_count'], got['bb_count'], got['ob_count'])} "
                         f"want {(rb, bb, ob)}"))

    for key, views in EXPECTED["phase_b_auroc"].items():
        for view, want in views.items():
            got = bundle["phase_b"]["aurocs"][key][view]["auroc"]
            ok = abs(got - want) <= TOL
            rows.append((f"phase_b auroc {key}/{view}", "OK" if ok else "MISMATCH",
                         f"got {got:.3f} want {want:.3f}"))

    for alias, contr in EXPECTED["e1"].items():
        arm = bundle["e1"][alias]
        for name, (pt, lo, hi) in contr.items():
            gp = arm["points"][name]
            gl, gh = arm["intervals"][name]["ci95"]
            ok = abs(gp - pt) <= TOL and abs(gl - lo) <= TOL and abs(gh - hi) <= TOL
            rows.append((f"e1 {alias}/{name}", "OK" if ok else "MISMATCH",
                         f"got ({gp:.3f}, [{gl:.3f}, {gh:.3f}]) "
                         f"want ({pt:.3f}, [{lo:.3f}, {hi:.3f}])"))

    total = sum(v["n_trials"] for v in bundle["phase_a"]["validity"].values())
    rows.append(("phase_a valid trials", "OK" if total == EXPECTED["phase_a_valid_trials"]
                 else "MISMATCH", f"got {total} want {EXPECTED['phase_a_valid_trials']}"))
    for receiver, pop in bundle["phase_b"]["population"].items():
        n = pop["n_resolved"]
        rows.append((f"phase_b resolved {receiver}",
                     "OK" if n == EXPECTED["phase_b_resolved_per_receiver"] else "MISMATCH",
                     f"got {n} want {EXPECTED['phase_b_resolved_per_receiver']}"))
    for alias, arm in bundle["e1"].items():
        n = arm["n_trials"]
        rows.append((f"e1 trials {alias}",
                     "OK" if n == EXPECTED["e1_planned_trials"] else "MISMATCH",
                     f"got {n} want {EXPECTED['e1_planned_trials']}"))
    return rows


def main() -> int:
    bundle = tf.build()
    rows = check(bundle)
    bad = [r for r in rows if r[1] != "OK"]
    (OUT / "reproduction_report.md").write_text(
        "# Offline reproduction report\n\n"
        f"Checks: **{len(rows)}** | OK: **{len(rows) - len(bad)}** | "
        f"MISMATCH: **{len(bad)}**\n\n| Item | Verdict | Detail |\n|---|---|---|\n"
        + "\n".join(f"| {i} | {v} | {d} |" for i, v, d in rows) + "\n",
        encoding="utf-8")
    print(json.dumps({"checks": len(rows), "ok": len(rows) - len(bad),
                      "mismatch": len(bad)}, indent=2))
    for item, verdict, detail in bad:
        print(f"MISMATCH  {item}: {detail}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
