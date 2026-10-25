#!/usr/bin/env python3
"""Phase A figure v2: receiver B-choice rate and joint-B (B/B) rate with
Wilson 95% intervals, one panel per receiver (READ-ONLY).

Run from the project root:
    .venv/bin/python figures/report_2026-10-24/make_phaseA_fig_v2.py

Reuses load_phase_a() and assert_phase_a() from make_figures.py, so counts
come from the Phase A traces and are checked against the audited values
before anything is drawn. Writes fig_phaseA_BB_v2.{pdf,png} only.

Wilson score interval (z = 1.959964 for 95%), for k successes in n trials,
p = k/n:
    centre    = (p + z^2/(2n)) / (1 + z^2/n)
    halfwidth = z / (1 + z^2/n) * sqrt(p(1-p)/n + z^2/(4n^2))
    interval  = [centre - halfwidth, centre + halfwidth]
The 20 scenarios per cell are treated as independent Bernoulli draws under
one fixed game and prompt; the intervals are descriptive only.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import make_figures as mf  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

Z95 = 1.959964

# Okabe-Ito pair (colour-blind safe): blue = Sonnet, vermillion = GPT.
# Colour encodes receiver model; hatching distinguishes joint B/B outcomes.
RECV_B = "#0072B2"
JOINT_B = "#D55E00"

SHORT = [
    "C0\nNo message",
    "C1\nNeutral\nnotice",
    "C2\nA-directed",
    "C3\nB-directed",
]


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z / denom * math.sqrt(
        p * (1 - p) / n + z * z / (4 * n * n)
    )
    return max(0.0, centre - half), min(1.0, centre + half)


def main() -> int:
    data = mf.load_phase_a()
    mf.assert_phase_a(data)

    fig, ax = plt.subplots(figsize=(6.3, 3.1))
    x = np.arange(len(mf.CONDITIONS), dtype=float)
    w = 0.2

    # Colour = receiver model; solid = receiver chose B;
    # hatched = joint B/B.
    series = [
        ("sonnet", "receiver_b", RECV_B, None, -1.5 * w),
        ("gpt", "receiver_b", JOINT_B, None, -0.5 * w),
        ("sonnet", "bb", RECV_B, "////", 0.5 * w),
        ("gpt", "bb", JOINT_B, "////", 1.5 * w),
    ]

    for key, field, colour, hatch, off in series:
        blocks = data[key]
        ks = np.array([blk[field] for blk in blocks], dtype=float)
        ns = np.array([blk["valid"] for blk in blocks], dtype=float)

        # Wilson bounds are computed on proportions, then rescaled to counts.
        ci = (
            np.array([
                wilson(int(k), int(n))
                for k, n in zip(ks, ns)
            ])
            * ns[:, None]
        )
        yerr = np.vstack([ks - ci[:, 0], ci[:, 1] - ks])

        ax.bar(
            x + off,
            ks,
            width=w - 0.02,
            zorder=3,
            color="white" if hatch else colour,
            edgecolor=colour,
            hatch=hatch,
            linewidth=1.0,
        )

        ax.errorbar(
            x + off,
            ks,
            yerr=yerr,
            fmt="none",
            ecolor=mf.DARK,
            elinewidth=0.8,
            capsize=2,
            capthick=0.8,
            zorder=4,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(SHORT, fontsize=9)
    ax.set_xlim(-0.55, len(x) - 0.45)
    ax.set_ylim(0, 21)
    ax.set_yticks([0, 5, 10, 15, 20])
    ax.set_ylabel("Trials (out of 20)", fontsize=9)
    ax.grid(axis="y", color=mf.GRID, linewidth=0.8)

    for side in ("left", "bottom"):
        ax.spines[side].set_visible(True)
        ax.spines[side].set_color(mf.DARK)
        ax.spines[side].set_linewidth(0.8)

    ax.tick_params(axis="both", length=3, color=mf.DARK)

    from matplotlib.patches import Patch

    handles = [
        # Colour encodes receiver model.
        Patch(
            facecolor=RECV_B,
            edgecolor=RECV_B,
            label="Sonnet 4.6",
        ),
        Patch(
            facecolor=JOINT_B,
            edgecolor=JOINT_B,
            label="GPT-5.4-mini",
        ),

        # Fill pattern encodes outcome.
        Patch(
            facecolor="#9AA5B1",
            edgecolor="#4A5568",
            label="Receiver chose B",
        ),
        Patch(
            facecolor="white",
            edgecolor="#4A5568",
            hatch="////",
            label="Joint B/B",
        ),
    ]

    ax.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.0),
        ncol=2,
        frameon=False,
        fontsize=9,
        columnspacing=1.5,
        handlelength=2.2,
        handletextpad=0.7,
        labelspacing=0.6,
    )

    for suffix in ("pdf", "png"):
        path = mf.OUT / f"fig_phaseA_BB_v2.{suffix}"
        fig.savefig(path, dpi=300, bbox_inches="tight")
        print(f"wrote {path.relative_to(mf.ROOT)}")

    plt.close(fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


