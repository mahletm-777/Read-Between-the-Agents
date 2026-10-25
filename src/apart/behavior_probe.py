"""Stage 4N behavioural feasibility probe (prompt v2.1, agent stages only).

This module prepares and, only when separately authorized, drives the Stage 4N
probe: five FREE_MSG cells, three traces each, scheduled round-robin, one parse
attempt per stage, no extraction, no labeler, no monitor arms. Its only outcome is
the joint action (sender/receiver) of each trace.

Nothing here imports ``extraction`` or ``apart.labeler``: the probe has no path to
an extractor call. The worst-case budget is computed from the real request path
(``agents.runner.run_trace`` plus ``monitors.client._input_token_bound`` plus
``Policy.worst_case_micro``) and must fit the ledger headroom before any request
is sent.

Status: a feasibility probe. NOT confirmatory, NOT a monitor comparison.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from agents.client import FakeAgentClient
from agents.prompts import PROMPT_VERSION, MAX_OUTPUT_TOKENS, VARIANTS
from agents.runner import (AGENT_TEMPERATURE, REQUEST_HASH_FORMAT, STAGE_CONTEXT,
                           run_trace, serialized_bytes)
from apart.provenance import ProvenanceLog, make_record
from monitors.client import _input_token_bound
from monitors.ledger import LedgerError, headroom, utc_now

PROBE_MANIFEST_VERSION = "4n-1.0"
PROTOCOL_VERSION = "stage4n-behavior-probe/1.0"
PROVENANCE_SCHEMA_VERSION = "agent-provenance/2"
STAGE_PARSE_ATTEMPTS = 1
OUTCOMES = ("A/A", "A/B", "B/A", "B/B")
INVALID = "invalid"

REQUIRED_FIELDS = (
    "probe_manifest_version", "run_id", "protocol_version", "design_status",
    "prompt_version", "prompt_sha256", "provenance_schema_version",
    "request_hash_format", "provider", "model", "base_url", "allocation", "phase",
    "cap_usd", "cells", "per_cell", "schedule", "seed_start", "max_traces",
    "max_calls", "max_attempts", "stage_parse_attempts", "agent_temperature",
    "agent_max_tokens", "max_output_bytes", "stage_context", "extraction",
    "labeler", "monitor_arms", "confirmatory", "outcome", "feasibility_gate",
    "stop_conditions", "created_utc", "prompt_files_sha256",
)


class ProbeError(ValueError):
    """The probe manifest is inconsistent, or the run must not start / continue."""


@dataclass(frozen=True)
class Cell:
    seed: int
    variant: str
    condition: str


# ---------------------------------------------------------------- manifest

def validate_probe_manifest(data: Any) -> dict:
    """Fail closed: every frozen Stage 4N setting must be present and exact."""
    if not isinstance(data, Mapping):
        raise ProbeError("probe manifest must be a JSON object")
    missing = [name for name in REQUIRED_FIELDS if name not in data]
    if missing:
        raise ProbeError(f"probe manifest is missing {missing}")
    if data["probe_manifest_version"] != PROBE_MANIFEST_VERSION:
        raise ProbeError(f"unsupported probe_manifest_version {data['probe_manifest_version']!r}")
    if data["protocol_version"] != PROTOCOL_VERSION:
        raise ProbeError(f"protocol_version must be {PROTOCOL_VERSION!r}")
    if data["prompt_version"] != PROMPT_VERSION:
        raise ProbeError(f"prompt_version {data['prompt_version']!r} is not the current "
                         f"prompt version {PROMPT_VERSION!r}")
    from agents.prompts import AGENT_SYSTEM_PROMPT_SHA256
    if data["prompt_sha256"] != AGENT_SYSTEM_PROMPT_SHA256:
        raise ProbeError("prompt_sha256 does not match agents.prompts.AGENT_SYSTEM_PROMPT")
    if data["provenance_schema_version"] != PROVENANCE_SCHEMA_VERSION:
        raise ProbeError("provenance_schema_version mismatch")
    if data["request_hash_format"] != REQUEST_HASH_FORMAT:
        raise ProbeError("request_hash_format mismatch")
    for flag, wanted in (("extraction", False), ("labeler", False), ("confirmatory", False)):
        if data[flag] is not wanted:
            raise ProbeError(f"{flag} must be {wanted}")
    if data["monitor_arms"] != []:
        raise ProbeError("monitor_arms must be empty: Stage 4N scores no monitor")
    if data["schedule"] != "round_robin":
        raise ProbeError("schedule must be 'round_robin'")
    for name in ("max_attempts", "stage_parse_attempts"):
        if data[name] != 1:
            raise ProbeError(f"{name} must be 1: Stage 4N performs no retries")
    if data["agent_temperature"] != AGENT_TEMPERATURE:
        raise ProbeError("agent_temperature does not match agents.runner")
    if data["agent_max_tokens"] != MAX_OUTPUT_TOKENS:
        raise ProbeError("agent_max_tokens does not match agents.prompts")
    if data["stage_context"] != {k: list(v) for k, v in STAGE_CONTEXT.items()}:
        raise ProbeError("stage_context does not match agents.runner.STAGE_CONTEXT")
    for name in ("per_cell", "seed_start", "max_traces", "max_calls", "max_output_bytes"):
        value = data[name]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ProbeError(f"{name} must be a positive integer")
    cells = data["cells"]
    if not isinstance(cells, list) or not cells:
        raise ProbeError("cells must be a non-empty list")
    seen = set()
    for cell in cells:
        if not (isinstance(cell, list) and len(cell) == 2):
            raise ProbeError(f"cell {cell!r} must be [variant, condition]")
        variant, condition = cell
        if variant not in VARIANTS or condition not in VARIANTS[variant]["conditions"]:
            raise ProbeError(f"cell {cell!r} is not a defined variant/condition")
        if tuple(cell) in seen:
            raise ProbeError(f"duplicate cell {cell!r}")
        seen.add(tuple(cell))
    plan = probe_plan(data)
    if len(plan) != data["max_traces"]:
        raise ProbeError(f"max_traces={data['max_traces']} but the plan has {len(plan)} traces")
    if data["max_calls"] != len(plan) * len(STAGE_CONTEXT):
        raise ProbeError("max_calls must equal traces x 5 agent stages (one attempt each)")
    return dict(data)


def load_probe_manifest(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProbeError(f"cannot read probe manifest {path}: {exc}") from exc
    return validate_probe_manifest(data)


def probe_plan(manifest: Mapping) -> list[Cell]:
    """Round-robin schedule: one trace of every cell per round, ``per_cell`` rounds.

    Seeds are consecutive from ``seed_start`` in schedule order, so a stop after k
    traces leaves every cell with either floor(k/n) or ceil(k/n) traces. Sender role
    alternates by seed parity, and with an odd number of cells each cell gets both
    roles across rounds.
    """
    plan: list[Cell] = []
    seed = int(manifest["seed_start"])
    for _round in range(int(manifest["per_cell"])):
        for variant, condition in manifest["cells"]:
            plan.append(Cell(seed=seed, variant=variant, condition=condition))
            seed += 1
    return plan


def check_binding(manifest: Mapping, policy) -> None:
    """Manifest, policy and ledger cap must name one model and one $ cap."""
    provider, model = policy.require_model()
    if (provider, model) != (manifest["provider"], manifest["model"]):
        raise ProbeError(f"manifest model {manifest['provider']}/{manifest['model']} does "
                         f"not match the policy {provider}/{model}")
    cap_micro = int(round(manifest["cap_usd"] * 1_000_000))
    if policy.pair_cap(manifest["allocation"], manifest["phase"]) != cap_micro:
        raise ProbeError("manifest cap_usd does not match the policy phase cap")
    if policy.allocation_caps[manifest["allocation"]] != cap_micro:
        raise ProbeError("manifest cap_usd does not match the policy allocation cap")
    if policy.backstop_micro != cap_micro:
        raise ProbeError("manifest cap_usd does not match the policy backstop")


# ---------------------------------------------------------------- worst case

def _minimal_responder(declined: bool):
    """Shortest valid reply for every stage, so the real request path runs end to end."""
    def respond(messages, _temperature, _seed, _index):
        user = messages[-1]["content"]
        if '"message"' in user:
            return json.dumps({"message": "" if declined else "x"})
        if '"plan"' in user:
            return json.dumps({"plan": "A"})
        return json.dumps({"action": "A"})
    return respond


def _call_bound(messages: list, stage: str, condition: str, max_output_bytes: int,
                delivered: Optional[str]) -> int:
    """Input-token bound for one request when every carried reply is at the cap.

    The live client reserves ``_input_token_bound(messages)``: the UTF-8 size of
    each JSON-serialized message plus 64. With every reply limited to
    ``max_output_bytes`` serialized bytes (enforced by ``run_trace``), the bound is
    the bound of the minimal request plus, per carried assistant message, the gap
    between its serialized size and the cap. In R2 the delivered message text is
    embedded as ``json.dumps(message)`` and serialized again; the inner text has at
    most ``max_output_bytes`` bytes and contains no raw control characters, so the
    second serialization at most doubles it.
    """
    bound = _input_token_bound(messages)
    for message in messages:
        if message["role"] == "assistant":
            bound += max_output_bytes - serialized_bytes(message["content"])
    if stage == "r2" and delivered is not None and condition in ("FREE_MSG",):
        inner = json.dumps(delivered, ensure_ascii=False)
        actual = serialized_bytes(inner) - 2           # inside the content string
        bound += 2 * max_output_bytes - actual
    return bound


def trace_worst_case(policy, variant: str, condition: str, max_output_bytes: int,
                     prompt_version: str = PROMPT_VERSION) -> dict:
    """Worst-case reservation (µ$) for one trace, per stage, over both S2 branches.

    Runs ``run_trace`` offline with a recording fake client, once with a delivered
    message and once with a declined one, and takes the larger total. No network.
    """
    best: Optional[dict] = None
    for declined in (False, True):
        client = FakeAgentClient(_minimal_responder(declined), model="worst-case-spy")
        result = run_trace(0, variant, condition, client, prompt_version=prompt_version,
                           max_stage_attempts=STAGE_PARSE_ATTEMPTS,
                           max_output_bytes=max_output_bytes, stop_on_failure=True)
        delivered = None if declined else (result["s2"] or {}).get("message")
        stages = {}
        for call in client.calls:
            stage = call["call_id"].split(":")[1]
            bound = _call_bound(call["messages"], stage, condition, max_output_bytes, delivered)
            stages[stage] = {"input_token_bound": bound,
                             "max_tokens": call["max_tokens"],
                             "micro": policy.worst_case_micro(bound, call["max_tokens"])}
        total = sum(s["micro"] for s in stages.values())
        if best is None or total > best["total_micro"]:
            best = {"variant": variant, "condition": condition,
                    "branch": "declined" if declined else "delivered",
                    "stages": stages, "total_micro": total, "calls": len(stages)}
    return best


def plan_worst_case(manifest: Mapping, policy) -> dict:
    """Worst-case µ$ for the exact plan: the sum of every trace's worst case."""
    per_cell = {f"{v}/{c}": trace_worst_case(policy, v, c, manifest["max_output_bytes"],
                                             manifest["prompt_version"])
                for v, c in manifest["cells"]}
    plan = probe_plan(manifest)
    total = sum(per_cell[f"{cell.variant}/{cell.condition}"]["total_micro"] for cell in plan)
    calls = sum(per_cell[f"{cell.variant}/{cell.condition}"]["calls"] for cell in plan)
    cap = int(round(manifest["cap_usd"] * 1_000_000))
    return {"per_cell": per_cell, "traces": len(plan), "calls": calls,
            "total_micro": total, "cap_micro": cap, "fits": total <= cap,
            "max_output_bytes": manifest["max_output_bytes"]}


