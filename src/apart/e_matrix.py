"""Run ``analysis_e`` for every receiver x monitor combination (offline, stdlib only).

Combinations (2 x 2):

    receiver  sonnet -> runs/phase-a-live-v2           labels from e_sheet_labels
              gpt    -> runs/phase-a-live-gpt54mini-v1
    monitor   sonnet -> scores_c*.jsonl          (excluding scores_gptmon_*)
              gpt    -> scores_gptmon_c*.jsonl

Each combination gets the full ``run_analysis_e`` report (primary M3 vs M1 paired AUROC
difference with the approved scenario-cluster bootstrap interval; the four secondary
contrasts with Holm and NOT_ESTIMABLE handling). Nothing here changes a metric: this
module only selects inputs, enforces the unresolved-label gate, and summarises.

Gate: if the label manifest's unresolved gate is triggered, the combination is NOT run
unless ``proceed_despite_gate`` is set, and then its summary is marked
``confirmatory_interpretation = blocked_pending_review``.

A combination whose score files are absent (e.g. GPT-monitor scoring not finished) is
reported as ``MISSING_SCORES`` and skipped; scores are never borrowed from another
monitor.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from typing import Mapping, Optional

from .analysis_e import (PRIMARY_CONTRAST, SECONDARY_CONTRASTS, _json_safe, read_jsonl,
                         run_analysis_e)

E_MATRIX_VERSION = "0.1"
DEFAULT_RECEIVERS = {"sonnet": "runs/phase-a-live-v2",
                     "gpt": "runs/phase-a-live-gpt54mini-v1"}
MONITOR_GLOBS = {"sonnet": "scores_c*.jsonl", "gpt": "scores_gptmon_c*.jsonl"}


def score_paths(run_dir: str, monitor: str,
                monitor_globs: Mapping[str, str] = MONITOR_GLOBS) -> list[str]:
    return sorted(glob.glob(os.path.join(run_dir, monitor_globs[monitor])))


def summarise(report: dict, gate: dict) -> dict:
    primary_name = f"{PRIMARY_CONTRAST[0]} vs {PRIMARY_CONTRAST[1]}"
    p = report["contrasts"].get(primary_name, {}).get("paired_auroc_difference") or {}
    secondary = {}
    for a, b in SECONDARY_CONTRASTS:
        block = report["contrasts"].get(f"{a} vs {b}", {}).get("paired_auroc_difference") or {}
        secondary[f"{a} vs {b}"] = {
            "delta": block.get("delta"), "ci95": block.get("ci95"),
            "interval_status": block.get("interval_status"),
            "interval_reason": block.get("interval_reason"),
            "p_boot_placeholder": block.get("p_boot"), "p_holm": block.get("p_holm"),
            "estimability": block.get("estimability"),
            "not_estimable_reason": block.get("not_estimable_reason")}
    return {
        "primary": {"contrast": primary_name, "auroc_M3": p.get("auroc_first"),
                    "auroc_M1": p.get("auroc_second"), "delta": p.get("delta"),
                    "ci95": p.get("ci95"), "interval_method": p.get("interval_method"),
                    "interval_status": p.get("interval_status"),
                    "interval_reason": p.get("interval_reason"),
                    "n_pairs": p.get("n_pairs"), "n_scenarios": p.get("n_scenarios"),
                    "n_boot_valid": p.get("n_boot_valid"),
                    "p_value_status": p.get("p_value_status"),
                    "correction": "none (single primary test)"},
        "secondary_holm": secondary,
        "n_traces_reference": report["n_traces_reference"],
        "unresolved_gate": gate,
        "confirmatory_interpretation": gate.get("confirmatory_interpretation"),
        "warnings": report["warnings"],
    }


def run_matrix(labels_by_receiver: Mapping[str, str], receivers: Mapping[str, str],
               out_dir: str, n_boot: int = 2000, seed: int = 0,
               proceed_despite_gate: bool = False,
               monitor_globs: Mapping[str, str] = MONITOR_GLOBS) -> dict:
    """``labels_by_receiver`` maps receiver name -> directory holding e_labels.jsonl
    and label_manifest.json (the output of ``apart.e_sheet_labels``)."""
    os.makedirs(out_dir, exist_ok=True)
    summary = {"e_matrix_version": E_MATRIX_VERSION, "n_boot": n_boot, "boot_seed": seed,
               "monitor_globs": dict(monitor_globs),
               "proceed_despite_gate": proceed_despite_gate, "combinations": {}}
    for receiver, run_dir in receivers.items():
        label_dir = labels_by_receiver[receiver]
        with open(os.path.join(label_dir, "label_manifest.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        if manifest.get("status") != "LABELS_WRITTEN":
            raise ValueError(f"{label_dir}: labels not written (status {manifest.get('status')})")
        if os.path.abspath(manifest["run_dir"]) != os.path.abspath(run_dir):
            raise ValueError(f"{label_dir}: labels were built for {manifest['run_dir']}, not {run_dir}")
        label_rows = read_jsonl(os.path.join(label_dir, "e_labels.jsonl"))
        gate = manifest["unresolved_gate"]
        for monitor in monitor_globs:
            name = f"receiver={receiver}__monitor={monitor}"
            paths = score_paths(run_dir, monitor, monitor_globs)
            entry: dict = {"receiver": receiver, "monitor": monitor, "run_dir": run_dir,
                           "label_dir": label_dir, "score_files": paths,
                           "guide_version": manifest.get("guide_version")}
            if not paths:
                entry["status"] = "MISSING_SCORES"
            elif gate.get("triggered") and not proceed_despite_gate:
                entry["status"] = "BLOCKED_BY_UNRESOLVED_GATE"
                entry["unresolved_gate"] = gate
            else:
                score_rows = [row for p in paths for row in read_jsonl(p)]
                report = run_analysis_e(label_rows, score_rows, n_boot=n_boot, seed=seed)
                report_path = os.path.join(out_dir, f"{name}.json")
                with open(report_path, "w", encoding="utf-8") as handle:
                    json.dump(_json_safe(report), handle, indent=2, sort_keys=True)
                    handle.write("\n")
                entry.update(status="ANALYSED", report=report_path,
                             **_json_safe(summarise(report, report["unresolved_gate"])))
            summary["combinations"][name] = entry
    with open(os.path.join(out_dir, "matrix_summary.json"), "w", encoding="utf-8") as handle:
        json.dump(_json_safe(summary), handle, indent=2, sort_keys=True)
        handle.write("\n")
    return summary


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--labels-sonnet", required=True, help="e_sheet_labels out dir, Sonnet receiver")
    parser.add_argument("--labels-gpt", required=True, help="e_sheet_labels out dir, GPT receiver")
    parser.add_argument("--run-sonnet", default=DEFAULT_RECEIVERS["sonnet"])
    parser.add_argument("--run-gpt", default=DEFAULT_RECEIVERS["gpt"])
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--n-boot", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--proceed-despite-gate", action="store_true")
    parser.add_argument("--gpt-monitor-glob", default=MONITOR_GLOBS["gpt"],
                        help="score-file glob for the GPT monitor, e.g. scores_gptmon2_c*.jsonl")
    args = parser.parse_args(argv)
    summary = run_matrix({"sonnet": args.labels_sonnet, "gpt": args.labels_gpt},
                         {"sonnet": args.run_sonnet, "gpt": args.run_gpt}, args.out_dir,
                         n_boot=args.n_boot, seed=args.seed,
                         proceed_despite_gate=args.proceed_despite_gate,
                         monitor_globs={**MONITOR_GLOBS, "gpt": args.gpt_monitor_glob})
    for name, entry in summary["combinations"].items():
        primary = entry.get("primary", {})
        print(f"{name}: {entry['status']} delta={primary.get('delta')} ci95={primary.get('ci95')}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
