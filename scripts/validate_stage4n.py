#!/usr/bin/env python3
"""Offline validator for the frozen Stage 4N probe artifacts (no network, no writes to the run dir).

    python scripts/validate_stage4n.py --run-dir runs/ext-probe-v21-sonnet46 \
        --out-dir reports/stage1_stage4n_validation/<stamp>

Checks, in order:

  HASH-*     the frozen artifacts are byte-identical to the recorded pins
  MANIFEST-* the probe manifest validates under ``apart.behavior_probe``
  POLICY-*   the run policy parses and binds to the manifest
  PROMPT-*   ``prompt_files_sha256`` still matches the working tree
  LEDGER-*   the ledger validates and agrees with the manifest binding
  ROWS-*     trace count, plan order, row schema and recomputed joint outcomes
  TRACE-*    every probe row converts to a canonical ``apart.schema.Trace``
  RECON-*    ledger and provenance agree on (call_id, attempt), amount and state
  BUDGET-*   the recomputed worst-case plan still fits the cap and the headroom
  SUMMARY-*  the recomputed summary equals the saved ``probe_summary.json``

Nothing here is written back to the run directory, and the saved summary is only
ever read. The report goes to ``--out-dir``, which must not already exist.

Status: a validation harness for a feasibility probe. It measures accounting
consistency, not monitor performance and not collusion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
for _extra in (ROOT, ROOT / "src"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from agents.runner import STAGE_CONTEXT, build_trace  # noqa: E402
from apart.behavior_probe import (  # noqa: E402
    INVALID,
    OUTCOMES,
    ProbeError,
    check_binding,
    evaluate_gate,
    joint_outcome,
    load_probe_manifest,
    plan_worst_case,
    probe_plan,
)
from apart.schema import Trace  # noqa: E402
from monitors.ledger import (  # noqa: E402
    LedgerError,
    Policy,
    commitments,
    config_sha256,
    headroom,
    validate as validate_ledger,
)

VALIDATOR_VERSION = "stage4n-validator/1.0"

# Recorded sha256 of the frozen Stage 4N live artifacts (MM 2026-10-23). These
# pin the run of record; a mismatch means an artifact changed after the fact and
# must be investigated, never silently re-pinned.
FROZEN_PINS = {
    "manifest.json": "c7638cfe26f75cabbb56f2df1d731d0dcb123da53cd85de320a7d1174dd48fc7",
    "policy.json": "6d362afaa8b9a179a729152bd18c957ecf0dff273536a70c6cd73fa6e883f27e",
    "out/probe_traces.jsonl": "33fdacf96a0aa39212771d3012202f495725c761b0b5c4f95e511791543570a9",
    "out/probe_summary.json": "e39e60fd5a1237c455024830992c79023a9333625d11e3b47cf84144da26d6ff",
    "provenance/calls.jsonl": "708758cb978c00a1010f78c7f8ebe3cba12228e701ece69f01ff41574c0921e6",
    "spend/ledger.json": "22ff07b0fb6a2fc43b036728b5158afa3dfa57be0eb2322e86dca1a7c134eb5d",
}

PROTECTED_DIRS = tuple(ROOT / name for name in ("data", "results", "configs", "docs", "AI Context"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as exc:
                raise ProbeError(f"{path}:{number} is not valid JSON: {exc}") from exc
    return rows


class Report:
    """Ordered pass/fail findings; the only output of this script."""

    def __init__(self) -> None:
        self.checks: list[dict] = []

    def check(self, check_id: str, ok: bool, detail: Any = None) -> bool:
        self.checks.append({"id": check_id, "ok": bool(ok), "detail": detail})
        return bool(ok)

    def fail(self, check_id: str, detail: Any = None) -> None:
        self.check(check_id, False, detail)

    @property
    def failures(self) -> list[dict]:
        return [c for c in self.checks if not c["ok"]]

    def as_dict(self) -> dict:
        return {
            "validator_version": VALIDATOR_VERSION,
            "checks": self.checks,
            "n_checks": len(self.checks),
            "n_failed": len(self.failures),
            "passed": not self.failures,
        }


# ------------------------------------------------------------------ checks

def check_frozen_pins(run_dir: Path, report: Report) -> dict:
    """The artifacts of record must be byte-identical to the recorded pins."""
    observed = {}
    for relative, pinned in FROZEN_PINS.items():
        path = run_dir / relative
        if not path.is_file():
            report.fail(f"HASH {relative}", {"missing": True, "pinned": pinned})
            continue
        digest = sha256_file(path)
        observed[relative] = digest
        report.check(f"HASH {relative}", digest == pinned,
                     {"sha256": digest, "pinned": pinned})
    return observed


def check_manifest_and_binding(run_dir: Path, report: Report):
    """Manifest validity, policy parse, model/cap binding, prompt-file pins."""
    manifest = load_probe_manifest(run_dir / "manifest.json")
    report.check("MANIFEST validates", True,
                 {"run_id": manifest["run_id"],
                  "probe_manifest_version": manifest["probe_manifest_version"],
                  "protocol_version": manifest["protocol_version"],
                  "prompt_version": manifest["prompt_version"],
                  "cells": manifest["cells"], "per_cell": manifest["per_cell"],
                  "max_traces": manifest["max_traces"], "max_calls": manifest["max_calls"]})

    policy_bytes = (run_dir / "policy.json").read_bytes()
    policy = Policy.from_config(json.loads(policy_bytes.decode("utf-8")))
    binding_hash = config_sha256(policy_bytes)
    report.check("POLICY parses", True, {"config_sha256": binding_hash})

    try:
        check_binding(manifest, policy)
        report.check("BINDING manifest<->policy", True,
                     {"model": f"{manifest['provider']}/{manifest['model']}",
                      "cap_usd": manifest["cap_usd"]})
    except ProbeError as exc:
        report.fail("BINDING manifest<->policy", str(exc))

    missing = [name for name in manifest["prompt_files_sha256"] if not (ROOT / name).is_file()]
    stale = {name: {"pinned": digest, "observed": sha256_file(ROOT / name)}
             for name, digest in manifest["prompt_files_sha256"].items()
             if (ROOT / name).is_file() and sha256_file(ROOT / name) != digest}
    report.check("PROMPT files match manifest pins", not stale and not missing,
                 {"checked": len(manifest["prompt_files_sha256"]),
                  "stale": sorted(stale), "missing": missing})
    return manifest, policy, binding_hash


def check_ledger(run_dir: Path, manifest: Mapping, policy, binding_hash: str,
                 report: Report) -> dict:
    """Structural validation, run header, and incident/unknown-state checks."""
    ledger = json.loads((run_dir / "spend" / "ledger.json").read_text(encoding="utf-8"))
    try:
        validate_ledger(ledger, expected_config_hash=binding_hash, policy=policy)
        report.check("LEDGER validates (structure, checksum, binding)", True,
                     {"version": ledger["version"], "events": len(ledger["events"])})
    except LedgerError as exc:
        report.fail("LEDGER validates (structure, checksum, binding)", str(exc))

    header = ledger["runs"].get(manifest["run_id"])
    report.check("LEDGER run header present", header is not None,
                 {"runs": sorted(ledger["runs"])})
    if header is not None:
        provenance = header.get("provenance") or {}
        report.check("LEDGER run provenance names the bound model",
                     provenance.get("model") == f"{manifest['provider']}/{manifest['model']}"
                     and provenance.get("base_url") == manifest["base_url"],
                     {k: provenance.get(k) for k in ("model", "base_url")})
        report.check("LEDGER run header config matches the policy binding",
                     header["config_sha256"] == binding_hash)
    overrun = [e["call_id"] for e in ledger["events"]
               if e.get("settled_micro") is not None and e["settled_micro"] > e["reserved_micro"]]
    report.check("LEDGER has no settlement over its reservation", not overrun, overrun)
    report.check("LEDGER has no unknown-cost event",
                 not [e for e in ledger["events"] if e["state"] == "unknown"])
    report.check("LEDGER audit/imports/missing_evidence are empty",
                 ledger["audit"] == [] and ledger["imports"] == []
                 and ledger["missing_evidence"] == [])
    return ledger


def check_rows(rows: list[dict], manifest: Mapping, report: Report) -> list:
    """Trace count, plan order, row schema, and recomputed joint outcomes."""
    from agents.runner import receiver_for, sender_for
    from agents.prompts import VARIANTS

    plan = probe_plan(manifest)
    report.check("ROWS count equals the plan and max_traces",
                 len(rows) == len(plan) == manifest["max_traces"],
                 {"rows": len(rows), "plan": len(plan), "max_traces": manifest["max_traces"]})
    report.check("ROWS trace_ids are unique",
                 len({r.get("trace_id") for r in rows}) == len(rows))
    report.check("ROWS seeds are unique",
                 len({r.get("seed") for r in rows}) == len(rows))
    observed_order = [(r.get("seed"), r.get("variant"), r.get("condition")) for r in rows]
    expected_order = [(c.seed, c.variant, c.condition) for c in plan]
    report.check("ROWS order matches the round-robin plan", observed_order == expected_order,
                 {"observed": observed_order} if observed_order != expected_order else "match")

    required = ("trace_id", "seed", "variant", "condition", "outcome", "invalid_reason",
                "sender", "receiver", "sender_brief", "receiver_brief",
                "s1", "r1", "s2", "s3", "r2", "stopped_after",
                "prompt_version", "requested_model", "served_models")
    problems: list[dict] = []
    for row in rows:
        trace_id = row.get("trace_id", "<missing trace_id>")
        missing = [name for name in required if name not in row]
        if missing:
            problems.append({"trace_id": trace_id, "missing_fields": missing})
            continue
        seed = int(row["seed"])
        if row["trace_id"] != f"ext-{seed:04d}":
            problems.append({"trace_id": trace_id, "trace_id_format": row["trace_id"]})
        if row["variant"] not in VARIANTS:
            problems.append({"trace_id": trace_id, "unknown_variant": row["variant"]})
        else:
            cfg = VARIANTS[row["variant"]]
            if row["sender_brief"] != cfg["sender"] or row["receiver_brief"] != cfg["receiver"]:
                problems.append({"trace_id": trace_id, "brief_mismatch": True})
        if row["sender"] != sender_for(seed) or row["receiver"] != receiver_for(seed):
            problems.append({"trace_id": trace_id, "role_mismatch": True})
        if row["prompt_version"] != manifest["prompt_version"]:
            problems.append({"trace_id": trace_id, "prompt_version": row["prompt_version"]})
        if row["requested_model"] != manifest["model"]:
            problems.append({"trace_id": trace_id, "requested_model": row["requested_model"]})
        served = row["served_models"]
        if not isinstance(served, list) or any(m != manifest["model"] for m in served):
            problems.append({"trace_id": trace_id, "served_models": served})
        recomputed = joint_outcome(row)
        if (row["outcome"], row["invalid_reason"]) != recomputed:
            problems.append({"trace_id": trace_id, "stored": [row["outcome"], row["invalid_reason"]],
                             "recomputed": list(recomputed)})
        if row["outcome"] not in OUTCOMES + (INVALID,):
            problems.append({"trace_id": trace_id, "unknown_outcome": row["outcome"]})
        if row["stopped_after"] is not None:
            problems.append({"trace_id": trace_id, "stopped_after": row["stopped_after"]})
    report.check("ROWS per-row schema, plan binding and recomputed outcome", not problems,
                 {"n_rows": len(rows), "problems": problems})
    return plan


def check_trace_conversion(rows: list[dict], manifest: Mapping, report: Report) -> list:
    """Every probe row must convert to a canonical Trace, with per-row failures reported."""
    traces, failures = [], []
    for row in rows:
        trace_id = row.get("trace_id", "<missing trace_id>")
        try:
            trace = build_trace(dict(row))
            if not isinstance(trace, Trace):
                raise ProbeError(f"build_trace returned {type(trace).__name__}")
            trace.validate()
            seed = int(row["seed"])
            if (trace.trace_id, trace.seed) != (f"ext-{seed:04d}", seed):
                raise ProbeError("trace identity does not match the row")
            if trace.delivery_condition.value != "FREE_MSG":
                raise ProbeError(f"unexpected delivery condition {trace.delivery_condition!r}")
            actions = trace.actions()
            want = {row["sender"]: row["s3"]["raw_action"], row["receiver"]: row["r2"]["raw_action"]}
            got = {agent: (action.value if action is not None else None) for agent, action in actions.items()}
            if got != want:
                raise ProbeError(f"parsed actions {got} != raw actions {want}")
            if row["s2"] and not row["s2"].get("message_declined"):
                if [m.text for m in trace.messages] != [row["s2"]["message"]]:
                    raise ProbeError("message text does not match the row")
            elif trace.messages:
                raise ProbeError("declined/no message expected, but messages were built")
            traces.append(trace)
        except (ProbeError, ValueError, KeyError, TypeError) as exc:
            failures.append({"trace_id": trace_id, "error": f"{type(exc).__name__}: {exc}"})
    report.check("TRACE every probe row converts to a canonical Trace",
                 not failures and len(traces) == len(rows),
                 {"converted": len(traces), "requested": len(rows), "failures": failures})
    return traces



STAGE_ORDER = ("s1", "r1", "s2", "s3", "r2")
STAGE_LABEL = {"s1": "plan", "r1": "plan", "s2": "message", "s3": "action", "r2": "action"}


def check_reconciliation(run_dir: Path, ledger: Mapping, rows: list[dict], manifest: Mapping,
                         report: Report) -> dict:
    """Ledger and provenance must agree exactly, keyed on (call_id, attempt)."""
    provenance = read_jsonl(run_dir / "provenance" / "calls.jsonl")
    events = ledger["events"]
    report.check("RECON provenance count equals the ledger event count",
                 len(provenance) == len(events),
                 {"events": len(events), "provenance": len(provenance)})

    left = {(e["call_id"], e["attempt"]): e for e in events}
    right = {(r["call_id"], r["attempt"]): r for r in provenance}
    report.check("RECON (call_id, attempt) key sets are identical", set(left) == set(right),
                 {"only_ledger": sorted(set(left) - set(right)),
                  "only_provenance": sorted(set(right) - set(left))})

    expected = {(f"{row['trace_id']}:{stage}:p1", 0)
                for row in rows for stage in STAGE_ORDER}
    report.check("RECON every key is one stage attempt of one row",
                 set(left) == expected,
                 {"unexpected": sorted(k[0] for k in set(left) - expected),
                  "missing": sorted(k[0] for k in expected - set(left))})

    mismatches = []
    for key in sorted(set(left) & set(right)):
        event, record = left[key], right[key]
        for field in ("reserved_micro", "settled_micro"):
            if event.get(field) != record.get(field):
                mismatches.append({"key": list(key), "field": field,
                                   "ledger": event.get(field), "provenance": record.get(field)})
        if event.get("state") != record.get("settlement_state"):
            mismatches.append({"key": list(key), "field": "state",
                               "ledger": event.get("state"),
                               "provenance": record.get("settlement_state")})
    report.check("RECON reserved, settled and state agree per event", not mismatches,
                 mismatches[:20] or "match")

    binding = []
    for key in sorted(set(left) & set(right)):
        stage = key[0].split(":")[1]
        record = right[key]
        context = tuple(record.get("context_stages") or ())
        if context != tuple(STAGE_CONTEXT[stage]):
            binding.append({"key": list(key), "stage": stage, "context_stages": list(context)})
        elif record.get("input_messages") != 2 + 2 * len(context):
            binding.append({"key": list(key), "input_messages": record.get("input_messages")})
        elif record.get("stage") != STAGE_LABEL[stage]:
            binding.append({"key": list(key), "stage_label": record.get("stage")})
    report.check("RECON carried context matches agents.runner.STAGE_CONTEXT", not binding,
                 binding[:20] or "match")

    report.check("RECON every event was dispatched",
                 all(e.get("dispatched_ts") for e in events)
                 and all(r.get("dispatched") is True for r in provenance))
    report.check("RECON request hash format matches the manifest",
                 {r.get("request_hash_format") for r in provenance}
                 == {manifest["request_hash_format"]})
    report.check("RECON served model matches the requested model",
                 {r.get("served_model") for r in provenance} == {manifest["model"]},
                 sorted({str(r.get("served_model")) for r in provenance}))
    missing_usage = [r["call_id"] for r in provenance
                     if not isinstance(r.get("usage"), Mapping)]
    report.check("RECON every settled record carries provider usage", not missing_usage,
                 missing_usage[:10] or "all present")

    totals = {
        "events": len(events),
        "reserved_micro": sum(e["reserved_micro"] for e in events),
        "settled_micro": sum(e["settled_micro"] or 0 for e in events),
        "provenance_records": len(provenance),
    }
    report.check("RECON every settlement is at or below its reservation",
                 totals["settled_micro"] <= totals["reserved_micro"], totals)
    return totals



def check_budget_and_summary(run_dir: Path, manifest: Mapping, policy, ledger: Mapping,
                             rows: list[dict], report: Report) -> dict:
    """Recompute the budget plan and the summary; compare with what was saved."""
    worst = plan_worst_case(manifest, policy)
    report.check("BUDGET recomputed worst case fits the cap", worst["fits"],
                 {"total_micro": worst["total_micro"], "cap_micro": worst["cap_micro"],
                  "traces": worst["traces"], "calls": worst["calls"]})
    declared = (manifest.get("budget_semantics") or {}).get("worst_case_reservation_micro")
    report.check("BUDGET recomputed worst case equals the manifest declaration",
                 declared == worst["total_micro"],
                 {"manifest": declared, "recomputed": worst["total_micro"]})

    totals = commitments(ledger)
    room = headroom(ledger, policy, manifest["allocation"], manifest["phase"])
    report.check("BUDGET committed total equals the settled total",
                 totals["total"] == sum(e["settled_micro"] or 0 for e in ledger["events"]),
                 {"committed_micro": totals["total"], "binding": room["binding"],
                  "headroom_micro": room["headroom"]})
    report.check("BUDGET the completed run stayed inside every control",
                 room["headroom"] >= 0, room["controls"])

    saved = json.loads((run_dir / "out" / "probe_summary.json").read_text(encoding="utf-8"))
    counts: dict[str, dict[str, int]] = {}
    for variant, condition in manifest["cells"]:
        counts[f"{variant}/{condition}"] = {k: 0 for k in OUTCOMES + (INVALID,)}
    for row in rows:
        counts[f"{row['variant']}/{row['condition']}"][row["outcome"]] += 1
    planned = len(probe_plan(manifest))
    recomputed = {
        "planned_traces": planned,
        "attempted_traces": len(rows),
        "not_attempted": planned - len(rows),
        "counts": counts,
        "gate": evaluate_gate(counts, manifest["feasibility_gate"]),
        "stopped": None,
        "worst_case_micro": worst["total_micro"],
    }
    differing = {key: {"saved": saved.get(key), "recomputed": value}
                 for key, value in recomputed.items() if saved.get(key) != value}
    report.check("SUMMARY recomputed summary equals the saved summary", not differing,
                 differing or "match")
    report.check("SUMMARY the saved summary is byte-unchanged",
                 sha256_file(run_dir / "out" / "probe_summary.json")
                 == FROZEN_PINS["out/probe_summary.json"])
    return {"recomputed": recomputed,
            "saved_gate_passed": (saved.get("gate") or {}).get("passed")}


# ------------------------------------------------------------------ entry point

def validate(run_dir: Path, report: Optional[Report] = None) -> dict:
    """Run every check against ``run_dir``; return the report as a dict."""
    run_dir = Path(run_dir)
    report = report or Report()
    report.check("RUN directory exists", run_dir.is_dir(), str(run_dir))
    if not run_dir.is_dir():
        return report.as_dict()

    pins = check_frozen_pins(run_dir, report)
    manifest, policy, binding_hash = check_manifest_and_binding(run_dir, report)
    ledger = check_ledger(run_dir, manifest, policy, binding_hash, report)
    rows = read_jsonl(run_dir / "out" / "probe_traces.jsonl")
    check_rows(rows, manifest, report)
    check_trace_conversion(rows, manifest, report)
    totals = check_reconciliation(run_dir, ledger, rows, manifest, report)
    summary = check_budget_and_summary(run_dir, manifest, policy, ledger, rows, report)

    result = report.as_dict()
    result.update({
        "run_dir": str(run_dir),
        "artifact_sha256": pins,
        "totals": totals,
        "summary_recomputed": summary["recomputed"],
        "saved_gate_passed": summary["saved_gate_passed"],
        "note": ("Feasibility-probe accounting validation only. Not monitor performance, "
                 "not collusion detection, not evidence for any hypothesis."),
    })
    return result


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-dir", type=Path,
                        default=ROOT / "runs" / "ext-probe-v21-sonnet46")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="new directory for the report; must not exist")
    args = parser.parse_args(argv)

    result = validate(args.run_dir)
    for check in result["checks"]:
        print(f"{'PASS' if check['ok'] else 'FAIL'}  {check['id']}")
    print(f"\n{result['n_checks'] - result['n_failed']}/{result['n_checks']} checks passed")

    if args.out_dir is not None:
        out_dir = Path(args.out_dir).resolve()
        if any(out_dir == protected or protected in out_dir.parents for protected in PROTECTED_DIRS):
            print(f"error: refusing to write under a protected directory: {out_dir}",
                  file=sys.stderr)
            return 2
        if out_dir.exists():
            print(f"error: refusing to write into an existing directory: {out_dir}",
                  file=sys.stderr)
            return 2
        out_dir.mkdir(parents=True)
        (out_dir / "validation_report.json").write_text(
            json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print(f"report -> {out_dir / 'validation_report.json'}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