def check_budget(manifest: Mapping, policy, ledger: Mapping) -> dict:
    """Refuse before any request unless the whole plan's worst case fits the headroom."""
    worst = plan_worst_case(manifest, policy)
    room = headroom(ledger, policy, manifest["allocation"], manifest["phase"])
    worst["headroom_micro"] = room["headroom"]
    worst["binding_control"] = room["binding"]
    if not worst["fits"] or worst["total_micro"] > room["headroom"]:
        raise ProbeError(
            f"worst-case plan cost {worst['total_micro']} µ$ exceeds the cap "
            f"{worst['cap_micro']} µ$ or the ledger headroom {room['headroom']} µ$; "
            "refusing to start"
        )
    return worst


# ---------------------------------------------------------------- outcomes

def joint_outcome(result: Mapping) -> tuple[str, Optional[str]]:
    """``"<sender>/<receiver>"`` joint action, or ``("invalid", reason)``.

    A trace is a valid behavioural outcome only if every stage it ran is ``ok`` and
    both final actions are A or B. Anything else is ``invalid`` and stays in the
    denominator; it is never read as A/A or B/B.
    """
    names = ["s1", "r1", "s2", "s3", "r2"] if result["condition"] == "FREE_MSG" else \
        ["s1", "r1", "s3", "r2"]
    for name in names:
        stage = result.get(name)
        if stage is None or stage.get("status") != "ok":
            status = None if stage is None else stage.get("status")
            return INVALID, f"{name}:{status}"
    sender, receiver = result["s3"].get("action"), result["r2"].get("action")
    if sender not in ("A", "B") or receiver not in ("A", "B"):
        return INVALID, "unreadable_action"
    return f"{sender}/{receiver}", None


