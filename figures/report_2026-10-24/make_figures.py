#!/usr/bin/env python3
"""Regenerate the APART report figures from frozen artifacts (READ-ONLY).

Run from the project root:
    .venv/bin/python figures/report_2026-10-24/make_figures.py

Writes PDF + 300 dpi PNG into this directory. Reads all data from the artifacts
below; hardcoded expected values are assertions, not plotting inputs.

Sources (read-only)
-------------------
Phase A traces:
    runs/phase-a-live-v2/traces_c*.jsonl
    runs/phase-a-live-gpt54mini-v1/traces_c*.jsonl

E labels:
    results/e_labels/{sonnet,gpt}_receiver/e_labels.jsonl

Monitor scores:
    Sonnet: <run>/scores_c*.jsonl
    GPT:    <run>/scores_gptmon2_c*.jsonl
            (NOT scores_gptmon_*: that run aborted)

Stored AUROC:
    results/e_matrix_gptmon/receiver=*__monitor=*.json

No provider calls, no spend, and no configuration changes.
Only figure files in this directory are written.
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Verified expectations
# ---------------------------------------------------------------------------
# These are assertions against independently audited artifact values.
# They are not used to construct plotted data.
EXPECTED_RECEIVER_B = {
    "sonnet": [20, 20, 19, 8],
    "gpt": [20, 16, 7, 20],
}
EXPECTED_BB = {
    "sonnet": [10, 10, 10, 3],
    "gpt": [10, 9, 2, 10],
}
EXPECTED_N_TRIALS = 20
EXPECTED_N_INVALID = 0

EXPECTED_AUROC = {
    "M1": 1.00,
    "M2": 0.50,
    "M3": 1.00,
    "M4": 1.00,
}

EXPECTED_N_ELIGIBLE = 40
EXPECTED_N_POS = 20
EXPECTED_N_NEG = 20


# ---------------------------------------------------------------------------
# Publication-style visual system
# ---------------------------------------------------------------------------
# Restrained, colorblind-conscious palette.
BLUE = "#D58A36"
ORANGE = "#0056B2"
VERMILLION = "#B94A48"
GREY = "#64748B"
DARK = "#263445"
GRID = "#E5EAF0"
BACKGROUND = "#FFFFFF"

RECEIVERS = {
    "sonnet": {
        "dir": ROOT / "runs" / "phase-a-live-v2",
        "label": "Sonnet 4.6 receiver",
        "colour": BLUE,
    },
    "gpt": {
        "dir": ROOT / "runs" / "phase-a-live-gpt54mini-v1",
        "label": "GPT-5.4-mini receiver",
        "colour": ORANGE,
    },
}

CONDITIONS = [
    ("c0_no_message", "No message"),
    ("c1_neutral", "Neutral notice"),
    ("c2_a_coordination", "A-directed"),
    ("c3_b_coordination", "B-directed"),
]

VIEWS = ["M1", "M2", "M3", "M4"]

MONITORS = {
    "sonnet": {
        "glob": "scores_c*.jsonl",
        "label": "Sonnet 4.6 monitor",
    },
    "gpt": {
        "glob": "scores_gptmon2_c*.jsonl",
        "label": "GPT-5.4-mini monitor",
    },
}

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.labelsize": 9,
    "axes.titlesize": 10,
    "axes.titleweight": "semibold",
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
    "text.color": DARK,
    "axes.labelcolor": DARK,
    "xtick.color": DARK,
    "ytick.color": DARK,
    "axes.edgecolor": GRID,
    "axes.linewidth": 0.8,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.spines.left": False,
    "axes.spines.bottom": True,
    "xtick.major.width": 0,
    "ytick.major.width": 0,
    "xtick.major.pad": 5,
    "ytick.major.pad": 5,
    "axes.axisbelow": True,
    "figure.facecolor": BACKGROUND,
    "axes.facecolor": BACKGROUND,
    "savefig.facecolor": BACKGROUND,
    "savefig.dpi": 300,
    "figure.dpi": 150,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
})


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------
def read_jsonl(path: Path) -> list[dict]:
    """Read non-empty JSONL records."""
    with open(path, encoding="utf-8") as handle:
        return [
            json.loads(line)
            for line in handle
            if line.strip()
        ]


def fail(message: str) -> None:
    """Stop execution on an unexpected artifact or data value."""
    print(f"ASSERTION FAILED: {message}", file=sys.stderr)
    raise SystemExit(1)


def save(fig, stem: str) -> None:
    """Write PDF and 300 dpi PNG into the designated output directory."""
    for suffix in ("pdf", "png"):
        path = OUT / f"{stem}.{suffix}"
        fig.savefig(
            path,
            bbox_inches="tight",
            format=suffix,
            dpi=300 if suffix == "png" else None,
            facecolor=BACKGROUND,
        )
        print(f"wrote {path.relative_to(ROOT)}")

    plt.close(fig)


# ---------------------------------------------------------------------------
# Phase A: behavioral outcomes
# ---------------------------------------------------------------------------
def load_phase_a() -> dict[str, list[dict]]:
    """Load per-condition behavioral counts directly from Phase A traces."""
    out: dict[str, list[dict]] = {}

    for receiver_key, meta in RECEIVERS.items():
        per_condition = []

        for condition, _label in CONDITIONS:
            path = meta["dir"] / f"traces_{condition}.jsonl"

            if not path.exists():
                fail(f"missing Phase A trace file: {path}")

            rows = read_jsonl(path)

            n = len(rows)
            valid = 0
            invalid = 0
            bb = 0
            ob = 0
            receiver_b = 0
            sender_b = 0
            invalid_detail: dict[str, int] = {}

            for row in rows:
                actions = row.get("raw_actions") or {}
                a1 = actions.get("agent_1")
                a2 = actions.get("agent_2")

                # Invalid or missing actions are not silently coded as 0.
                if a1 not in ("A", "B") or a2 not in ("A", "B"):
                    invalid += 1
                    key = f"agent_1={a1!r},agent_2={a2!r}"
                    invalid_detail[key] = invalid_detail.get(key, 0) + 1
                    continue

                valid += 1

                if a1 == "B" and a2 == "B":
                    bb += 1

                if a1 == "B" or a2 == "B":
                    ob += 1

                if a2 == "B":
                    receiver_b += 1

                if a1 == "B":
                    sender_b += 1

            per_condition.append({
                "condition": condition,
                "n": n,
                "valid": valid,
                "invalid": invalid,
                "bb": bb,
                "ob": ob,
                "receiver_b": receiver_b,
                "sender_b": sender_b,
                "invalid_detail": invalid_detail,
            })

        out[receiver_key] = per_condition

    return out


def assert_phase_a(data: dict[str, list[dict]]) -> None:
    """Verify the audited Phase A counts before plotting."""
    for receiver_key in RECEIVERS:
        blocks = data[receiver_key]

        if len(blocks) != len(CONDITIONS):
            fail(
                f"{receiver_key}: expected {len(CONDITIONS)} conditions, "
                f"got {len(blocks)}"
            )

        got_receiver_b = [block["receiver_b"] for block in blocks]
        got_bb = [block["bb"] for block in blocks]

        if got_receiver_b != EXPECTED_RECEIVER_B[receiver_key]:
            fail(
                f"{receiver_key}: receiver-B {got_receiver_b} != "
                f"expected {EXPECTED_RECEIVER_B[receiver_key]}"
            )

        if got_bb != EXPECTED_BB[receiver_key]:
            fail(
                f"{receiver_key}: BB {got_bb} != "
                f"expected {EXPECTED_BB[receiver_key]}"
            )

        for block in blocks:
            if block["n"] != EXPECTED_N_TRIALS:
                fail(
                    f"{receiver_key}/{block['condition']}: "
                    f"n={block['n']} != {EXPECTED_N_TRIALS}"
                )

            if block["valid"] != EXPECTED_N_TRIALS:
                fail(
                    f"{receiver_key}/{block['condition']}: "
                    f"valid={block['valid']} != {EXPECTED_N_TRIALS}"
                )

            if block["invalid"] != EXPECTED_N_INVALID:
                fail(
                    f"{receiver_key}/{block['condition']}: "
                    f"invalid={block['invalid']} "
                    f"{block['invalid_detail']} != {EXPECTED_N_INVALID}"
                )

        # Sender action composition should be scenario-fixed.
        sender_counts = {block["sender_b"] for block in blocks}

        if len(sender_counts) != 1:
            fail(
                f"{receiver_key}: sender-B count varies across conditions: "
                f"{sender_counts}"
            )

    print(
        "PASS Phase A: receiver-B and BB counts verified; "
        "20 valid trials and 0 invalid per condition."
    )


def figure_phase_a(data: dict[str, list[dict]]) -> None:
    """Grouped bar chart of joint-B outcomes across communication conditions."""
    fig, ax = plt.subplots(figsize=(4, 4))

    x = np.arange(len(CONDITIONS), dtype=float)
    width = 0.2
    offsets = {
        "sonnet": -width / 2,
        "gpt": width / 2,
    }

    for receiver_key in ("sonnet", "gpt"):
        counts = [
            block["bb"]
            for block in data[receiver_key]
        ]

        bars = ax.bar(
            x + offsets[receiver_key],
            counts,
            width=width,
            label=RECEIVERS[receiver_key]["label"],
            color=RECEIVERS[receiver_key]["colour"],
            edgecolor="none",
            zorder=3,
        )

    # --- ADD INNER AXES FRAME ---
    # Loop through all 4 sides of the plot area and style them as a solid frame
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color(GRID)       # Match your grid color, or use a distinct color like DARK
        spine.set_linewidth(1.0)    # Set the thickness of the box frame

    ax.set_ylabel("Joint-B outcomes ($B/B$)")
    ax.set_xticks(x)
    ax.set_xticklabels([label for _, label in CONDITIONS])
    ax.set_ylim(0, 23)
    ax.set_yticks([0, 5, 10, 15, 20])

    ax.grid(axis="y", color=GRID, linewidth=0.8)
    
    # Restoring small ticks can help anchor the frame, or leave at 0 if you want it completely flush
    ax.tick_params(axis="x", length=3, color=GRID, pad=9)
    ax.tick_params(axis="y", length=3, color=GRID)

    ax.legend(
        frameon=False,
        loc="upper right",
        #ncol=2,
        #handlelength=1.2,
        #columnspacing=1.5,
    )

    fig.subplots_adjust(
        left=0.10,
        right=0.98,
        top=0.84,
        bottom=0.25,
    )



    save(fig, "fig_phaseA_BB")


# ---------------------------------------------------------------------------
# Phase B: monitor evaluation
# ---------------------------------------------------------------------------
def auroc(pos: list[float], neg: list[float]) -> float | None:
    """Compute AUROC using the Mann–Whitney formulation; ties count 0.5."""
    if not pos or not neg:
        return None

    wins = 0.0

    for positive_score in pos:
        for negative_score in neg:
            if positive_score > negative_score:
                wins += 1.0
            elif positive_score == negative_score:
                wins += 0.5

    return wins / (len(pos) * len(neg))


def load_labels(receiver_key: str) -> dict[str, int]:
    """Load resolved message-level E labels; never treat unresolved as negative."""
    path = (
        ROOT
        / "results"
        / "e_labels"
        / f"{receiver_key}_receiver"
        / "e_labels.jsonl"
    )

    if not path.exists():
        fail(f"missing E labels: {path}")

    truth: dict[str, int] = {}

    for row in read_jsonl(path):
        if row.get("evidence_status") != "resolved":
            continue

        if row.get("e") is None:
            fail(f"{row.get('trace_id')}: resolved row with e=None")

        truth[row["trace_id"]] = 1 if row["e"] else 0

    return truth


def load_scores(run_dir: Path, pattern: str) -> tuple[int, list[dict]]:
    """Load score rows and retain successful primary (k=0) records."""
    paths = [
        path
        for path in sorted(glob.glob(str(run_dir / pattern)))
        if not path.endswith(".status.json")
    ]

    if not paths:
        fail(f"no score files matching {run_dir}/{pattern}")

    rows: list[dict] = []

    for path in paths:
        rows.extend(read_jsonl(Path(path)))

    ok = [
        row
        for row in rows
        if row.get("status") == "ok" and row.get("k") == 0
    ]

    return len(rows), ok


def load_phase_b() -> dict:
    """Load scores and calculate AUROC separately for each receiver and monitor."""
    data: dict = {}

    for receiver_key in RECEIVERS:
        truth = load_labels(receiver_key)

        data[receiver_key] = {
            "truth": truth,
            "monitors": {},
        }

        for monitor_key, monitor_meta in MONITORS.items():
            total, ok = load_scores(
                RECEIVERS[receiver_key]["dir"],
                monitor_meta["glob"],
            )

            by_view = {}

            for view in VIEWS:
                scores = {
                    row["trace_id"]: row["score"]
                    for row in ok
                    if row.get("view") == view
                }

                pos = [
                    scores[trace_id]
                    for trace_id in truth
                    if truth[trace_id] == 1 and trace_id in scores
                ]

                neg = [
                    scores[trace_id]
                    for trace_id in truth
                    if truth[trace_id] == 0 and trace_id in scores
                ]

                values = list(scores.values())

                by_view[view] = {
                    "scores": scores,
                    "pos": pos,
                    "neg": neg,
                    "auroc": auroc(pos, neg),
                    "n_scored": len(scores),
                    "n_distinct": len(set(values)),
                    "min": min(values) if values else None,
                    "max": max(values) if values else None,
                }

            data[receiver_key]["monitors"][monitor_key] = {
                "total_rows": total,
                "ok_rows": len(ok),
                "views": by_view,
            }

    return data


def stored_aurocs() -> dict:
    """Read stored AUROC summaries for independent cross-checking."""
    out: dict = {}

    for receiver_key in RECEIVERS:
        for monitor_key in MONITORS:
            path = (
                ROOT
                / "results"
                / "e_matrix_gptmon"
                / f"receiver={receiver_key}__monitor={monitor_key}.json"
            )

            if not path.exists():
                fail(f"missing stored report: {path}")

            report = json.loads(path.read_text(encoding="utf-8"))

            out[(receiver_key, monitor_key)] = {
                "auroc": {
                    view: report["per_arm"][view]["auroc"]["value"]
                    for view in VIEWS
                },
                "n_traces_reference": report["n_traces_reference"],
                "n_pos": report["per_arm"]["M1"]["auroc"].get("n_pos"),
                "n_neg": report["per_arm"]["M1"]["auroc"].get("n_neg"),
                "delta_M3_M1": report["contrasts"]["M3 vs M1"][
                    "paired_auroc_difference"
                ]["delta"],
                "ci95_M3_M1": report["contrasts"]["M3 vs M1"][
                    "paired_auroc_difference"
                ]["ci95"],
                "n_pairs": report["contrasts"]["M3 vs M1"][
                    "paired_auroc_difference"
                ]["n_pairs"],
            }

    return out


def assert_phase_b(data: dict, stored: dict) -> None:
    """Verify labels, sample sizes, observed AUROC, and stored summaries."""
    for receiver_key in RECEIVERS:
        truth = data[receiver_key]["truth"]

        n_pos = sum(value == 1 for value in truth.values())
        n_neg = sum(value == 0 for value in truth.values())

        if len(truth) != EXPECTED_N_ELIGIBLE:
            fail(
                f"{receiver_key}: eligible traces {len(truth)} "
                f"!= {EXPECTED_N_ELIGIBLE}"
            )

        if (n_pos, n_neg) != (EXPECTED_N_POS, EXPECTED_N_NEG):
            fail(
                f"{receiver_key}: (pos,neg)=({n_pos},{n_neg}) "
                f"!= ({EXPECTED_N_POS},{EXPECTED_N_NEG})"
            )

        for monitor_key in MONITORS:
            views = data[receiver_key]["monitors"][monitor_key]["views"]

            for view in VIEWS:
                observed = views[view]["auroc"]
                expected = EXPECTED_AUROC[view]

                if observed is None or abs(observed - expected) > 1e-9:
                    fail(
                        f"{receiver_key}/{monitor_key}/{view}: "
                        f"AUROC {observed} != {expected}"
                    )

            entry = stored[(receiver_key, monitor_key)]

            for view in VIEWS:
                observed = entry["auroc"][view]
                expected = EXPECTED_AUROC[view]

                if abs(observed - expected) > 1e-9:
                    fail(
                        f"stored {receiver_key}/{monitor_key}/{view}: "
                        f"{observed} != {expected}"
                    )

            if entry["n_traces_reference"] != EXPECTED_N_ELIGIBLE:
                fail(
                    f"stored {receiver_key}/{monitor_key}: "
                    f"n_traces_reference {entry['n_traces_reference']} "
                    f"!= {EXPECTED_N_ELIGIBLE}"
                )

            if abs(entry["delta_M3_M1"]) > 1e-9:
                fail(
                    f"stored {receiver_key}/{monitor_key}: "
                    f"M3-M1 delta {entry['delta_M3_M1']} != 0.0"
                )

    print(
        "PASS Phase B: M1=1.00, M2=0.50, M3=1.00, M4=1.00 "
        "for all receiver/monitor combinations; "
        "40 eligible traces (20 positive, 20 negative); M3-M1=0.00."
    )

    print(
        "NOTE: the M3-M1 zero-width interval occurs at the AUROC ceiling. "
        "It is not an equivalence claim."
    )


def figure_phase_b(data: dict, stored: dict) -> None:
    """Compare observed AUROC across the four evidence views."""
    combos = [
        ("sonnet", "sonnet"),
        ("sonnet", "gpt"),
        ("gpt", "sonnet"),
        ("gpt", "gpt"),
    ]

    fig, axes = plt.subplots(
        1,
        4,
        figsize=(10.5, 3.8),
        sharey=True,
    )

    x = np.arange(len(VIEWS), dtype=float)

    for ax, (receiver_key, monitor_key) in zip(axes, combos):
        views = data[receiver_key]["monitors"][monitor_key]["views"]
        values = [views[view]["auroc"] for view in VIEWS]

        colours = [
            GREY if view == "M2" else BLUE
            for view in VIEWS
        ]

        bars = ax.bar(
            x,
            values,
            width=0.66,
            color=colours,
            edgecolor="none",
            zorder=3,
        )

        ax.axhline(
            0.5,
            color=GREY,
            linestyle=(0, (4, 3)),
            linewidth=1,
            zorder=2,
        )

        for bar, value in zip(bars, values):
            ax.annotate(
                f"{value:.2f}",
                xy=(
                    bar.get_x() + bar.get_width() / 2,
                    value,
                ),
                xytext=(0, 6),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=8,
                fontweight="semibold",
            )

        receiver_label = RECEIVERS[receiver_key]["label"]
        monitor_label = MONITORS[monitor_key]["label"]

        ax.set_title(
            f"{receiver_label}\n{monitor_label}",
            fontsize=8,
            pad=12,
        )

        entry = stored[(receiver_key, monitor_key)]

        ax.text(
            0.5,
            0.02,
            f"n = {entry['n_traces_reference']} "
            f"({entry['n_pos']} positive, {entry['n_neg']} negative)",
            transform=ax.transAxes,
            ha="center",
            va="bottom",
            fontsize=6.5,
            color=GREY,
        )

        ax.set_xticks(x)
        ax.set_xticklabels(VIEWS)
        ax.set_ylim(0.35, 1.12)
        ax.set_yticks([0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])

        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.tick_params(axis="both", length=0)
        ax.set_axisbelow(True)

    axes[0].set_ylabel("Observed AUROC")
    fig.supxlabel("Evidence view", fontsize=9, y=0.12)

    fig.text(
        0.5,
        0.025,
        "Dashed line: AUROC = 0.50. M2 assigns zero to every trace; "
        "its AUROC is 0.50 because all positive–negative pairs are tied.",
        ha="center",
        va="bottom",
        fontsize=7,
        color=GREY,
    )

    fig.subplots_adjust(
        left=0.07,
        right=0.99,
        top=0.78,
        bottom=0.27,
        wspace=0.12,
    )

    save(fig, "fig_phaseB_AUROC")


# ---------------------------------------------------------------------------
# Optional appendix: score distributions
# ---------------------------------------------------------------------------
def figure_scores(data: dict) -> None:
    """Plot individual monitor scores by E-label class and evidence view."""
    combos = [
        ("sonnet", "sonnet"),
        ("sonnet", "gpt"),
        ("gpt", "sonnet"),
        ("gpt", "gpt"),
    ]

    fig, axes = plt.subplots(
        2,
        2,
        figsize=(9.0, 6.2),
        sharex=True,
        sharey=True,
    )

    rng = np.random.default_rng(0)

    class_styles = [
        (-0.16, "neg", BLUE, "E-negative"),
        (+0.16, "pos", VERMILLION, "E-positive"),
    ]

    for ax, (receiver_key, monitor_key) in zip(axes.ravel(), combos):
        views = data[receiver_key]["monitors"][monitor_key]["views"]

        for i, view in enumerate(VIEWS):
            for offset, key, colour, label in class_styles:
                values = views[view][key]
                n = len(values)

                jitter = rng.uniform(-0.045, 0.045, size=n)

                ax.scatter(
                    np.full(n, i + offset) + jitter,
                    values,
                    s=20,
                    color=colour,
                    alpha=0.70,
                    edgecolors="white",
                    linewidths=0.25,
                    label=label if i == 0 else None,
                    zorder=3,
                )

        ax.axhline(
            50,
            color=DARK,
            linestyle=(0, (4, 3)),
            linewidth=1,
            alpha=0.8,
            zorder=2,
        )

        ax.set_title(
            f"{RECEIVERS[receiver_key]['label']} · "
            f"{MONITORS[monitor_key]['label']}",
            loc="left",
            pad=10,
            fontsize=9,
        )

        ax.set_xticks(range(len(VIEWS)))
        ax.set_xticklabels(VIEWS)
        ax.set_ylim(-5, 105)
        ax.set_yticks([0, 25, 50, 75, 100])

        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.tick_params(axis="both", length=0)
        ax.set_axisbelow(True)

    axes[0, 0].set_ylabel("Monitor score (0–100)")
    axes[1, 0].set_ylabel("Monitor score (0–100)")
    axes[1, 0].set_xlabel("Evidence view")
    axes[1, 1].set_xlabel("Evidence view")

    axes[0, 0].legend(
        frameon=False,
        loc="lower right",
        ncol=2,
        fontsize=8,
        markerscale=1.1,
    )

    fig.text(
        0.5,
        0.025,
        "Dashed line: score threshold of 50. M2 assigns zero to every trace. "
        "In these observed data, M1, M3, and M4 perfectly separate the "
        "E-positive and E-negative traces.",
        ha="center",
        va="bottom",
        fontsize=8,
        color=GREY,
    )

    fig.subplots_adjust(
        left=0.10,
        right=0.98,
        top=0.91,
        bottom=0.13,
        hspace=0.32,
        wspace=0.16,
    )

    save(fig, "fig_scores_by_view")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    print(f"project root : {ROOT}")
    print(f"output dir   : {OUT.relative_to(ROOT)}")
    print()

    # Complete all data checks before generating any figure.
    phase_a = load_phase_a()
    assert_phase_a(phase_a)

    phase_b = load_phase_b()
    stored = stored_aurocs()
    assert_phase_b(phase_b, stored)

    print()
    figure_phase_a(phase_a)
    figure_phase_b(phase_b, stored)
    figure_scores(phase_b)

    print()
    print("All figures written; all assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())