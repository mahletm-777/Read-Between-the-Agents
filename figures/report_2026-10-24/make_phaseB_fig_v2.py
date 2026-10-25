#!/usr/bin/env python3
"""Phase B figure v2: monitor scores by evidence view and E label,
with AUROC printed per view.

Run from the project root:
    .venv/bin/python figures/report_2026-10-24/make_phaseB_fig_v2.py

Reuses load_phase_b(), stored_aurocs() and assert_phase_b() from
make_figures.py. Writes fig_phaseB_v2.{pdf,png}.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import make_figures as mf  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

# E-label colors; marker shape identifies the receiver/monitor pairing.
E_NEG = "#7B8794"
E_POS = "#CC79A7"

COMBOS = [
    ("sonnet", "sonnet", "o", "Sonnet receiver / Sonnet monitor"),
    ("sonnet", "gpt", "s", "Sonnet receiver / GPT monitor"),
    ("gpt", "sonnet", "^", "GPT receiver / Sonnet monitor"),
    ("gpt", "gpt", "D", "GPT receiver / GPT monitor"),
]


def main() -> int:
    data = mf.load_phase_b()
    stored = mf.stored_aurocs()
    mf.assert_phase_b(data, stored)

    fig, ax = plt.subplots(figsize=(6.3, 2.9))
    rng = np.random.default_rng(0)

    x = np.arange(len(mf.VIEWS), dtype=float)
    class_offsets = {"neg": -0.19, "pos": 0.19}
    pairing_offsets = np.array([-0.075, -0.025, 0.025, 0.075])

    # Plot individual monitor scores. Small jitter reveals overlapping points.
    for i, view in enumerate(mf.VIEWS):
        for cls, colour in (("neg", E_NEG), ("pos", E_POS)):
            for (rk, mk, marker, _), offset in zip(COMBOS, pairing_offsets):
                vals = data[rk]["monitors"][mk]["views"][view][cls]
                n = len(vals)

                xs = (
                    i
                    + class_offsets[cls]
                    + offset
                    + rng.uniform(-0.012, 0.012, n)
                )
                ys = np.asarray(vals) + rng.uniform(-0.8, 0.8, n)

                ax.scatter(
                    xs,
                    ys,
                    s=12,
                    marker=marker,
                    facecolor=colour,
                    edgecolor="white",
                    linewidth=0.35,
                    alpha=0.8,
                    zorder=3,
                )

        aucs = {
            data[rk]["monitors"][mk]["views"][view]["auroc"]
            for rk, mk, *_ in COMBOS
        }
        auc_label = (
            f"AUROC {aucs.pop():.2f}"
            if len(aucs) == 1
            else "AUROC varies"
        )

        # Place the summary inside the plotting area rather than above it.
        ax.text(
            i,
            105,
            auc_label,
            ha="center",
            va="center",
            fontsize=8,
            fontweight="semibold",
            color=mf.DARK,
            zorder=4,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(mf.VIEWS)
    ax.set_xlim(-0.5, len(x) - 0.5)
    ax.set_ylim(-3, 110)
    ax.set_yticks([0, 25, 50, 75, 100])

    ax.set_xlabel("Evidence view")
    ax.set_ylabel("Monitor score (0–100)")

    ax.grid(axis="y", color=mf.GRID, linewidth=0.7)
    ax.set_axisbelow(True)

    for side in ("top", "right"):
        ax.spines[side].set_visible(False)

    for side in ("left", "bottom"):
        ax.spines[side].set_visible(True)
        ax.spines[side].set_color(mf.DARK)
        ax.spines[side].set_linewidth(0.8)

    ax.tick_params(axis="both", length=3, color=mf.DARK)

    # Legend: color identifies E label; marker identifies model pairing.
    label_handles = [
        Line2D([], [], linestyle="none", marker="o", markersize=5,
               markerfacecolor=E_NEG, markeredgecolor="none", label="E-negative"),
        Line2D([], [], linestyle="none", marker="o", markersize=5,
               markerfacecolor=E_POS, markeredgecolor="none", label="E-positive"),
    ]

    pairing_handles = [
        Line2D([], [], linestyle="none", marker=marker, markersize=5,
               markerfacecolor="white", markeredgecolor=mf.DARK, label=label)
        for _, _, marker, label in COMBOS
    ]

    handles = label_handles + pairing_handles

    ax.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.19),
        ncol=3,
        frameon=False,
        fontsize=7,
        columnspacing=1.3,
        handletextpad=0.5,
        handlelength=1.1,
        labelspacing=0.6,
    )

    # Reserve only a small bottom margin for the legend.
    fig.subplots_adjust(left=0.13, right=0.98, top=0.97, bottom=0.30)

    for suffix in ("pdf", "png"):
        path = mf.OUT / f"fig_phaseB_v2.{suffix}"
        fig.savefig(path, dpi=300, bbox_inches="tight")
        print(f"wrote {path.relative_to(mf.ROOT)}")

    plt.close(fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