def evaluate_gate(counts: Mapping[str, Mapping[str, int]], gate: Mapping) -> dict:
    """Operational smoke gate only: each listed cell needs >= 1 of its outcome."""
    checks = {}
    for cell, outcome in gate["require"].items():
        n = counts.get(cell, {}).get(outcome, 0)
        checks[f"{cell} {outcome}"] = {"observed": n, "passed": n >= 1}
    return {"passed": all(c["passed"] for c in checks.values()), "checks": checks,
            "note": gate.get("note")}


# ---------------------------------------------------------------- run

def _record_calls(manifest, store, provenance, result, trace_id) -> list[dict]:
    index = {(e["call_id"], e["attempt"]): e for e in store.load()["events"]}
    records = []
    for entry in result["calls"]:
        event = index.get((entry["call_id"], 0))
        if event is None:
            raise LedgerError(f"no ledger event for {entry['call_id']!r}")
        record = make_record(
            run_id=manifest["run_id"], trace_id=trace_id, stage=entry["stage"],
            call_id=entry["call_id"], attempt=event["attempt"],
            provider=manifest["provider"], model=manifest["model"],
            prompt_sha256=entry["prompt_sha256"],
            dispatched=event.get("dispatched_ts") is not None,
            reserved_micro=event["reserved_micro"], usage=entry.get("usage"),
            settled_micro=event.get("settled_micro"), settlement_state=event["state"],
            ts=utc_now(),
            failure_category=(entry["status"] if entry["status"] != "ok" else None),
            context_stages=entry.get("context_stages"),
            input_messages=entry.get("input_messages"),
            request_hash_format=entry.get("request_hash_format"),
            served=entry,
        )
        provenance.record(record)
        records.append(record)
    return records


