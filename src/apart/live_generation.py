"""Guarded live generation for one frozen run manifest (extension_spec.md §4, §16.9).

This module is the only place a real generation run is orchestrated. It reads a
frozen manifest, refuses to run if the manifest, the spend policy and the
guarded clients disagree, and then drives:

    agent stages  ->  two guarded extractors  ->  adjudication  ->  frozen labeler

Every billable call is reserved by ``monitors.ledger`` before it is sent and is
written to the provenance log after it is settled. There is no unguarded path, no
automatic retry, no fallback model, and no way for a command-line argument to
widen the frozen scope: the trace count, call count and model all come from the
manifest.

Status of this run: a *feasibility* run. ``c_set_measured`` is false because the
monitor gate has not been run, so N is fixed by the generation cap rather than by
a measured C_set (§16.9.5). That is recorded in the manifest and must not be
reported as a confirmatory design.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from agents.prompts import PROMPT_VERSION
from agents.runner import STAGE_CONTEXT, build_trace, run_trace
from apart.provenance import ProvenanceLog, make_record
from extraction.adjudicate import adjudicate
from monitors.ledger import LedgerError, utc_now

MANIFEST_VERSION = "1.0"

REQUIRED_MANIFEST_FIELDS = (
    "manifest_version", "run_id", "design_status", "c_set_measured", "provider",
    "model", "base_url", "allocation", "phase", "variants", "per_cell",
    "max_traces", "max_calls", "max_attempts", "agent_max_tokens",
    "extractor_max_tokens", "seed_start", "cap_usd", "label_definition",
    "monitor_arms", "stop_conditions",
)


class ManifestError(ValueError):
    """The run manifest is incomplete, inconsistent, or not frozen."""


def load_manifest(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"cannot read run manifest {path}: {exc}") from exc
    return validate_manifest(data)


def validate_manifest(data: Any) -> dict:
    """Fail closed: a manifest missing any frozen field is refused outright."""
    if not isinstance(data, Mapping):
        raise ManifestError("run manifest must be a JSON object")
    missing = [name for name in REQUIRED_MANIFEST_FIELDS if name not in data]
    if missing:
        raise ManifestError(f"run manifest is missing {missing}")
    if data["manifest_version"] != MANIFEST_VERSION:
        raise ManifestError(
            f"unsupported manifest_version {data['manifest_version']!r}; "
            f"expected {MANIFEST_VERSION!r}"
        )
    variants = data["variants"]
    if not isinstance(variants, Mapping) or not variants:
        raise ManifestError("'variants' must be a non-empty object")
    for variant, conditions in variants.items():
        if not isinstance(conditions, list) or not conditions:
            raise ManifestError(f"variants.{variant} must be a non-empty list")
        for condition in conditions:
            if condition not in ("FREE_MSG", "NEUTRAL_MSG", "NO_MSG"):
                raise ManifestError(f"variants.{variant} has unknown condition {condition!r}")
    for name in ("per_cell", "max_traces", "max_calls", "max_attempts",
                 "agent_max_tokens", "extractor_max_tokens", "seed_start"):
        value = data[name]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ManifestError(f"{name} must be a positive integer")
    if data["max_attempts"] != 1:
        raise ManifestError("max_attempts must be 1: this run performs no retries")
    if not isinstance(data["monitor_arms"], list):
        raise ManifestError("monitor_arms must be a list (may be empty)")
    plan = live_plan(data)
    if len(plan) != data["max_traces"]:
        raise ManifestError(
            f"manifest says max_traces={data['max_traces']} but the frozen plan has "
            f"{len(plan)} traces"
        )
    return dict(data)


@dataclass(frozen=True)
class Cell:
    seed: int
    variant: str
    condition: str


def live_plan(manifest: Mapping) -> list[Cell]:
    """The frozen plan: variants in manifest order, seeds from ``seed_start``.

    Deterministic, so the plan can be recomputed from the manifest alone and
    compared against what was actually generated.
    """
    plan: list[Cell] = []
    seed = int(manifest["seed_start"])
    for variant, conditions in manifest["variants"].items():
        for condition in conditions:
            for _ in range(int(manifest["per_cell"])):
                plan.append(Cell(seed=seed, variant=variant, condition=condition))
                seed += 1
    return plan


def check_model_binding(manifest: Mapping, policy) -> None:
    """The manifest, the spend policy and the guard must name one model."""
    provider, model = policy.require_model()
    if (provider, model) != (manifest["provider"], manifest["model"]):
        raise ManifestError(
            f"manifest model {manifest['provider']}/{manifest['model']} does not match "
            f"the spend policy {provider}/{model}"
        )
    cap_micro = policy.pair_cap(manifest["allocation"], manifest["phase"])
    if int(round(manifest["cap_usd"] * 1_000_000)) != cap_micro:
        raise ManifestError(
            f"manifest cap_usd={manifest['cap_usd']} does not match the policy cap "
            f"{cap_micro} µ$"
        )


def message_for(result: Mapping, condition: str) -> tuple[Optional[str], str]:
    """(message text, administrative message_status) for one generated trace.

    Mirrors ``scripts/generate_extension.py``'s helper: ``delivered`` and
    ``declined`` are genuine message-stage outcomes, ``not_sent_by_design`` is
    NO_MSG, ``generation_failed`` is a failed S2. A missing message is never
    given an attempt annotation.
    """
    from agents.prompts import NEUTRAL_NOTICE

    if condition == "NO_MSG":
        return None, "not_sent_by_design"
    if condition == "NEUTRAL_MSG":
        return NEUTRAL_NOTICE, "delivered"
    s2 = result.get("s2")
    if s2 is None or s2.get("status") != "ok":
        return None, "generation_failed"
    if s2.get("message_declined"):
        return None, "declined"
    text = s2.get("message")
    if not isinstance(text, str) or not text.strip():
        return None, "declined"
    return text, "delivered"


def claims_from_list(claims: Sequence[Mapping], sender: str) -> list:
    """Claims the labeler consumes, built from adjudicated extractor output."""
    from apart.schema import Action, Claim, ClaimType

    return [
        Claim(sender=sender, target_action=Action(c["target"]),
              claim_type=ClaimType(c["type"]), message_index=0, source="extractor",
              confidence=c["confidence"])
        for c in claims
    ]


def _ledger_index(store) -> dict:
    """{(call_id, attempt): event} for everything booked so far."""
    return {(e["call_id"], e["attempt"]): e for e in store.load()["events"]}


def _require_event(index: Mapping, call_id: str):
    event = index.get((call_id, 0))
    if event is None:
        raise LedgerError(f"no ledger event for {call_id!r}; refusing to record unbooked spend")
    return event


def _record_agent_calls(manifest, store, provenance, result, trace_id) -> None:
    """Write one provenance line per agent-stage attempt, joining the ledger."""
    index = _ledger_index(store)
    for entry in result["calls"]:
        event = _require_event(index, entry["call_id"])
        failed = entry.get("status") == "api_error"
        provenance.record(make_record(
            run_id=manifest["run_id"], trace_id=trace_id, stage=entry["stage"],
            call_id=entry["call_id"], attempt=event["attempt"],
            provider=manifest["provider"], model=manifest["model"],
            prompt_sha256=entry["prompt_sha256"],
            dispatched=event.get("dispatched_ts") is not None,
            reserved_micro=event["reserved_micro"], usage=entry.get("usage"),
            settled_micro=event.get("settled_micro"), settlement_state=event["state"],
            ts=utc_now(),
            failure_category=("api_error" if failed else None),
            # Stage 4M/4N: durable evidence of which context this stage received,
            # how the request hash was computed, and what the provider served.
            context_stages=entry.get("context_stages"),
            input_messages=entry.get("input_messages"),
            request_hash_format=entry.get("request_hash_format"),
            served=entry,
        ))


def _record_extractor_call(manifest, store, provenance, call, trace_id, stage) -> None:
    """One provenance line for an extractor attempt, joining the ledger by call_id."""
    event = _require_event(_ledger_index(store), call.call_id)
    provenance.record(make_record(
        run_id=manifest["run_id"], trace_id=trace_id, stage=stage, call_id=call.call_id,
        attempt=event["attempt"], provider=manifest["provider"], model=manifest["model"],
        prompt_sha256=call.prompt_sha256,
        dispatched=event.get("dispatched_ts") is not None,
        reserved_micro=event["reserved_micro"], usage=call.usage,
        settled_micro=event.get("settled_micro"), settlement_state=event["state"],
        ts=utc_now(), failure_category=call.failure_category,
    ))


def run_live(
    *,
    manifest: Mapping,
    policy,
    store,
    config_hash: str,
    agent_client,
    extractors: Sequence,
    provenance: ProvenanceLog,
    out_dir: Path,
) -> dict:
    """Run the frozen plan end to end and return the run summary.

    ``LedgerError`` propagates: a booking or persistence failure means nothing
    more may be sent. Transport failures are recorded, never retried, and stop
    the run through the manifest's own stop conditions.
    """
    from apart.labeler import Y_H_DEFINITION, label_trajectory, resolve_label
    from extraction.adjudicate import CLAIM_RESOLVED, claim_state
    from extraction.client import STATUS_OK

    check_model_binding(manifest, policy)
    if len(extractors) != 2:
        raise ManifestError("exactly two extractors (E1, E2) are required by §6.2")
    for extractor in extractors:
        if extractor.max_tokens != manifest["extractor_max_tokens"]:
            raise ManifestError("extractor max_tokens does not match the manifest")
    if getattr(agent_client, "model", None) != manifest["model"]:
        raise ManifestError("agent client model does not match the manifest")
    if int(getattr(agent_client, "max_attempts", 1)) != manifest["max_attempts"]:
        raise ManifestError("agent client max_attempts does not match the manifest")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stops = manifest["stop_conditions"]
    max_failures = int(stops.get("max_trace_failures", 0))
    max_unknown = int(stops.get("max_unknown_reservations", 0))
    max_calls = int(manifest["max_calls"])

    traces: list = []
    labels: list = []
    label_rows: list[dict] = []
    records: list[dict] = []
    failures = 0
    stopped: Optional[str] = None

    plan = live_plan(manifest)
    for cell in plan:
        if len(store.load()["events"]) >= max_calls:
            stopped = f"call limit {max_calls} reached before trace ext-{cell.seed:04d}"
            break
        trace_id = f"ext-{cell.seed:04d}"
        result = run_trace(cell.seed, cell.variant, cell.condition, agent_client)
        _record_agent_calls(manifest, store, provenance, result, trace_id)

        failed = [name for name in ("s1", "r1", "s3", "r2")
                  if result[name]["status"] == "api_error"]
        if failed:
            failures += 1
            records.append({"trace_id": trace_id, "seed": cell.seed,
                            "variant": cell.variant, "condition": cell.condition,
                            "included": False,
                            "exclusion_reason": "agent transport failure: " + ",".join(failed)})
            if failures > max_failures:
                stopped = f"agent transport failure in {trace_id} ({','.join(failed)})"
                break
            continue

        message, message_status = message_for(result, cell.condition)

        calls: list = []
        transport_error: Optional[str] = None
        if message is not None:
            for extractor, label in ((extractors[0], "E1"), (extractors[1], "E2")):
                call_id = f"{trace_id}:{label}"
                try:
                    calls.append(extractor.call(message, call_id))
                except Exception as exc:  # transport/HTTP: recorded, never retried
                    calls.append(extractor.calls[-1])
                    transport_error = type(exc).__name__
                _record_extractor_call(manifest, store, provenance, calls[-1], trace_id, label)
                if transport_error is not None:
                    break
        if transport_error is not None:
            failures += 1
            records.append({"trace_id": trace_id, "seed": cell.seed,
                            "variant": cell.variant, "condition": cell.condition,
                            "included": False,
                            "exclusion_reason": f"extractor transport failure: {transport_error}"})
            if failures > max_failures:
                stopped = f"extractor transport failure in {trace_id}"
                break
            continue

        # §6.2: two independent extractors, adjudicated. Stage 4M: the claim set
        # carries an explicit resolution state, so a message awaiting adjudication
        # (or one whose extraction failed) is never silently read as "no claims".
        annotation_status = "not_applicable"
        adjudication: Optional[dict] = None
        if message is not None:
            e1_parsed = calls[0].parsed
            e2_parsed = calls[1].parsed
            if calls[0].status != STATUS_OK or calls[1].status != STATUS_OK:
                annotation_status = "malformed_extractor_response"
            elif e1_parsed is None or e2_parsed is None:
                annotation_status = "malformed_extractor_response"
            else:
                adjudication = adjudicate(e1_parsed, e2_parsed, message)
                annotation_status = ("needs_adjudication"
                                     if adjudication["needs_adjudication"] else "auto_accepted")

        claim_set_state = claim_state({
            "annotation_status": annotation_status,
            "needs_adjudication": annotation_status == "needs_adjudication",
        })
        claims: list = []
        if (claim_set_state == CLAIM_RESOLVED and adjudication is not None
                and adjudication.get("claims_ok")):
            claims = claims_from_list(adjudication["claims"], result["sender"])

        trace = build_trace(result, claims=claims)
        ground_truth = label_trajectory(trace)
        resolution = resolve_label(ground_truth, claim_set_state=claim_set_state)
        traces.append(trace)
        labels.append(ground_truth)
        label_rows.append({
            **ground_truth.to_dict(),
            **resolution.to_dict(),
            "claim_set_state": claim_set_state,
            "y_h_definition": Y_H_DEFINITION,
            "variant": cell.variant,
            "delivery_condition": cell.condition,
        })
        records.append({
            "trace_id": trace_id,
            "seed": cell.seed,
            "variant": cell.variant,
            "condition": cell.condition,
            "included": True,
            "message_status": message_status,
            "annotation_status": annotation_status,
            "needs_adjudication": (None if adjudication is None
                                   else bool(adjudication["needs_adjudication"])),
            "adjudication_reason": (None if adjudication is None else adjudication["reason"]),
            "attempt_type": (None if adjudication is None else adjudication["attempt_type"]),
            "coordination_attempt": (None if adjudication is None
                                     else adjudication["coordination_attempt"]),
            "extractor_status": [c.status for c in calls] if message is not None else [],
            "agent_status": trace.agent_status,
            "trajectory_class": ground_truth.trajectory_class.value,
            "safety_violation": ground_truth.safety_violation,
            "collusive_talk": ground_truth.collusive_talk,
            # Stage 4M: the resolution state travels with the record so a pending
            # or errored label is visible downstream instead of looking negative.
            "claim_set_state": claim_set_state,
            **resolution.to_dict(),
        })

        unknown = sum(1 for e in store.load()["events"] if e["state"] == "unknown")
        if unknown > max_unknown:
            stopped = f"{unknown} unknown-cost reservation(s) exceed the manifest limit"
            break

    summary = {
        "run_id": manifest["run_id"],
        "design_status": manifest["design_status"],
        "c_set_measured": bool(manifest["c_set_measured"]),
        "provider": manifest["provider"],
        "model": manifest["model"],
        "planned_traces": len(plan),
        "traces_generated": len(traces),
        "labels": len(labels),
        "excluded": sum(1 for r in records if not r["included"]),
        "failures": failures,
        "stopped": stopped,
        "provenance_records": provenance.count,
        "label_definition": manifest["label_definition"],
        "y_h_definition": Y_H_DEFINITION,
        "monitor_arms": list(manifest["monitor_arms"]),
        "class_counts": _class_counts(labels),
        # Stage 4M: unresolved labels are reported, never folded into the negatives.
        "label_state_counts": _counts(row["y_h_state"] for row in label_rows),
        "claim_state_counts": _counts(row["claim_set_state"] for row in label_rows),
        "unresolved_labels": sum(1 for row in label_rows if row["y_h_state"] != "resolved"),
        "unresolved_seeds": sorted(row["trace_id"] for row in label_rows
                                   if row["y_h_state"] != "resolved"),
        "prompt_version": PROMPT_VERSION,
        "stage_context": {k: list(v) for k, v in STAGE_CONTEXT.items()},
    }
    return {"summary": summary, "traces": traces, "labels": labels,
            "label_rows": label_rows, "records": records}


def _counts(values) -> dict:
    counts: dict = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: str(kv[0])))


def _class_counts(labels) -> dict:
    counts: dict[str, int] = {}
    for ground_truth in labels:
        key = ground_truth.trajectory_class.value
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_corpus(out_dir: Path, result: Mapping, manifest: Mapping) -> dict:
    """Write the run artifacts. Refuses to overwrite any file that already exists."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    names = ("feas_v1_traces.jsonl", "feas_v1_labels.jsonl", "feas_v1_adjudication.jsonl")
    existing = [name for name in names if (out_dir / name).exists()]
    if existing:
        raise FileExistsError(f"refusing to overwrite existing corpus files: {existing}")

    def _write(name: str, rows) -> None:
        with (out_dir / name).open("x", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False,
                                        separators=(",", ":")) + "\n")

    _write("feas_v1_traces.jsonl", [t.to_dict() for t in result["traces"]])
    _write("feas_v1_labels.jsonl", result["label_rows"])
    _write("feas_v1_adjudication.jsonl", result["records"])

    files = {name: sha256_file(out_dir / name) for name in names}
    manifest_out = {
        "run_manifest": dict(manifest),
        "summary": dict(result["summary"]),
        "files": dict(sorted(files.items())),
    }
    with (out_dir / "manifest_feas_v1.json").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(manifest_out, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return manifest_out
