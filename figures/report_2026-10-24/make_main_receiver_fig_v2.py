#!/usr/bin/env python3
"""Main-text receiver figure: (a) Phase A and (b) exploratory E1, side by side
(READ-ONLY).

Run from the project root:
    .venv/bin/python figures/report_2026-10-24/make_main_receiver_fig.py

Reuses the loaders and count assertions of make_figures.py (Phase A) and
make_e1_fig_v2.py (E1); every count is checked before anything is drawn.
Encoding as in fig_phaseA_BB_v2 / fig_e1_v2: colour = receiver model,
solid = receiver chose B, hatched = joint B/B, Wilson 95% intervals (formula in
make_phaseA_fig_v2.py) rescaled to counts. Writes fig_main_receiver_v2.{pdf,png} (panel titles reduced to (a)/(b)).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import make_e1_fig_v2 as e1  # noqa: E402
import make_figures as mf  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from make_phaseA_fig_v2 import wilson  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

COLOURS = {"sonnet": "#0072B2", "gpt": "#D55E00"}
PHASE_A_TICKS = ["C0\nNo\nmessage", "C1\nNeutral\nnotice", "C2\nA-directed", "C3\nB-directed"]
E1_TICKS = ["A-target\npayoff\nabsent", "A-target\npayoff\npresent",
            "B-target\npayoff\nabsent", "B-target\npayoff\npresent"]


def load() -> dict:
    phase_a = mf.load_phase_a()
    mf.assert_phase_a(phase_a)
    out = {"a": {}, "b": {}}
    for key in ("sonnet", "gpt"):
        blocks = phase_a[key]
        out["a"][key] = {
            "recv_b": [blk["receiver_b"] for blk in blocks],
            "bb": [blk["bb"] for blk in blocks],
            "n": [blk["valid"] for blk in blocks],
        }
    for key, run, *_ in e1.MODELS:
        d = e1.load(run)
        for field in ("recv_b", "bb", "ob"):
            if d[field] != e1.EXPECTED[key][field]:
                mf.fail(f"E1 {key} {field}: {d[field]} != {e1.EXPECTED[key][field]}")
        out["b"][key] = d
    print("PASS E1: receiver-B, BB and OB counts verified; 20 valid trials per cell.")
    return out


def panel(ax, data: dict, ticks: list[str], title: str) -> None:
    x = np.arange(len(ticks), dtype=float)
    w = 0.2
    series = [
        ("sonnet", "recv_b", None, -1.5 * w),
        ("gpt", "recv_b", None, -0.5 * w),
        ("sonnet", "bb", "////", 0.5 * w),
        ("gpt", "bb", "////", 1.5 * w),
    ]
    for key, field, hatch, off in series:
        colour = COLOURS[key]
        ks = np.array(data[key][field], dtype=float)
        ns = np.array(data[key]["n"], dtype=float)
        ci = np.array([wilson(int(k), int(n)) for k, n in zip(ks, ns)]) * ns[:, None]
        ax.bar(x + off, ks, width=w - 0.02, zorder=3,
               color="white" if hatch else colour, edgecolor=colour,
               hatch=hatch, linewidth=0.9)
        ax.errorbar(x + off, ks, yerr=np.vstack([ks - ci[:, 0], ci[:, 1] - ks]), fmt="none",
                    ecolor=mf.DARK, elinewidth=0.7, capsize=1.6, capthick=0.7, zorder=4)
    ax.set_title(title, loc="left", fontsize=8.5)
    ax.set_xticks(x)
    ax.set_xticklabels(ticks, fontsize=6.5)
    ax.set_xlim(-0.55, len(x) - 0.45)
    ax.set_ylim(0, 21)
    ax.set_yticks([0, 5, 10, 15, 20])
    ax.grid(axis="y", color=mf.GRID, linewidth=0.7)
    for side in ("left", "bottom"):
        ax.spines[side].set_visible(True)
        ax.spines[side].set_color(mf.DARK)
        ax.spines[side].set_linewidth(0.8)
    ax.tick_params(axis="both", length=3, color=mf.DARK, labelsize=7)
    ax.tick_params(axis="x", labelsize=6.5)


def main() -> int:
    data = load()
    fig, axes = plt.subplots(1, 2, figsize=(6.5, 2.6), sharey=True)
    panel(axes[0], data["a"], PHASE_A_TICKS, "(a)")
    panel(axes[1], data["b"], E1_TICKS, "(b)")
    axes[0].set_ylabel("Trials (out of 20)", fontsize=8)

    handles = [
        Patch(color=COLOURS["sonnet"], label="Sonnet 4.6"),
        Patch(color=COLOURS["gpt"], label="GPT-5.4-mini"),
        Patch(facecolor="#9AA5B1", edgecolor="#9AA5B1", label="Receiver chose B"),
        Patch(facecolor="white", edgecolor="#4A5568", hatch="////", label="Joint B/B"),
    ]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.98),
               ncol=4, frameon=False, fontsize=7.5, columnspacing=1.5)
    fig.subplots_adjust(wspace=0.06)

    for suffix in ("pdf", "png"):
        path = mf.OUT / f"fig_main_receiver_v2.{suffix}"
        fig.savefig(path, dpi=300, bbox_inches="tight")
        print(f"wrote {path.relative_to(mf.ROOT)}")
    plt.close(fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