def run_probe(*, manifest: Mapping, policy, store, config_hash: str, agent_client,
              provenance: ProvenanceLog) -> dict:
    """Run the frozen probe plan. Every request is reserved by the guard first.

    Stops (and reports why) on: a ledger error or cap refusal, any unknown-cost
    event, a settlement above its reservation, a served model that differs from
    the requested one (when the provider reports it), or the call limit.
    """
    manifest = validate_probe_manifest(manifest)
    check_binding(manifest, policy)
    if getattr(agent_client, "model", None) != manifest["model"]:
        raise ProbeError("agent client model does not match the manifest")
    if int(getattr(agent_client, "max_attempts", 1)) != 1:
        raise ProbeError("agent client max_attempts must be 1")
    ledger = store.load(expected_config_hash=config_hash, policy=policy)
    if any(e["run_id"] == manifest["run_id"] for e in ledger["events"]):
        raise ProbeError(f"run {manifest['run_id']!r} already has ledger events; refusing")
    budget = check_budget(manifest, policy, ledger)

    rows: list[dict] = []
    stopped: Optional[str] = None
    for cell in probe_plan(manifest):
        if len(store.load()["events"]) + len(STAGE_CONTEXT) > manifest["max_calls"]:
            stopped = f"call limit {manifest['max_calls']} reached before ext-{cell.seed:04d}"
            break
        trace_id = f"ext-{cell.seed:04d}"
        try:
            result = run_trace(cell.seed, cell.variant, cell.condition, agent_client,
                               prompt_version=manifest["prompt_version"],
                               max_stage_attempts=STAGE_PARSE_ATTEMPTS,
                               max_output_bytes=manifest["max_output_bytes"],
                               stop_on_failure=True)
        except LedgerError as exc:
            rows.append({"trace_id": trace_id, "seed": cell.seed, "variant": cell.variant,
                         "condition": cell.condition, "outcome": INVALID,
                         "invalid_reason": f"ledger_stop:{type(exc).__name__}"})
            stopped = f"ledger refused or failed in {trace_id}: {exc}"
            break
        _record_calls(manifest, store, provenance, result, trace_id)
        outcome, reason = joint_outcome(result)
        served = sorted({c["served_model"] for c in result["calls"]
                         if isinstance(c.get("served_model"), str)})
        rows.append({
            "trace_id": trace_id, "seed": cell.seed, "variant": cell.variant,
            "condition": cell.condition, "outcome": outcome, "invalid_reason": reason,
            "sender": result["sender"], "receiver": result["receiver"],
            "sender_brief": result["sender_brief"], "receiver_brief": result["receiver_brief"],
            "s1": result["s1"], "r1": result["r1"], "s2": result["s2"],
            "s3": result["s3"], "r2": result["r2"],
            "stopped_after": result["stopped_after"],
            "prompt_version": result["prompt_version"],
            "requested_model": manifest["model"], "served_models": served,
        })
        if any(m != manifest["model"] for m in served):
            stopped = f"served model {served} differs from requested {manifest['model']}"
            break
        events = [e for e in store.load()["events"] if e["run_id"] == manifest["run_id"]]
        if any(e["state"] == "unknown" for e in events):
            stopped = f"unknown-cost event after {trace_id}"
            break
        if any(e.get("settled_micro") is not None and e["settled_micro"] > e["reserved_micro"]
               for e in events):
            stopped = f"a settlement exceeded its reservation after {trace_id}"
            break

    return {"summary": summarize(manifest, rows, stopped, budget), "rows": rows}


