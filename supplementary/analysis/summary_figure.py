"""Rebuild the summary figure from supplementary/data. Optional (needs matplotlib)."""
from __future__ import annotations

from common import CONDITIONS, OUT, RECEIVERS


def main(bundle: dict) -> str | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for ax, receiver in zip(axes, RECEIVERS):
        conds = bundle["phase_a"]["conditions"][receiver]
        xs = list(range(len(CONDITIONS)))
        ax.bar([x - 0.18 for x in xs], [conds[c]["receiver_b_count"] for c in CONDITIONS],
               width=0.36, label="receiver B /20")
        ax.bar([x + 0.18 for x in xs], [conds[c]["ob_count"] for c in CONDITIONS],
               width=0.36, label="OB /20")
        ax.plot(xs, [conds[c]["bb_count"] for c in CONDITIONS], "ko--",
                label="BB /20 (cap 10)")
        ax.set_xticks(xs)
        ax.set_xticklabels([c.split("_")[0].upper() for c in CONDITIONS])
        ax.set_ylim(0, 20)
        ax.set_title(f"receiver = {receiver}")
        ax.legend(fontsize=7)
    fig.suptitle("Reproduced from supplementary/data (descriptive; no intervals)")
    fig.tight_layout()
    path = OUT / "figure_summary_rebuilt.png"
    fig.savefig(path, dpi=120)
    return str(path)


if __name__ == "__main__":
    import tables_and_figures as tf
    print(main(tf.build()))
