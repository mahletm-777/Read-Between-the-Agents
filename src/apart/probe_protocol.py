"""Protocol identity, manifest validation and gates for the OBJ-1.0 generation-only probe.

Scope
-----
This module owns the **new** protocol:

    probe_manifest_version: "generation-probe-1.0"
    protocol_version:       "apart-generation-probe/1.0"

It deliberately does **not** modify the Stage 4N machinery in
:mod:`apart.behavior_probe`. Historical manifests keep their original validator and
their original semantics, and the historical code/prompt pins in
``runs/ext-probe-v21-sonnet46/manifest.json`` remain valid because no pinned file is
edited by this module.

Shared, imported, never redefined (single sources of truth):

* ``agents.prompts.VARIANTS`` / ``MAX_OUTPUT_TOKENS`` - the variant registry
* ``agents.runner.STAGE_CONTEXT`` / ``AGENT_TEMPERATURE`` - the stage protocol
* ``apart.objective_spec.OBJECTIVE_ID`` - the canonical OBJ-1.0 identity

Gate semantics
--------------
Under OBJ-1.0 the probe gate is an **execution-validity** gate. It never looks at
which joint action, message or apparent coordination pattern a trace produced, so a
low (or high) count of any behaviour can never be a pipeline pass/fail. Invalid
traces are counted and reported; they are never coerced into valid outcomes. The
obsolete Stage 4N ``V0 = A/A`` gate cannot be inherited: under this protocol a
legacy ``require: {cell: outcome}`` mapping is refused outright.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from agents.prompts import MAX_OUTPUT_TOKENS, VARIANTS
from agents.runner import AGENT_TEMPERATURE, STAGE_CONTEXT
from apart.behavior_probe import INVALID  # shared "invalid outcome" marker (read-only import)
from apart.objective_spec import OBJECTIVE_ID

ROOT = Path(__file__).resolve().parents[2]

GENERATION_PROBE_MANIFEST_VERSION = "generation-probe-1.0"
GENERATION_PROBE_PROTOCOL_VERSION = "apart-generation-probe/1.0"

#: Canonical objective document whose SHA-256 a manifest may pin (computed at
#: runtime - never hard-coded here, so it cannot go stale).
OBJECTIVE_DOCUMENT = "docs/objective_spec_v1_0.md"

#: The seven approved variants, in the approved order (no V4 exists).
GENERATION_PROBE_VARIANTS = ("V0", "V1", "V2", "V3", "V5", "V6", "V7")

GATE_KIND_EXECUTION_VALIDITY = "execution_validity"
STAGE_COUNT = len(STAGE_CONTEXT)

#: Seed blocks already consumed by historical artifacts and frozen corpora
#: (verified in the working tree: ``runs/*/manifest.json``, ``data/frozen/*``,
#: ``data/preflight/*``). A new probe block must be disjoint from every one of them.
AUDIT_SEED_BLOCKS: tuple[tuple[int, int, str], ...] = (
    (0, 13, "data/frozen/gate_v2_traces.jsonl"),
    (100, 139, "data/frozen/pilot_v2_traces.jsonl"),
    (900, 905, "data/preflight/preflight_v1_traces.jsonl"),
    (5000, 5035, "runs/ext-feas-v1-sonnet46 (Stage 4K)"),
    (6000, 6014, "runs/ext-probe-v21-sonnet46 (Stage 4N)"),
)


class ProtocolError(ValueError):
    """The manifest does not satisfy the generation-probe protocol."""


@dataclass(frozen=True)
class ProtocolSpec:
    manifest_version: str
    protocol_version: str
    requires_objective: bool
    gate_kind: str
    variants: tuple[str, ...]


SUPPORTED_PROTOCOLS: dict[str, ProtocolSpec] = {
    GENERATION_PROBE_MANIFEST_VERSION: ProtocolSpec(
        manifest_version=GENERATION_PROBE_MANIFEST_VERSION,
        protocol_version=GENERATION_PROBE_PROTOCOL_VERSION,
        requires_objective=True,
        gate_kind=GATE_KIND_EXECUTION_VALIDITY,
        variants=GENERATION_PROBE_VARIANTS,
    ),
}

#: The Stage 4N field set (kept explicit rather than imported so that a change to
#: the historical module cannot silently change this protocol's contract; a test
#: asserts this list is a superset of ``apart.behavior_probe.REQUIRED_FIELDS``).
GENERATION_PROBE_REQUIRED_FIELDS: tuple[str, ...] = (
    "probe_manifest_version", "run_id", "protocol_version", "design_status",
    "prompt_version", "prompt_sha256", "provenance_schema_version",
    "request_hash_format", "provider", "model", "base_url", "allocation", "phase",
    "cap_usd", "cells", "per_cell", "schedule", "seed_start", "max_traces",
    "max_calls", "max_attempts", "stage_parse_attempts", "agent_temperature",
    "agent_max_tokens", "max_output_bytes", "stage_context", "extraction",
    "labeler", "monitor_arms", "confirmatory", "outcome", "feasibility_gate",
    "stop_conditions", "created_utc", "prompt_files_sha256",
    # generation-probe-1.0 additions
    "objective_id",
)



# ------------------------------------------------------------------- objective

def objective_document_sha256(path: Optional[Path] = None) -> str:
    """SHA-256 of the canonical OBJ-1.0 document, computed from the working tree.

    Computed at call time from ``docs/objective_spec_v1_0.md`` so the value can never
    go stale in this module.
    """
    target = Path(path) if path is not None else ROOT / OBJECTIVE_DOCUMENT
    return hashlib.sha256(target.read_bytes()).hexdigest()


def validate_objective(data: Mapping, *, repo_root: Optional[Path] = None) -> str:
    """``objective_id`` must equal the canonical constant; a pinned doc hash must match.

    The identity comes from ``apart.objective_spec.OBJECTIVE_ID`` - this module never
    defines a second copy of it.
    """
    objective_id = data.get("objective_id")
    if objective_id != OBJECTIVE_ID:
        raise ProtocolError(
            f"objective_id {objective_id!r} is not the canonical objective {OBJECTIVE_ID!r}")
    pinned = data.get("objective_doc_sha256")
    if pinned is not None:
        root = Path(repo_root) if repo_root is not None else ROOT
        actual = objective_document_sha256(root / OBJECTIVE_DOCUMENT)
        if pinned != actual:
            raise ProtocolError(
                f"objective_doc_sha256 does not match the current {OBJECTIVE_DOCUMENT}")
    return objective_id


# ------------------------------------------------------------------- gates

#: A gate may never carry a cell -> outcome expectation. This is what prevents the
#: obsolete Stage 4N "V0 must produce A/A" gate from being inherited.
FORBIDDEN_GATE_KEYS = ("require", "expect", "expected", "cells", "outcomes")

_GATE_NOTE = (
    "Execution-validity gate: it checks that the plan ran and that invalid traces stay "
    "within the declared allowance. It never inspects actions, messages or apparent "
    "coordination, so behaviour counts cannot become pipeline failures."
)


def validate_execution_gate(gate: Any) -> dict:
    """Validate the execution-validity gate shape; refuse any behavioural gate."""
    if not isinstance(gate, Mapping):
        raise ProtocolError("feasibility_gate must be an object")
    kind = gate.get("kind")
    if kind != GATE_KIND_EXECUTION_VALIDITY:
        raise ProtocolError(
            f"feasibility_gate.kind must be {GATE_KIND_EXECUTION_VALIDITY!r} under this "
            f"protocol; got {kind!r}. A cell/outcome gate (e.g. the Stage 4N V0=A/A gate) "
            "is not valid here.")
    forbidden = [key for key in FORBIDDEN_GATE_KEYS if key in gate]
    if forbidden:
        raise ProtocolError(
            f"feasibility_gate may not contain {forbidden}: this protocol forbids "
            "expressing any expected action, message or coordination pattern")
    for name in ("min_attempted_traces", "max_invalid_traces"):
        value = gate.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProtocolError(f"feasibility_gate.{name} must be an integer")
    if gate["min_attempted_traces"] < 1:
        raise ProtocolError("feasibility_gate.min_attempted_traces must be >= 1")
    if gate["max_invalid_traces"] < 0:
        raise ProtocolError("feasibility_gate.max_invalid_traces must be >= 0")
    return dict(gate)


def evaluate_execution_gate(rows: Sequence[Mapping], gate: Mapping,
                            stopped: Optional[str] = None) -> dict:
    """Evaluate the execution-validity gate over probe rows.

    ``passed`` depends only on: whether the run stopped early, how many traces were
    attempted, and how many were invalid. Outcome *distributions* are reported as
    description only and never influence ``passed``.
    """
    gate = validate_execution_gate(gate)
    attempted = len(rows)
    invalid_rows = [row for row in rows if row.get("outcome") == INVALID]
    checks = {
        "no_pipeline_stop": {"observed": stopped, "passed": stopped is None},
        "attempted_traces": {"observed": attempted,
                             "required": gate["min_attempted_traces"],
                             "passed": attempted >= gate["min_attempted_traces"]},
        "invalid_traces": {"observed": len(invalid_rows),
                           "allowed": gate["max_invalid_traces"],
                           "passed": len(invalid_rows) <= gate["max_invalid_traces"]},
    }
    description: dict = {}
    for row in rows:
        key = str(row.get("outcome"))
        description[key] = description.get(key, 0) + 1
    return {
        "kind": GATE_KIND_EXECUTION_VALIDITY,
        "passed": all(check["passed"] for check in checks.values()),
        "checks": checks,
        "descriptive_outcome_counts": dict(sorted(description.items())),
        "invalid_reasons": [{"trace_id": row.get("trace_id"),
                             "reason": row.get("invalid_reason")} for row in invalid_rows],
        "note": _GATE_NOTE,
        "behavioural_criteria_used": False,
    }


# ------------------------------------------------------------- seed safeguards

#: Roots scanned for already-used seeds. Evidence, not self-declaration: a manifest
#: could understate its own range, so the check reads the artifacts themselves.
SEED_SCAN_GLOBS = ("data/**/*.jsonl", "runs/**/*.jsonl", "results/**/*.jsonl",
                   "reports/**/*.jsonl")


def _seeds_in(path: Path) -> list[int]:
    """Integer ``seed`` values in a JSONL file, streamed line by line.

    Streaming keeps memory bounded regardless of file size; unreadable files and
    unparseable lines are skipped rather than guessed at.
    """
    seeds: list[int] = []
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return seeds
    with handle:
        for line in handle:
            if '"seed"' not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, Mapping):
                continue
            seed = record.get("seed")
            if isinstance(seed, int) and not isinstance(seed, bool):
                seeds.append(seed)
    return seeds


def protected_seed_blocks(repo_root: Optional[Path] = None) -> list[tuple[int, int, str]]:
    """Seed blocks already consumed by historical artifacts, corpora and run manifests.

    Built from the working tree: every seed-bearing JSONL under :data:`SEED_SCAN_GLOBS`
    plus the declared range of every ``runs/*/manifest.json``, plus
    :data:`AUDIT_SEED_BLOCKS`. Missing files are skipped; the audit-named blocks always
    remain protected.
    """
    root = Path(repo_root) if repo_root is not None else ROOT
    blocks: list[tuple[int, int, str]] = list(AUDIT_SEED_BLOCKS)

    seen: set[Path] = set()
    for pattern in SEED_SCAN_GLOBS:
        for path in sorted(root.glob(pattern)):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            seeds = _seeds_in(path)
            if seeds:
                blocks.append((min(seeds), max(seeds), str(path.relative_to(root))))

    runs_dir = root / "runs"
    if runs_dir.is_dir():
        for manifest in sorted(runs_dir.glob("*/manifest.json")):
            try:
                data = json.loads(manifest.read_text())
            except (OSError, ValueError):
                continue
            start, per_cell = data.get("seed_start"), data.get("per_cell")
            count: Optional[int] = None
            cells = data.get("cells")
            variants = data.get("variants")
            if isinstance(cells, list) and cells and isinstance(per_cell, int):
                count = per_cell * len(cells)
            elif isinstance(variants, Mapping) and variants and isinstance(per_cell, int):
                count = per_cell * len(variants)
            elif isinstance(data.get("max_traces"), int):
                count = data["max_traces"]
            if isinstance(start, int) and isinstance(count, int) and count > 0:
                blocks.append((start, start + count - 1,
                               str(manifest.relative_to(root))))
    return sorted(set(blocks))


