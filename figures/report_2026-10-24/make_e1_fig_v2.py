#!/usr/bin/env python3
"""E1 figure v2: receiver B-choice counts by message condition, one panel per
receiver model, with Wilson 95% intervals (READ-ONLY).

Run from the project root:
    .venv/bin/python figures/report_2026-10-24/make_e1_fig_v2.py

Reads results/runs_e1/<run>/trials.jsonl, asserts the reported counts (receiver B,
BB, OB; 20 valid trials per cell) before plotting, and writes fig_e1_v2.{pdf,png}.

Wilson score interval (z = 1.959964), k successes in n trials, p = k/n:
    centre    = (p + z^2/(2n)) / (1 + z^2/n)
    halfwidth = z / (1 + z^2/n) * sqrt(p(1-p)/n + z^2/(4n^2))
Bounds are computed on proportions and rescaled to counts. The 20 scenarios
are treated as independent Bernoulli draws under one fixed game; descriptive only.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import make_figures as mf  # noqa: E402  (shared style + fail())
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

RUNS = mf.ROOT / "results" / "runs_e1"
MODELS = [
    ("sonnet", "e1-msg2x2-sonnet46-v1-20261025T030713Z", "Sonnet 4.6 receiver", "#0072B2"),
    ("gpt", "e1-msg2x2-gpt54mini-v1-20261025T031210Z", "GPT-5.4-mini receiver", "#D55E00"),
]
CELLS = [
    ("c_a_absent", "A-target\npayoff\nabsent"),
    ("c_a_present", "A-target\npayoff\npresent"),
    ("c_b_absent", "B-target\npayoff\nabsent"),
    ("c_b_present", "B-target\npayoff\npresent"),
]
N = 20
EXPECTED = {  # assertions only, not plotting inputs
    "sonnet": {"recv_b": [20, 20, 20, 14], "bb": [10, 10, 10, 8], "ob": [20, 20, 20, 16]},
    "gpt": {"recv_b": [16, 17, 20, 20], "bb": [9, 9, 10, 10], "ob": [17, 18, 20, 20]},
}
Z95 = 1.959964


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z / denom * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, centre - half), min(1.0, centre + half)


def load(run: str) -> dict[str, list[int]]:
    path = RUNS / run / "trials.jsonl"
    if not path.exists():
        mf.fail(f"missing {path}")
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    out = {"recv_b": [], "bb": [], "ob": [], "n": []}
    for cell, _ in CELLS:
        cell_rows = [r for r in rows if r["cell_id"] == cell]
        valid = [r for r in cell_rows
                 if r.get("parsed_action") in ("A", "B") and r.get("sender_action") in ("A", "B")]
        if len(cell_rows) != N or len(valid) != N:
            mf.fail(f"{run}/{cell}: rows={len(cell_rows)} valid={len(valid)}, expected {N}")
        if len({r["scenario_id"] for r in valid}) != N:
            mf.fail(f"{run}/{cell}: duplicate scenario ids")
        r_b = [r["parsed_action"] == "B" for r in valid]
        s_b = [r["sender_action"] == "B" for r in valid]
        if sum(s_b) != 10:
            mf.fail(f"{run}/{cell}: sender chose B {sum(s_b)} times, expected 10")
        out["recv_b"].append(sum(r_b))
        out["bb"].append(sum(a and b for a, b in zip(r_b, s_b)))
        out["ob"].append(sum(a or b for a, b in zip(r_b, s_b)))
        out["n"].append(len(valid))
    return out


def main() -> int:
    data = {}
    for key, run, *_ in MODELS:
        data[key] = load(run)
        for field in ("recv_b", "bb", "ob"):
            if data[key][field] != EXPECTED[key][field]:
                mf.fail(f"{key} {field}: {data[key][field]} != {EXPECTED[key][field]}")
    print("PASS E1: receiver-B, BB and OB counts verified; 20 valid trials per cell.")

    fig, ax = plt.subplots(figsize=(6.3, 2.9))
    x = np.arange(len(CELLS), dtype=float)
    w = 0.2
    # Same encoding as fig_phaseA_BB_v2: colour = receiver model;
    # solid = receiver chose B, hatched = joint B/B.
    colours = {key: colour for key, _, _, colour in MODELS}
    series = [
        ("sonnet", "recv_b", None, -1.5 * w),
        ("gpt", "recv_b", None, -0.5 * w),
        ("sonnet", "bb", "////", 0.5 * w),
        ("gpt", "bb", "////", 1.5 * w),
    ]
    for key, field, hatch, off in series:
        colour = colours[key]
        ks = np.array(data[key][field], dtype=float)
        ns = np.array(data[key]["n"], dtype=float)
        ci = np.array([wilson(int(k), int(n)) for k, n in zip(ks, ns)]) * ns[:, None]
        ax.bar(x + off, ks, width=w - 0.02, zorder=3,
               color="white" if hatch else colour, edgecolor=colour,
               hatch=hatch, linewidth=1.0)
        ax.errorbar(x + off, ks, yerr=np.vstack([ks - ci[:, 0], ci[:, 1] - ks]), fmt="none",
                    ecolor=mf.DARK, elinewidth=0.8, capsize=2, capthick=0.8, zorder=4)
    ax.set_xticks(x)
    ax.set_xticklabels([c[1].replace("\npayoff\n", "\npayoff ") for c in CELLS], fontsize=7.5)
    ax.set_ylim(0, 21)
    ax.set_yticks([0, 5, 10, 15, 20])
    ax.set_ylabel("Trials (out of 20)")
    ax.grid(axis="y", color=mf.GRID, linewidth=0.8)
    for side in ("left", "bottom"):
        ax.spines[side].set_visible(True)
        ax.spines[side].set_color(mf.DARK)
        ax.spines[side].set_linewidth(0.8)
    ax.tick_params(axis="both", length=3, color=mf.DARK)
    from matplotlib.patches import Patch
    handles = [
        Patch(color=colours["sonnet"], label="Sonnet 4.6"),
        Patch(color=colours["gpt"], label="GPT-5.4-mini"),
        Patch(facecolor="#9AA5B1", edgecolor="#9AA5B1", label="Receiver chose B"),
        Patch(facecolor="white", edgecolor="#4A5568", hatch="////", label="Joint B/B"),
    ]
    ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 1.0),
              ncol=4, frameon=False, fontsize=7.5, columnspacing=1.5)

    for suffix in ("pdf", "png"):
        path = mf.OUT / f"fig_e1_v2.{suffix}"
        fig.savefig(path, dpi=300, bbox_inches="tight")
        print(f"wrote {path.relative_to(mf.ROOT)}")
    plt.close(fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