def summarize(manifest: Mapping, rows: list[dict], stopped: Optional[str],
              budget: Optional[Mapping] = None) -> dict:
    """Per-cell outcome counts with failures kept in the denominator."""
    counts: dict[str, dict[str, int]] = {}
    for variant, condition in manifest["cells"]:
        counts[f"{variant}/{condition}"] = {k: 0 for k in OUTCOMES + (INVALID,)}
    for row in rows:
        counts[f"{row['variant']}/{row['condition']}"][row["outcome"]] += 1
    planned = len(probe_plan(manifest))
    return {
        "run_id": manifest["run_id"],
        "protocol_version": manifest["protocol_version"],
        "design_status": manifest["design_status"],
        "planned_traces": planned,
        "attempted_traces": len(rows),
        "not_attempted": planned - len(rows),
        "counts": counts,
        "gate": evaluate_gate(counts, manifest["feasibility_gate"]),
        "stopped": stopped,
        "worst_case_micro": None if budget is None else budget["total_micro"],
        "outcome_order": "sender/receiver",
    }


def write_probe_outputs(out_dir: Path, result: Mapping) -> dict:
    """Write rows and summary. Refuses an existing output directory (no clobber)."""
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise FileExistsError(f"refusing to write into an existing directory: {out_dir}")
    out_dir.mkdir(parents=True)
    with (out_dir / "probe_traces.jsonl").open("x", encoding="utf-8", newline="\n") as handle:
        for row in result["rows"]:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    with (out_dir / "probe_summary.json").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(result["summary"], indent=2, sort_keys=True) + "\n")
    return dict(result["summary"])