def validate_seed_block(seed_start: int, count: int, *,
                        protected: Optional[Iterable[tuple[int, int, str]]] = None,
                        repo_root: Optional[Path] = None) -> tuple[int, int]:
    """Refuse a seed block that overlaps any known historical/corpus block.

    A machine-checkable *block* check, not a claim of global seed uniqueness: it can
    only prove disjointness from the blocks it can see.
    """
    for name, value in (("seed_start", seed_start), ("count", count)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProtocolError(f"{name} must be an integer")
    if count < 1:
        raise ProtocolError("seed block count must be >= 1")
    block = (seed_start, seed_start + count - 1)
    known = list(protected) if protected is not None else protected_seed_blocks(repo_root)
    for low, high, source in known:
        if block[0] <= high and low <= block[1]:
            raise ProtocolError(
                f"seed block [{block[0]}, {block[1]}] overlaps {source} [{low}, {high}]; "
                "choose a disjoint block")
    return block


def validate_prompt_files(pins: Any, *, repo_root: Optional[Path] = None) -> None:
    """Re-validate declared code/prompt SHA-256 pins against the working tree."""
    if not isinstance(pins, Mapping) or not pins:
        raise ProtocolError("prompt_files_sha256 must be a non-empty object")
    if "agents/prompts.py" not in pins:
        raise ProtocolError(
            "prompt_files_sha256 must pin agents/prompts.py so the brief text is pinned")
    root = Path(repo_root) if repo_root is not None else ROOT
    for rel, digest in pins.items():
        target = root / rel
        if not target.is_file():
            raise ProtocolError(f"pinned file {rel} is missing from the working tree")
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != digest:
            raise ProtocolError(f"{rel} changed since the manifest was frozen")



# ----------------------------------------------------------- manifest contract

def validate_generation_probe_manifest(data: Any, *,
                                       repo_root: Optional[Path] = None) -> dict:
    """Validate a ``generation-probe-1.0`` manifest.

    Deliberately a *narrow* validator: it is reached only for the manifest versions in
    :data:`SUPPORTED_PROTOCOLS`. Stage 4N manifests are validated by
    ``apart.behavior_probe.validate_probe_manifest`` and never routed here, so their
    semantics cannot be reinterpreted by this module.
    """
    if not isinstance(data, Mapping):
        raise ProtocolError("manifest must be a JSON object")
    missing = [name for name in GENERATION_PROBE_REQUIRED_FIELDS if name not in data]
    if missing:
        raise ProtocolError(f"manifest is missing required fields: {missing}")

    version = data["probe_manifest_version"]
    spec = SUPPORTED_PROTOCOLS.get(version)
    if spec is None:
        raise ProtocolError(
            f"unsupported probe_manifest_version {version!r}; supported versions: "
            f"{sorted(SUPPORTED_PROTOCOLS)}")
    if data["protocol_version"] != spec.protocol_version:
        raise ProtocolError(
            f"protocol_version {data['protocol_version']!r} does not match "
            f"{spec.manifest_version!r} ({spec.protocol_version!r})")

    validate_objective(data, repo_root=repo_root)

    for flag in ("extraction", "labeler", "confirmatory"):
        if data[flag] is not False:
            raise ProtocolError(f"{flag} must be false: this probe is generation-only")
    if data["monitor_arms"] != []:
        raise ProtocolError("monitor_arms must be empty: no monitor evaluation in scope")

    if data["schedule"] != "round_robin":
        raise ProtocolError("schedule must be 'round_robin'")
    for name in ("max_attempts", "stage_parse_attempts"):
        if data[name] != 1:
            raise ProtocolError(f"{name} must be 1: no retries in this probe")

    stops = data["stop_conditions"]
    if not isinstance(stops, Mapping) or not stops:
        raise ProtocolError("stop_conditions must be a non-empty object")
    if stops.get("retries") != "none":
        raise ProtocolError("stop_conditions.retries must be 'none'")
    if stops.get("fallback_model") != "none":
        raise ProtocolError("stop_conditions.fallback_model must be 'none'")

    if data["agent_temperature"] != AGENT_TEMPERATURE:
        raise ProtocolError(f"agent_temperature must be {AGENT_TEMPERATURE}")
    if data["agent_max_tokens"] != MAX_OUTPUT_TOKENS:
        raise ProtocolError(f"agent_max_tokens must be {MAX_OUTPUT_TOKENS}")
    if data["stage_context"] != {key: list(value) for key, value in STAGE_CONTEXT.items()}:
        raise ProtocolError("stage_context does not match the implemented stage order")

    for name in ("per_cell", "seed_start", "max_traces", "max_calls", "max_output_bytes"):
        value = data[name]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ProtocolError(f"{name} must be a positive integer")

    cells = data["cells"]
    if not isinstance(cells, list):
        raise ProtocolError("cells must be a list")
    if len(cells) != len(spec.variants):
        raise ProtocolError(
            f"cells must list exactly the {len(spec.variants)} approved variants "
            f"{list(spec.variants)}")
    seen: set[tuple] = set()
    covered: list[str] = []
    for cell in cells:
        if not (isinstance(cell, list) and len(cell) == 2):
            raise ProtocolError(f"cell {cell!r} must be [variant, condition]")
        variant, condition = cell
        if variant not in spec.variants:
            raise ProtocolError(f"{variant!r} is not an approved variant {list(spec.variants)}")
        if variant not in VARIANTS:
            raise ProtocolError(f"{variant!r} is not a known variant in agents.prompts")
        if condition not in VARIANTS[variant]["conditions"]:
            raise ProtocolError(
                f"condition {condition!r} is not allowed for {variant} "
                f"({list(VARIANTS[variant]['conditions'])})")
        if (variant, condition) in seen:
            raise ProtocolError(f"duplicate cell {cell!r}")
        seen.add((variant, condition))
        covered.append(variant)
    if sorted(covered) != sorted(spec.variants):
        raise ProtocolError(
            f"cells must cover each approved variant exactly once; got {sorted(covered)}")

    if data["max_traces"] != data["per_cell"] * len(cells):
        raise ProtocolError("max_traces must equal per_cell * len(cells)")
    if data["max_calls"] != data["max_traces"] * STAGE_COUNT:
        raise ProtocolError(f"max_calls must equal max_traces * {STAGE_COUNT} stages")

    validate_prompt_files(data["prompt_files_sha256"], repo_root=repo_root)
    validate_execution_gate(data["feasibility_gate"])
    validate_seed_block(data["seed_start"], data["max_traces"], repo_root=repo_root)
    return dict(data)


def load_generation_probe_manifest(path: Any, *,
                                   repo_root: Optional[Path] = None) -> dict:
    """Read and validate a ``generation-probe-1.0`` manifest from disk."""
    return validate_generation_probe_manifest(json.loads(Path(path).read_text()),
                                              repo_root=repo_root)


__all__ = [
    "GENERATION_PROBE_MANIFEST_VERSION", "GENERATION_PROBE_PROTOCOL_VERSION",
    "GENERATION_PROBE_VARIANTS", "GENERATION_PROBE_REQUIRED_FIELDS",
    "GATE_KIND_EXECUTION_VALIDITY", "STAGE_COUNT",
    "AUDIT_SEED_BLOCKS", "SEED_SCAN_GLOBS", "OBJECTIVE_DOCUMENT", "SUPPORTED_PROTOCOLS",
    "ProtocolSpec", "ProtocolError", "objective_document_sha256", "validate_objective",
    "validate_execution_gate", "evaluate_execution_gate", "protected_seed_blocks",
    "validate_seed_block", "validate_prompt_files",
    "validate_generation_probe_manifest", "load_generation_probe_manifest",
]

