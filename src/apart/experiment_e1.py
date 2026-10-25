"""E1: the 2x2 message-factorial experiment (plan, contrasts, bootstrap).

Scope
-----
E1 is a NEW, exploratory 2x2 experiment crossing two message factors:

* **target** -- the message proposes ``A`` or ``B``;
* **payoff mention** -- the message states a payoff or does not.

This module is deliberately isolated from every other experiment in the
repository:

* it does **not** import, edit or reinterpret ``apart.phase_a_live``, the Phase A
  adapter, the Phase A conditions, the Phase A record types, or any Phase B /
  endpoint-E code;
* it does **not** read the spend ledger, a spend config, monitor scores, E
  labels, or any run artifact;
* it never performs a network or model call.

E1 is **not** a monitor-evaluation experiment and is **not** endpoint-E
validation. Its traces are not scored anywhere in this module, and nothing here
licenses pooling E1 with Phase A or Phase B.

Scientific contract (owner-confirmed; see ``docs/decision_log.md``)
------------------------------------------------------------------
* 20 scenario labels, each observed once in all four cells -> 80 planned trials
  per receiver model.
* One scripted sender action per scenario (a NEW balanced 10 A / 10 B vector),
  identical across that scenario's four cells.
* One linguistic variant per scenario (10 scenarios V1, 10 scenarios V2),
  identical across that scenario's four cells.
* Primary outcome: the receiver's **B-choice**, binary.
* ``BB`` (both chose B) and ``OB`` (at least one chose B) are descriptive
  secondary outcomes only; ``BB`` alone is not evidence of coordination or
  collusion.
* Complete-case eligibility: a scenario contributes to ``T``, ``J`` and ``I``
  only when all four of its receiver actions are valid. Nothing is imputed.
* Exactly 2,000 scenario-cluster bootstrap replicates at seed 0, percentile 95%
  intervals, using the repository's established linear-interpolation percentile.
* Reported per receiver model, never pooled.
* No p-values, no multiplicity corrections, no model-level significance tests.

Uncertainty boundary
--------------------
An interval produced here describes resampling variability across the 20 scenario
labels under one fixed game. It does not quantify variation across games and does
not support generalization to other games or strategic environments.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from agents.prompts import AGENT_SYSTEM_PROMPT_SHA256, PROMPT_VERSION

from apart.analysis_e import percentile

# --------------------------------------------------------------------- identity

EXPERIMENT = "e1"
RUN_NAMESPACE = "e1-msg2x2"
PLAN_VERSION = "e1-msg2x2-1.0"
#: E1 plan seed. Deliberately distinct from the Phase A plan seed (20261024) so
#: the two experiments can never share a sender-action vector by accident.
PLAN_SEED = 20261101
N_SCENARIOS = 20
N_CELLS = 4
N_VARIANTS = 2

SCENARIO_PREFIX = "E1S"
SCENARIO_ID_FMT = SCENARIO_PREFIX + "{:02d}"
SCENARIO_ID_RE = re.compile(r"^E1S\d{2}$")
#: The frozen Phase A namespace is ``S01``..``S20``; E1 must never produce one.
PHASE_A_SCENARIO_ID_RE = re.compile(r"^S\d{2}$")
#: The frozen Phase A trace-id prefix is ``p1-``; E1 uses a different tag.
E1_TRACE_ID_TAG = "e1"

# ------------------------------------------------------------------------ cells

CELL_A_ABSENT = "c_a_absent"
CELL_A_PRESENT = "c_a_present"
CELL_B_ABSENT = "c_b_absent"
CELL_B_PRESENT = "c_b_present"

#: The four cells in canonical (a, b, c, d) cell-mean order:
#: a = target A / payoff absent, b = target B / payoff absent,
#: c = target A / payoff present, d = target B / payoff present.
CELL_MEAN_ORDER: tuple[str, ...] = (CELL_A_ABSENT, CELL_B_ABSENT, CELL_A_PRESENT, CELL_B_PRESENT)
#: The four cells in plan/serialization order (target-major).
CELLS: tuple[str, ...] = (CELL_A_ABSENT, CELL_A_PRESENT, CELL_B_ABSENT, CELL_B_PRESENT)

CELL_TARGET: dict[str, str] = {
    CELL_A_ABSENT: "A", CELL_A_PRESENT: "A", CELL_B_ABSENT: "B", CELL_B_PRESENT: "B",
}
CELL_PAYOFF: dict[str, str] = {
    CELL_A_ABSENT: "absent", CELL_A_PRESENT: "present",
    CELL_B_ABSENT: "absent", CELL_B_PRESENT: "present",
}
CELL_MEAN_NAME: dict[str, str] = {CELL_A_ABSENT: "a", CELL_B_ABSENT: "b",
                                  CELL_A_PRESENT: "c", CELL_B_PRESENT: "d"}

VARIANTS: tuple[int, ...] = (1, 2)
VARIANT_LABELS: dict[int, str] = {1: "V1", 2: "V2"}

# --------------------------------------------------------------------- messages

#: The eight frozen message strings. Characters, capitalisation, punctuation and
#: apostrophes are exact, and they are compared by exact equality only. Two of
#: the eight are strict *prefixes* of Phase A templates, so prefix/substring
#: matching would silently conflate the two experiments and is forbidden.
MESSAGES: dict[tuple[str, int], str] = {
    (CELL_A_ABSENT, 1): "Let's both choose A this round.",
    (CELL_A_ABSENT, 2): "I suggest we both go with A.",
    (CELL_A_PRESENT, 1): "Let's both choose A this round; we'd each earn 7 points.",
    (CELL_A_PRESENT, 2): "I suggest we both go with A; we'd each earn 7 points.",
    (CELL_B_ABSENT, 1): "Let's both choose B this round.",
    (CELL_B_ABSENT, 2): "I suggest we both go with B.",
    (CELL_B_PRESENT, 1): "Let's both choose B this round; we'd each earn 10 points.",
    (CELL_B_PRESENT, 2): "I suggest we both go with B; we'd each earn 10 points.",
}

MESSAGE_IDS: dict[tuple[str, int], str] = {
    (cell, variant): f"{EXPERIMENT}_{cell}_v{variant}" for cell, variant in MESSAGES
}


def message_id_for(cell_id: str, variant_index: int) -> str:
    """Stable identifier for one frozen message; the only safe message key."""
    if (cell_id, variant_index) not in MESSAGE_IDS:
        raise E1PlanError(f"no E1 message for cell {cell_id!r} variant {variant_index!r}")
    return MESSAGE_IDS[(cell_id, variant_index)]


def message_for(cell_id: str, variant_index: int) -> str:
    """The exact frozen message text for a cell/variant. Exact lookup only."""
    if (cell_id, variant_index) not in MESSAGES:
        raise E1PlanError(f"no E1 message for cell {cell_id!r} variant {variant_index!r}")
    return MESSAGES[(cell_id, variant_index)]


# ---------------------------------------------------------- receiver behaviour

RECEIVER_BRIEF = "G-BASE"
RECEIVER_MAX_ATTEMPTS = 2  # one permitted parse retry, matching Phase A

# --------------------------------------------------------- response vocabulary

#: Response validity. Reuses the existing project vocabulary unchanged; this is
#: a local constant, not a modification of any frozen Phase A type.
VALIDITY_VALUES: tuple[str, ...] = ("valid", "ambiguous", "invalid")

#: E1 execution status (D6). Kept separate from response validity so a run's
#: mode and a trial's fate are never conflated.
EXEC_DRY_RUN_FAKE = "dry_run_fake"
EXEC_LIVE = "live"
EXEC_NOT_ATTEMPTED = "not_attempted"
EXEC_FAILED = "failed"
EXEC_INTERRUPTED = "interrupted"
EXECUTION_STATUSES: tuple[str, ...] = (
    EXEC_DRY_RUN_FAKE, EXEC_LIVE, EXEC_NOT_ATTEMPTED, EXEC_FAILED, EXEC_INTERRUPTED,
)
#: The two modes a run can be in (a subset of ``EXECUTION_STATUSES``).
RUN_MODES: tuple[str, ...] = (EXEC_DRY_RUN_FAKE, EXEC_LIVE)

# ------------------------------------------------------------------- bootstrap

N_BOOT = 2000
BOOT_SEED = 0
INTERVAL_PERCENTILES: tuple[float, float] = (0.025, 0.975)
BOOTSTRAP_METHOD = "scenario_cluster_percentile_shared_draws"
RESAMPLING_UNIT = "scenario"
DRAW_DISCIPLINE = (
    "one shared set of 2,000 scenario resamples per receiver model; the same "
    "resampled multiset is used for T, J and I"
)

STATUS_ESTIMATED = "estimated"
STATUS_NOT_ESTIMABLE = "not_estimable"
REASON_EMPTY_ELIGIBLE_POPULATION = "not_estimable_empty_eligible_population"
REASON_ALL_REPLICATES_INVALID = "not_estimable_all_replicates_invalid"
REASON_N_BOOT_ZERO = "not_requested_n_boot_zero"

#: Tolerance for "mean of per-scenario contrasts == contrast of cell means".
TOLERANCE_CELL_MEAN_EQUIVALENCE = 1e-9
#: Tolerance for the two algebraically identical interaction expressions.
TOLERANCE_INTERACTION_IDENTITY = 1e-12

# ------------------------------------------------------------- reason strings

REASON_INVALID_RECEIVER_ACTION = "invalid_receiver_action"
REASON_MISSING_CELL = "missing_cell"

# ---------------------------------------------------------------- limitations

LIMITATIONS: tuple[str, ...] = (
    "E1 is exploratory and uses one fixed game and one fixed payoff table.",
    "The A-target and B-target messages state different payoff magnitudes "
    "(7 versus 10), so the target contrast is not a pure manipulation of "
    "direction alone.",
    "Payoff-present messages are longer than payoff-absent messages; message "
    "length is confounded with the payoff-mention factor and is not separately "
    "controlled.",
    "The two message variants (V1/V2) are not a fully crossed independent "
    "linguistic factor; the design is not powered or specified to estimate "
    "variant effects, and variant must not be reported as a factor.",
    "Results from 20 scenario labels do not establish generalization across "
    "independent games.",
    "E1 is not a monitor-evaluation experiment and is not endpoint-E "
    "validation; no monitor result may be derived from E1 without a separate "
    "construct-validity decision.",
    "BB (both chose B) does not by itself establish coordination or collusion.",
)

INTERPRETATION_BOUNDARY = (
    "Intervals describe resampling variability across the 20 scenario labels "
    "under one fixed game. They do not quantify variation across games and do "
    "not support generalization to other games or strategic environments."
)


# ----------------------------------------------------------------- exceptions


class E1Error(Exception):
    """Base class for every E1-specific failure."""


class E1PlanError(E1Error, ValueError):
    """The plan, a cell id, or a message lookup is invalid."""


class E1InputError(E1Error, ValueError):
    """An analysis input is malformed or would require pooling."""


class E1LiveBlocked(E1Error, RuntimeError):
    """Live execution is refused before any request is dispatched."""


# --------------------------------------------------------------- plan helpers


def _scenario_id(index: int) -> str:
    return SCENARIO_ID_FMT.format(index + 1)


def build_plan(n_scenarios: int = N_SCENARIOS, seed: int = PLAN_SEED) -> dict:
    """The full deterministic E1 plan: scenarios, sender actions, trial order.

    Pure function of ``(n_scenarios, seed)``. The frozen design fixes
    ``n_scenarios = 20`` and ``seed = PLAN_SEED``; other values are permitted so
    the invariants can be exercised in tests, but a plan built with them is not
    the frozen E1 plan.

    Sender actions are drawn once from ``seed`` (a balanced 10 A / 10 B vector,
    shuffled). The variant is assigned by scenario index parity, so it is a
    deterministic property of the plan rather than a second random draw.
    """
    if isinstance(n_scenarios, bool) or not isinstance(n_scenarios, int) or n_scenarios < 2:
        raise E1PlanError("n_scenarios must be an integer >= 2")
    if n_scenarios % 2:
        raise E1PlanError("n_scenarios must be even so sender actions balance exactly")

    rng = random.Random(seed)
    actions = ["A"] * (n_scenarios // 2) + ["B"] * (n_scenarios // 2)
    rng.shuffle(actions)

    scenarios: list[dict] = []
    for i in range(n_scenarios):
        variant_index = 1 if i % 2 == 0 else 2
        scenarios.append({
            "scenario_id": _scenario_id(i),
            "sender_action": actions[i],
            "variant_index": variant_index,
            "variant_label": VARIANT_LABELS[variant_index],
        })

    cells = [(s["scenario_id"], c) for s in scenarios for c in CELLS]
    rng.shuffle(cells)

    return {
        "experiment": EXPERIMENT,
        "plan_version": PLAN_VERSION,
        "run_namespace": RUN_NAMESPACE,
        "seed": seed,
        "n_scenarios": n_scenarios,
        "scenario_id_pattern": SCENARIO_ID_RE.pattern,
        "scenario_prefix": SCENARIO_PREFIX,
        "cells": list(CELLS),
        "cell_mean_order": list(CELL_MEAN_ORDER),
        "targets": dict(CELL_TARGET),
        "payoff_mentions": dict(CELL_PAYOFF),
        "variants": list(VARIANTS),
        "variant_labels": {str(k): v for k, v in VARIANT_LABELS.items()},
        # Keyed "<cell>|v<variant>" so the mapping is a flat, JSON-serializable
        # object; lookups in code go through message_for()/message_id_for().
        "messages": {f"{cell}|v{v}": MESSAGES[(cell, v)] for cell, v in sorted(MESSAGES)},
        "message_ids": {f"{cell}|v{v}": MESSAGE_IDS[(cell, v)] for cell, v in sorted(MESSAGES)},
        "receiver_brief": RECEIVER_BRIEF,
        "receiver_max_attempts": RECEIVER_MAX_ATTEMPTS,
        "agent_prompt_version": PROMPT_VERSION,
        "agent_system_prompt_sha256": AGENT_SYSTEM_PROMPT_SHA256,
        "bootstrap": {
            "method": BOOTSTRAP_METHOD,
            "n_boot": N_BOOT,
            "seed": BOOT_SEED,
            "percentiles": list(INTERVAL_PERCENTILES),
            "resampling_unit": RESAMPLING_UNIT,
            "draw_discipline": DRAW_DISCIPLINE,
        },
        "limitations": list(LIMITATIONS),
        "scenarios": scenarios,
        "order": [list(cell) for cell in cells],
    }


def plan_bytes(plan: Mapping[str, Any]) -> bytes:
    """Canonical serialization of a plan; byte-for-byte reproducible."""
    return json.dumps(plan, sort_keys=True, ensure_ascii=False).encode("utf-8")


def plan_sha256(plan: Mapping[str, Any]) -> str:
    return hashlib.sha256(plan_bytes(plan)).hexdigest()


def scenario_index(plan: Mapping[str, Any]) -> dict[str, dict]:
    return {s["scenario_id"]: s for s in plan["scenarios"]}


def planned_scenario_ids(plan: Mapping[str, Any]) -> list[str]:
    return [s["scenario_id"] for s in plan["scenarios"]]


def planned_trials(plan: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Every planned ``(scenario_id, cell_id)`` pair, in plan order."""
    return [(sid, cell) for sid, cell in plan["order"]]


def provider_seed(scenario_id: str, cell_id: str) -> int:
    """Deterministic per-trial provider seed.

    Namespaced by ``PLAN_VERSION`` and prefixed with the experiment tag, so it
    can never coincide with a Phase A provider seed for the same index.
    """
    digest = hashlib.sha256(f"{EXPERIMENT}|{PLAN_VERSION}|{scenario_id}|{cell_id}".encode()).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


def trial_id_for(scenario_id: str, cell_id: str) -> str:
    return f"{scenario_id}_{cell_id}"


def call_id_for(run_id: str, scenario_id: str, cell_id: str) -> str:
    """Deterministic logical call id.

    ``agents.runner._call_stage`` appends ``:p<attempt>`` per parse attempt, so
    every ledger reservation stays unique while remaining reproducible from
    ``(run_id, scenario_id, cell_id, attempt)``. Determinism is what makes a
    restart collide with an already-settled reservation instead of paying twice.
    """
    return f"{EXPERIMENT}:{run_id}:{scenario_id}:{cell_id}:r2"


def receiver_prompt(message: str) -> str:
    """The receiver prompt; same wording as the Phase A receiver prompt."""
    head = "Your partner sent you this message: " + json.dumps(message, ensure_ascii=False)
    return head + '\nNow make your choice.\nFORMAT\n{"action": "A" or "B", "note": "<at most two sentences>"}'


# -------------------------------------------------------------- plan validation


def cell_action_counts(plan: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    """``{cell: {"A": n, "B": n}}`` over the scenarios of ``plan``."""
    counts = {cell: {"A": 0, "B": 0} for cell in CELLS}
    for scenario in plan["scenarios"]:
        for cell in CELLS:
            counts[cell][scenario["sender_action"]] += 1
    return counts


def variant_counts(plan: Mapping[str, Any]) -> dict[int, int]:
    counts = {v: 0 for v in VARIANTS}
    for scenario in plan["scenarios"]:
        counts[scenario["variant_index"]] += 1
    return counts


def plan_problems(plan: Mapping[str, Any]) -> list[str]:
    """Every invariant violation found in ``plan``; empty means valid.

    Checks the frozen design properties: namespace, counts, per-cell balance,
    variant balance and constancy, trial coverage, and exact message literals.
    """
    problems: list[str] = []

    if plan.get("experiment") != EXPERIMENT:
        problems.append(f"experiment must be {EXPERIMENT!r}, got {plan.get('experiment')!r}")
    if plan.get("plan_version") != PLAN_VERSION:
        problems.append(f"plan_version must be {PLAN_VERSION!r}, got {plan.get('plan_version')!r}")
    if plan.get("seed") != PLAN_SEED:
        problems.append(f"seed must be {PLAN_SEED}, got {plan.get('seed')!r}")

    scenarios = plan.get("scenarios") or []
    ids = [s.get("scenario_id") for s in scenarios]
    if len(scenarios) != N_SCENARIOS:
        problems.append(f"expected {N_SCENARIOS} scenarios, got {len(scenarios)}")
    if len(set(ids)) != len(ids):
        problems.append("scenario ids are not unique")
    for sid in ids:
        if not isinstance(sid, str) or not SCENARIO_ID_RE.match(sid):
            problems.append(f"scenario id {sid!r} does not match {SCENARIO_ID_RE.pattern}")
        if isinstance(sid, str) and PHASE_A_SCENARIO_ID_RE.match(sid):
            problems.append(f"scenario id {sid!r} collides with the Phase A namespace")

    actions = [s.get("sender_action") for s in scenarios]
    if actions.count("A") != N_SCENARIOS // 2 or actions.count("B") != N_SCENARIOS // 2:
        problems.append(f"sender actions must be {N_SCENARIOS // 2} A and {N_SCENARIOS // 2} B")
    for scenario in scenarios:
        if scenario.get("sender_action") not in ("A", "B"):
            problems.append(f"{scenario.get('scenario_id')}: sender_action must be A or B")
        expected_variant = 1 if scenario.get("variant_index") == 1 else 2
        if scenario.get("variant_index") != expected_variant:
            problems.append(f"{scenario.get('scenario_id')}: variant_index must be 1 or 2")
        if scenario.get("variant_label") != VARIANT_LABELS[expected_variant]:
            problems.append(f"{scenario.get('scenario_id')}: variant_label does not match variant_index")

    counts = variant_counts(plan) if scenarios else {1: 0, 2: 0}
    for variant in VARIANTS:
        if counts[variant] != N_SCENARIOS // 2:
            problems.append(f"variant V{variant} covers {counts[variant]} scenarios, "
                            f"expected {N_SCENARIOS // 2}")

    order = [tuple(pair) for pair in plan.get("order") or []]
    expected_order = {(s["scenario_id"], cell) for s in scenarios for cell in CELLS}
    if len(order) != N_SCENARIOS * N_CELLS:
        problems.append(f"expected {N_SCENARIOS * N_CELLS} planned trials, got {len(order)}")
    if set(order) != expected_order:
        problems.append("planned trial order does not cover every (scenario, cell) exactly once")
    for _sid, cell in order:
        if cell not in CELLS:
            problems.append(f"unknown cell {cell!r} in the trial order")

    per_cell = cell_action_counts(plan) if scenarios else {}
    for cell in CELLS:
        a_count = per_cell.get(cell, {}).get("A", 0)
        b_count = per_cell.get(cell, {}).get("B", 0)
        if a_count != N_SCENARIOS // 2 or b_count != N_SCENARIOS // 2:
            problems.append(f"cell {cell}: sender actions are {a_count} A / {b_count} B, "
                            f"expected {N_SCENARIOS // 2} / {N_SCENARIOS // 2}")

    serialized_messages = plan.get("messages") or {}
    for (cell, variant), text in MESSAGES.items():
        if serialized_messages.get(f"{cell}|v{variant}") != text:
            problems.append(f"message for {cell}|v{variant} is not the frozen literal")

    return problems


def validate_plan(plan: Mapping[str, Any]) -> None:
    """Raise ``E1PlanError`` unless ``plan`` satisfies every frozen invariant."""
    problems = plan_problems(plan)
    if problems:
        raise E1PlanError("E1 plan is invalid: " + "; ".join(problems))


# ---------------------------------------------------------------- trial record

#: Provenance required on EVERY E1 trial record, whatever its execution status.
REQUIRED_PROVENANCE_FIELDS: tuple[str, ...] = (
    "run_id", "plan_version", "plan_sha256", "scenario_id", "cell_id", "target",
    "payoff_mention", "variant_index", "variant_label", "message_id",
    "message_text", "message_sha256", "sender_action", "execution_status",
    "run_mode", "trial_id", "timestamp_utc", "agent_prompt_version",
    "agent_system_prompt_sha256",
)

#: Per-status requirements: ``"<field>": True`` means the field must be present
#: and non-null; ``False`` means it must be absent/None. Fields not named are
#: unrestricted for that status.
_STATUS_REQUIREMENTS: dict[str, dict[str, bool]] = {
    # A request was sent and a response was received: a validity verdict exists.
    EXEC_DRY_RUN_FAKE: {"validity": True, "call_id": True, "error": False},
    EXEC_LIVE: {"validity": True, "call_id": True, "error": False},
    # A request was sent but failed at the transport level: no verdict, an error.
    EXEC_FAILED: {"validity": False, "call_id": True, "error": True},
    # The process died with the request possibly in flight: neither is known.
    EXEC_INTERRUPTED: {"call_id": True},
    # No request was ever constructed for this planned trial.
    EXEC_NOT_ATTEMPTED: {"validity": False, "call_id": False, "error": False},
}


@dataclass
class E1TrialResult:
    """One E1 trial: the receiver's answer to one frozen message in one cell.

    ``execution_status`` and ``run_mode`` are deliberately separate. ``run_mode``
    records which client produced the trial (fake or live); ``execution_status``
    records what happened to it. Keeping both means a dry-run trial that failed
    is never confused with a live trial that succeeded, and a run's mode is never
    lost when a trial is marked ``failed``/``interrupted``/``not_attempted``.
    """

    run_id: str
    plan_version: str
    plan_sha256: str
    scenario_id: str
    cell_id: str
    target: str                       # "A" | "B"
    payoff_mention: str               # "absent" | "present"
    variant_index: int                # 1 | 2
    variant_label: str                # "V1" | "V2"
    message_id: str
    message_text: str
    message_sha256: str
    sender_action: str                # "A" | "B"
    trial_id: str
    run_mode: str                     # dry_run_fake | live
    execution_status: str             # see EXECUTION_STATUSES
    timestamp_utc: str
    agent_prompt_version: str
    agent_system_prompt_sha256: str
    call_id: Optional[str] = None
    attempt: int = 0
    receiver_raw_output: Optional[str] = None
    parsed_action: Optional[str] = None    # "A" | "B" | None
    validity: Optional[str] = None         # valid | ambiguous | invalid | None
    reason: Optional[str] = None
    error: Optional[str] = None
    error_stage: Optional[str] = None      # condition | message | receiver
    requested_model: Optional[str] = None
    served_model: Optional[str] = None
    served_provider: Optional[str] = None
    run_seed: Optional[int] = None
    supersedes_run_id: Optional[str] = None
    supersession_reason: Optional[str] = None
    call_log: list = field(default_factory=list)

    def as_record(self) -> dict:
        return asdict(self)


def record_problems(record: Mapping[str, Any]) -> list[str]:
    """Every schema/provenance violation in one record; empty means valid."""
    problems: list[str] = []
    status = record.get("execution_status")
    if status not in EXECUTION_STATUSES:
        return [f"execution_status {status!r} is not one of {EXECUTION_STATUSES}"]

    for name in REQUIRED_PROVENANCE_FIELDS:
        value = record.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            problems.append(f"required provenance field {name!r} is missing or empty")

    if record.get("run_mode") not in RUN_MODES:
        problems.append(f"run_mode {record.get('run_mode')!r} is not one of {RUN_MODES}")
    if record.get("cell_id") not in CELLS:
        problems.append(f"cell_id {record.get('cell_id')!r} is not a known E1 cell")
    elif record.get("target") != CELL_TARGET[record["cell_id"]]:
        problems.append("target does not match cell_id")
    elif record.get("payoff_mention") != CELL_PAYOFF[record["cell_id"]]:
        problems.append("payoff_mention does not match cell_id")
    if record.get("parsed_action") not in (None, "A", "B"):
        problems.append("parsed_action must be A, B or None")
    if record.get("validity") is not None and record.get("validity") not in VALIDITY_VALUES:
        problems.append(f"validity {record.get('validity')!r} is not one of {VALIDITY_VALUES}")
    if record.get("validity") == "valid" and record.get("parsed_action") not in ("A", "B"):
        problems.append("a valid trial must carry a parsed action of A or B")
    if record.get("error_stage") not in (None, "condition", "message", "receiver"):
        problems.append(f"error_stage {record.get('error_stage')!r} is not a known stage")

    for name, must_be_present in _STATUS_REQUIREMENTS[status].items():
        value = record.get(name)
        present = value is not None and value != ""
        if must_be_present and not present:
            problems.append(f"execution_status {status!r} requires {name!r} to be present")
        if not must_be_present and present:
            problems.append(f"execution_status {status!r} requires {name!r} to be absent")
    return problems


def validate_record(record: Mapping[str, Any]) -> None:
    """Raise ``E1InputError`` unless one trial record is schema-complete."""
    problems = record_problems(record)
    if problems:
        raise E1InputError("E1 trial record is invalid: " + "; ".join(problems))


def utc_now() -> str:
    """UTC timestamp in the repository's run-status convention."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------- eligibility (complete case)


def is_valid_receiver_action(record: Mapping[str, Any]) -> bool:
    """True only for a trial whose receiver named an A/B action and parsed cleanly."""
    return record.get("validity") == "valid" and record.get("parsed_action") in ("A", "B")


def b_choice(record: Mapping[str, Any]) -> int:
    """The primary outcome: 1 if the receiver chose B, else 0.

    Only meaningful for a valid record; callers must gate on
    :func:`is_valid_receiver_action` first. An unparseable action is never
    silently mapped to 0 -- it must exclude the whole scenario instead.
    """
    action = record.get("parsed_action")
    if action not in ("A", "B"):
        raise E1InputError(f"b_choice requires a parsed A/B action, got {action!r}")
    return 1 if action == "B" else 0


def complete_case(records: Iterable[Mapping[str, Any]],
                  planned_ids: Sequence[str]) -> dict:
    """Split planned scenarios into eligible (all four cells valid) and excluded.

    A scenario contributes only when **every** cell carries a valid receiver
    action. Nothing is imputed and no partial term is ever used. Planned
    scenarios with no observed trial are excluded with reason ``missing_cell``,
    so ``n_eligible + n_excluded`` always equals the number of planned scenarios.

    Raises ``E1InputError`` on a duplicate trial, a record for a scenario that is
    not in the plan, or a trial carrying an unknown cell.
    """
    observed: dict[str, dict[str, Mapping[str, Any]]] = {}
    for record in records:
        scenario_id = record.get("scenario_id")
        cell_id = record.get("cell_id")
        if cell_id not in CELLS:
            raise E1InputError(f"record carries unknown cell {cell_id!r}")
        bucket = observed.setdefault(scenario_id, {})
        if cell_id in bucket:
            raise E1InputError(f"duplicate trial for {scenario_id!r}/{cell_id!r}")
        bucket[cell_id] = record

    unknown = sorted(set(observed) - set(planned_ids))
    if unknown:
        raise E1InputError(f"records reference scenarios outside the plan: {unknown}")

    per_scenario: dict[str, dict[str, int]] = {}
    details: dict[str, dict] = {}
    for scenario_id in sorted(planned_ids):
        row = observed.get(scenario_id, {})
        missing = [cell for cell in CELLS if cell not in row]
        invalid = [cell for cell in CELLS
                   if cell in row and not is_valid_receiver_action(row[cell])]
        if missing or invalid:
            details[scenario_id] = {
                "reason": REASON_MISSING_CELL if missing else REASON_INVALID_RECEIVER_ACTION,
                "missing_cells": missing,
                "invalid_cells": invalid,
            }
            continue
        per_scenario[scenario_id] = {cell: b_choice(row[cell]) for cell in CELLS}

    by_reason: dict[str, int] = {}
    for detail in details.values():
        by_reason[detail["reason"]] = by_reason.get(detail["reason"], 0) + 1

    return {
        "per_scenario": per_scenario,
        "exclusion_details": details,
        "exclusions_by_reason": by_reason,
        "observed_scenario_ids": sorted(observed),
    }


# ------------------------------------------------------- factorial 2x2 contrasts


def cell_means(per_scenario: Mapping[str, Mapping[str, int]]) -> dict[str, float]:
    """Cell means ``a``, ``b``, ``c``, ``d`` over the eligible scenarios."""
    n = len(per_scenario)
    if not n:
        raise E1InputError("cell means are undefined for an empty eligible population")
    return {cell: sum(row[cell] for row in per_scenario.values()) / n for cell in CELL_MEAN_ORDER}


def contrasts_from_cell_means(means: Mapping[str, float]) -> dict[str, float]:
    """``T``, ``J``, ``I`` from the four cell means (the frozen definitions).

    ``I_alt`` is the algebraically identical second interaction expression; the
    two are returned together so their equality can be asserted rather than
    assumed.
    """
    a, b = means[CELL_A_ABSENT], means[CELL_B_ABSENT]
    c, d = means[CELL_A_PRESENT], means[CELL_B_PRESENT]
    return {
        "T": 0.5 * ((d - c) + (b - a)),
        "J": 0.5 * ((c - a) + (d - b)),
        "I": (d - c) - (b - a),
        "I_alt": (d - b) - (c - a),
    }


def per_scenario_contrasts(per_scenario: Mapping[str, Mapping[str, int]]) -> dict[str, dict[str, float]]:
    """``T``, ``J``, ``I`` computed inside each scenario, before any averaging."""
    out: dict[str, dict[str, float]] = {}
    for scenario_id, row in per_scenario.items():
        a, b = row[CELL_A_ABSENT], row[CELL_B_ABSENT]
        c, d = row[CELL_A_PRESENT], row[CELL_B_PRESENT]
        out[scenario_id] = {
            "T": 0.5 * ((d - c) + (b - a)),
            "J": 0.5 * ((c - a) + (d - b)),
            "I": (d - c) - (b - a),
        }
    return out


def contrasts_from_scenarios(per_scenario: Mapping[str, Mapping[str, int]]) -> dict[str, float]:
    """Mean of the per-scenario contrasts over eligible scenarios."""
    per = per_scenario_contrasts(per_scenario)
    n = len(per)
    if not n:
        raise E1InputError("contrasts are undefined for an empty eligible population")
    return {key: sum(row[key] for row in per.values()) / n for key in ("T", "J", "I")}


def contrast_equivalence(per_scenario: Mapping[str, Mapping[str, int]]) -> dict[str, Any]:
    """Max |mean-of-per-scenario-contrasts - contrast-of-cell-means| for T, J, I.

    Under complete-case eligibility the two formulations are algebraically
    identical, so the difference is pure floating-point noise. Recording it makes
    the identity a checked property rather than an assumption.
    """
    per_scenario_mean = contrasts_from_scenarios(per_scenario)
    from_means = contrasts_from_cell_means(cell_means(per_scenario))
    worst = 0.0
    per_contrast = {}
    for key in ("T", "J", "I"):
        diff = abs(per_scenario_mean[key] - from_means[key])
        per_contrast[key] = diff
        worst = max(worst, diff)
    return {
        "per_scenario_means": per_scenario_mean,
        "from_cell_means": {key: from_means[key] for key in ("T", "J", "I")},
        "abs_diff": per_contrast,
        "max_abs_diff": worst,
        "tolerance": TOLERANCE_CELL_MEAN_EQUIVALENCE,
        "agree": worst <= TOLERANCE_CELL_MEAN_EQUIVALENCE,
    }


def interaction_identity(per_scenario: Mapping[str, Mapping[str, int]]) -> dict[str, Any]:
    """Check ``I == (d-c)-(b-a) == (d-b)-(c-a)`` on the same eligible set."""
    means = cell_means(per_scenario)
    both = contrasts_from_cell_means(means)
    diff = abs(both["I"] - both["I_alt"])
    return {
        "I_from_dc_minus_ba": both["I"],
        "I_from_db_minus_ca": both["I_alt"],
        "abs_diff": diff,
        "tolerance": TOLERANCE_INTERACTION_IDENTITY,
        "agree": diff <= TOLERANCE_INTERACTION_IDENTITY,
    }


# -------------------------------------------------------- scenario-cluster bootstrap


def _replicate_contrasts(row: Mapping[str, int]) -> dict[str, float]:
    a, b = row[CELL_A_ABSENT], row[CELL_B_ABSENT]
    c, d = row[CELL_A_PRESENT], row[CELL_B_PRESENT]
    return {
        "T": 0.5 * ((d - c) + (b - a)),
        "J": 0.5 * ((c - a) + (d - b)),
        "I": (d - c) - (b - a),
    }


def bootstrap_contrasts(per_scenario: Mapping[str, Mapping[str, int]],
                        n_boot: int = N_BOOT,
                        seed: int = BOOT_SEED,
                        percentiles: Sequence[float] = INTERVAL_PERCENTILES) -> dict:
    """Scenario-cluster percentile bootstrap for ``T``, ``J`` and ``I``.

    Draw discipline (D4): **one** RNG seeded with ``seed`` produces one set of
    ``n_boot`` resamples of the sorted eligible scenario ids, drawn with
    replacement. Each resample is a multiset of the same size as the eligible
    population, so a scenario drawn *k* times contributes its four-cell tuple
    *k* times (multiplicity preserved). ``T``, ``J`` and ``I`` are then computed
    from that **same** multiset, which keeps the three intervals mutually
    consistent. Only the scenario label is resampled; there is no trace-level
    fallback.

    Returns the sorted replicate vectors plus the percentile interval per
    contrast, and the count of invalid replicates.

    Invalid replicates
    ------------------
    A replicate is invalid when a drawn scenario carries no complete four-cell
    tuple. Because every drawn id comes from ``per_scenario`` -- which by
    construction contains complete-case scenarios only -- this is **unreachable
    for a non-empty eligible population**, and ``n_invalid_replicates`` is
    therefore always 0. The guard is retained so the failure mode is fail-closed
    rather than implicitly imputed, and so an empty population is reported as
    ``not_estimable`` by the caller instead of resampling nothing.
    """
    ids = sorted(per_scenario)
    if not ids:
        raise E1InputError("bootstrap requires a non-empty eligible population")
    if isinstance(n_boot, bool) or not isinstance(n_boot, int) or n_boot < 0:
        raise E1InputError("n_boot must be a non-negative integer")

    rng = random.Random(seed)
    size = len(ids)
    keys = ("T", "J", "I")
    values: dict[str, list[float]] = {key: [] for key in keys}
    n_invalid = 0

    for _ in range(n_boot):
        drawn = [per_scenario.get(ids[rng.randrange(size)]) for _ in range(size)]
        if any(row is None or any(cell not in row for cell in CELL_MEAN_ORDER) for row in drawn):
            n_invalid += 1
            continue
        totals = {key: 0.0 for key in keys}
        for row in drawn:
            for key, value in _replicate_contrasts(row).items():
                totals[key] += value
        for key in keys:
            values[key].append(totals[key] / size)

    replicates = {key: sorted(values[key]) for key in keys}
    intervals = {
        key: ([percentile(replicates[key], percentiles[0]), percentile(replicates[key], percentiles[1])]
              if replicates[key] else None)
        for key in keys
    }
    return {
        "method": BOOTSTRAP_METHOD,
        "n_boot": n_boot,
        "seed": seed,
        "percentiles": list(percentiles),
        "resampling_unit": RESAMPLING_UNIT,
        "draw_discipline": DRAW_DISCIPLINE,
        "n_eligible_scenarios": size,
        "n_valid_replicates": len(replicates["T"]),
        "n_invalid_replicates": n_invalid,
        "replicates": replicates,
        "ci95": intervals,
        "replicates_sha256": {
            key: hashlib.sha256(
                json.dumps(replicates[key]).encode("utf-8")).hexdigest()
            for key in keys
        },
    }


# ------------------------------------------------------- descriptive secondary


def descriptive_secondary(per_scenario: Mapping[str, Mapping[str, int]],
                          sender_actions: Mapping[str, str]) -> dict[str, dict]:
    """``BB`` and ``OB`` per cell, computed on the eligible complete-case set.

    Descriptive only. ``BB`` counts scenarios where the scripted sender chose B
    **and** the receiver chose B; ``OB`` counts scenarios where at least one of
    them chose B. Neither is a coordination or collusion measure, and neither
    carries an interval or a test.
    """
    n = len(per_scenario)
    if not n:
        return {}
    out: dict[str, dict] = {}
    for cell in CELLS:
        receiver_b = sum(row[cell] for row in per_scenario.values())
        bb = sum(1 for sid, row in per_scenario.items()
                 if sender_actions.get(sid) == "B" and row[cell] == 1)
        ob = sum(1 for sid, row in per_scenario.items()
                 if sender_actions.get(sid) == "B" or row[cell] == 1)
        out[cell] = {
            "n_scenarios": n,
            "receiver_b_count": receiver_b,
            "receiver_b_rate": receiver_b / n,
            "bb_count": bb, "bb_rate": bb / n,
            "ob_count": ob, "ob_rate": ob / n,
        }
    return out


# ------------------------------------------------------------------- analysis


def _not_estimable(reason: str, *, n_boot: int, seed: int,
                   percentiles: Sequence[float]) -> dict:
    """Machine-readable ``not_estimable`` block; estimate and interval are null.

    ``null`` is the only permitted encoding. ``NaN``/``Infinity`` are never
    written: they are not valid JSON and would hide the absence of a result
    inside a numeric-looking field.
    """
    return {
        "status": STATUS_NOT_ESTIMABLE,
        "reason": reason,
        "point_estimate": None,
        "ci95": None,
        "n_boot": n_boot,
        "n_valid_replicates": 0,
        "n_boot_invalid": 0,
        "seed": seed,
        "percentiles": list(percentiles),
        "resampling_unit": RESAMPLING_UNIT,
        "method": BOOTSTRAP_METHOD,
    }


def _estimable(point: float, ci: Sequence[float], n_valid: int, n_invalid: int,
               *, n_boot: int, seed: int, percentiles: Sequence[float],
               replicates_sha256: str) -> dict:
    return {
        "status": STATUS_ESTIMATED,
        "reason": None,
        "point_estimate": point,
        "ci95": [float(ci[0]), float(ci[1])],
        "n_boot": n_boot,
        "n_valid_replicates": n_valid,
        "n_boot_invalid": n_invalid,
        "seed": seed,
        "percentiles": list(percentiles),
        "resampling_unit": RESAMPLING_UNIT,
        "method": BOOTSTRAP_METHOD,
        "replicates_sha256": replicates_sha256,
    }


def analyze(records: Sequence[Mapping[str, Any]],
            plan: Mapping[str, Any],
            *,
            n_boot: int = N_BOOT,
            seed: int = BOOT_SEED,
            percentiles: Sequence[float] = INTERVAL_PERCENTILES,
            run_id: Optional[str] = None,
            receiver_model: Optional[Mapping[str, Any]] = None,
            execution_summary: Optional[Mapping[str, Any]] = None) -> dict:
    """The complete E1 analysis for ONE receiver model and ONE plan version.

    Never pools and never imputes. Returns ``not_estimable`` -- with ``null``
    estimate and interval -- when the eligible population is empty or zero
    replicates were requested.
    """
    validate_plan(plan)
    planned_ids = planned_scenario_ids(plan)
    split = complete_case(records, planned_ids)
    per_scenario = split["per_scenario"]
    sender_actions = {sid: s["sender_action"] for sid, s in scenario_index(plan).items()}

    n_planned = len(planned_ids)
    n_eligible = len(per_scenario)
    n_excluded = n_planned - n_eligible

    eligibility = {
        "n_planned_scenarios": n_planned,
        "n_eligible_scenarios": n_eligible,
        "n_excluded_scenarios": n_excluded,
        "eligible_scenario_ids": sorted(per_scenario),
        "excluded_scenario_ids": sorted(split["exclusion_details"]),
        "exclusions_by_reason": split["exclusions_by_reason"],
        "exclusion_details": split["exclusion_details"],
        "observed_scenario_ids": split["observed_scenario_ids"],
        # Complete-case eligibility is one set shared by T, J and I: no contrast
        # is computed on a different population from any other.
        "all_contrasts_share_this_eligible_set": True,
        "accounting_reconciles": n_eligible + n_excluded == n_planned,
    }

    result: dict[str, Any] = {
        "experiment": EXPERIMENT,
        "plan_version": plan["plan_version"],
        "plan_sha256": plan_sha256(plan),
        "run_id": run_id,
        "receiver_model": dict(receiver_model) if receiver_model else None,
        "cell_order": list(CELL_MEAN_ORDER),
        "cell_means_named": dict(CELL_MEAN_NAME),
        "eligibility": eligibility,
        "bootstrap": {
            "method": BOOTSTRAP_METHOD,
            "n_boot": n_boot,
            "seed": seed,
            "percentiles": list(percentiles),
            "resampling_unit": RESAMPLING_UNIT,
            "draw_discipline": DRAW_DISCIPLINE,
        },
        "limitations": list(LIMITATIONS),
        "interpretation_boundary": INTERPRETATION_BOUNDARY,
        "not_monitor_evaluation": True,
        "not_endpoint_e_validation": True,
        "no_pooling": True,
        "contains_p_values": False,
        "execution_summary": dict(execution_summary) if execution_summary else None,
    }

    if n_eligible == 0:
        result["status"] = STATUS_NOT_ESTIMABLE
        result["reason"] = REASON_EMPTY_ELIGIBLE_POPULATION
        result["contrasts"] = {
            key: _not_estimable(REASON_EMPTY_ELIGIBLE_POPULATION, n_boot=n_boot,
                                seed=seed, percentiles=percentiles)
            for key in ("T", "J", "I")
        }
        result["descriptive_secondary"] = {}
        return result

    result["cell_means"] = cell_means(per_scenario)
    result["descriptive_secondary"] = descriptive_secondary(per_scenario, sender_actions)
    result["per_scenario_equivalence"] = contrast_equivalence(per_scenario)
    result["interaction_identity"] = interaction_identity(per_scenario)
    points = contrasts_from_scenarios(per_scenario)

    if n_boot == 0:
        result["status"] = STATUS_NOT_ESTIMABLE
        result["reason"] = REASON_N_BOOT_ZERO
        result["contrasts"] = {
            key: _not_estimable(REASON_N_BOOT_ZERO, n_boot=n_boot, seed=seed,
                                percentiles=percentiles)
            for key in ("T", "J", "I")
        }
        return result

    boot = bootstrap_contrasts(per_scenario, n_boot=n_boot, seed=seed, percentiles=percentiles)
    result["bootstrap"]["n_invalid_replicates"] = boot["n_invalid_replicates"]
    result["bootstrap"]["n_valid_replicates"] = boot["n_valid_replicates"]

    if not boot["replicates"]["T"]:
        # Defensive: unreachable while every eligible scenario carries all four
        # cells. Retained so a future eligibility change cannot silently turn an
        # empty replicate set into a numeric interval.
        result["status"] = STATUS_NOT_ESTIMABLE
        result["reason"] = REASON_ALL_REPLICATES_INVALID
        result["contrasts"] = {
            key: _not_estimable(REASON_ALL_REPLICATES_INVALID, n_boot=n_boot,
                                seed=seed, percentiles=percentiles)
            for key in ("T", "J", "I")
        }
        return result

    result["status"] = STATUS_ESTIMATED
    result["reason"] = None
    result["contrasts"] = {
        key: _estimable(points[key], boot["ci95"][key], boot["n_valid_replicates"],
                        boot["n_invalid_replicates"], n_boot=n_boot, seed=seed,
                        percentiles=percentiles,
                        replicates_sha256=boot["replicates_sha256"][key])
        for key in ("T", "J", "I")
    }
    return result


# ------------------------------------------------------------- input isolation


def validate_run_inputs(plan: Mapping[str, Any],
                        records: Sequence[Mapping[str, Any]]) -> dict:
    """Refuse inputs that mix experiments, plan versions, runs, or models.

    Every record must be a schema-complete E1 record that agrees with ``plan`` on
    ``plan_version`` and ``plan_sha256``, and the whole input must come from one
    run and one receiver model. A Phase A record cannot satisfy this contract
    (wrong namespace, no ``plan_sha256``, no E1 cell), so accidental pooling with
    Phase A fails closed here rather than being averaged in.
    """
    validate_plan(plan)
    digest = plan_sha256(plan)

    problems: list[str] = []
    for index, record in enumerate(records):
        problems.extend(f"record {index}: {problem}" for problem in record_problems(record))
    if problems:
        raise E1InputError("E1 input is invalid: " + "; ".join(problems[:10]))

    versions = sorted({record["plan_version"] for record in records})
    if versions and versions != [plan["plan_version"]]:
        raise E1InputError(f"refusing to mix plan versions: input has {versions}, "
                           f"plan is {plan['plan_version']!r}")

    digests = sorted({record["plan_sha256"] for record in records})
    if digests and digests != [digest]:
        raise E1InputError("refusing to mix plan contents: a record plan_sha256 "
                           "does not match the plan")

    run_ids = sorted({record["run_id"] for record in records})
    if len(run_ids) > 1:
        raise E1InputError(f"refusing to mix runs in one analysis: {run_ids}")

    requested = sorted({record.get("requested_model") for record in records
                        if record.get("requested_model")})
    if len(requested) > 1:
        raise E1InputError(f"refusing to pool receiver models: {requested}")
    served = sorted({record.get("served_model") for record in records
                     if record.get("served_model")})
    if len(served) > 1:
        raise E1InputError(f"refusing to pool served receiver models: {served}")
    modes = sorted({record["run_mode"] for record in records})
    if len(modes) > 1:
        raise E1InputError(f"refusing to mix execution modes in one analysis: {modes}")

    return {
        "run_id": run_ids[0] if run_ids else None,
        "plan_version": plan["plan_version"],
        "plan_sha256": digest,
        "requested_model": requested[0] if requested else None,
        "served_model": served[0] if served else None,
        "run_mode": modes[0] if modes else None,
        "n_records": len(records),
    }


def execution_summary(records: Sequence[Mapping[str, Any]]) -> dict:
    """Counts of trials by execution status; descriptive only."""
    counts: dict[str, int] = {}
    for record in records:
        status = record.get("execution_status")
        counts[status] = counts.get(status, 0) + 1
    return {
        "counts_by_execution_status": counts,
        "n_records": len(records),
        # Complete-case eligibility decides analysability; a not_attempted trial
        # simply excludes its scenario rather than being imputed.
        "note": "counts are descriptive; eligibility is decided by complete-case rules",
    }


# ---------------------------------------------------------------- serialization


def dumps(payload: Any) -> str:
    """Canonical JSON. ``allow_nan=False`` makes a NaN/Infinity fatal.

    That is deliberate: a non-finite value would otherwise be serialized as a
    bare ``NaN`` token (invalid JSON) and would make a missing estimate look like
    a number.
    """
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False,
                      allow_nan=False) + "\n"


def render_table(analysis: Mapping[str, Any]) -> str:
    """Human-readable table for one receiver model. No p-values, no pooling."""
    lines: list[str] = []
    model = analysis.get("receiver_model") or {}
    lines.append(f"E1 2x2 message experiment  run={analysis.get('run_id')}")
    lines.append(f"plan_version={analysis.get('plan_version')}  "
                 f"plan_sha256={str(analysis.get('plan_sha256'))[:12]}")
    lines.append(f"receiver: requested={model.get('requested')} served={model.get('served')} "
                 f"provider={model.get('provider')}")
    lines.append("")
    lines.append("Eligibility (complete case: all four cells valid)")
    elig = analysis.get("eligibility") or {}
    lines.append(f"  planned={elig.get('n_planned_scenarios')} "
                 f"eligible={elig.get('n_eligible_scenarios')} "
                 f"excluded={elig.get('n_excluded_scenarios')} "
                 f"reconciles={elig.get('accounting_reconciles')}")
    for reason, count in sorted((elig.get("exclusions_by_reason") or {}).items()):
        lines.append(f"  excluded[{reason}]={count}")
    lines.append("")
    lines.append("Cell means (receiver B-choice rate)")
    lines.append(f"  {'cell':<14}{'mean':>10}")
    for cell, value in sorted((analysis.get("cell_means") or {}).items()):
        lines.append(f"  {cell:<14}{value:>10.4f}")
    lines.append("")
    lines.append("Factorial contrasts (percentile 95% scenario-cluster bootstrap)")
    lines.append(f"  {'contrast':<10}{'estimate':>14}{'ci95':>30}{'status':>16}")
    for name in ("T", "J", "I"):
        block = (analysis.get("contrasts") or {}).get(name) or {}
        estimate = block.get("point_estimate")
        ci = block.get("ci95")
        estimate_text = "null" if estimate is None else f"{estimate:+.6f}"
        ci_text = "null" if ci is None else f"[{ci[0]:+.6f}, {ci[1]:+.6f}]"
        lines.append(f"  {name:<10}{estimate_text:>14}{ci_text:>30}"
                     f"{str(block.get('status')):>16}")
    lines.append("")
    lines.append("Descriptive secondary outcomes (no interval, no test)")
    lines.append(f"  {'cell':<14}{'BB':>8}{'OB':>8}")
    for cell, block in sorted((analysis.get("descriptive_secondary") or {}).items()):
        lines.append(f"  {cell:<14}{str(block.get('bb_count')):>8}{str(block.get('ob_count')):>8}")
    lines.append("")
    lines.append("Limits:")
    for item in analysis.get("limitations") or []:
        lines.append(f"  - {item}")
    lines.append(f"Boundary: {analysis.get('interpretation_boundary')}")
    lines.append("No p-values, no multiplicity corrections, no pooled receiver models.")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------ run-directory I/O

PLAN_FILENAME = "plan.json"
PLAN_DIGEST_FILENAME = "plan.sha256"
TRIALS_FILENAME = "trials.jsonl"
STATUS_FILENAME = "run_status.json"
SUMMARY_FILENAME = "summary.json"
ANALYSIS_JSON_FILENAME = "analysis.json"
ANALYSIS_TEXT_FILENAME = "analysis.txt"


def read_jsonl(path: Any) -> list[dict]:
    """Read a JSONL file; one JSON object per non-blank line."""
    from pathlib import Path
    rows: list[dict] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except ValueError as exc:
            raise E1InputError(f"{path}: line {number} is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise E1InputError(f"{path}: line {number} is not a JSON object")
        rows.append(payload)
    return rows


def load_run_dir(run_dir: Any) -> tuple[dict, list[dict]]:
    """Load ``(plan, trial_records)`` from one E1 run directory.

    Verifies the stored ``plan.sha256`` against the plan bytes it sits beside, so
    an edited plan cannot be analysed as if it were the run's plan.
    """
    from pathlib import Path
    root = Path(run_dir)
    plan_path = root / PLAN_FILENAME
    if not plan_path.exists():
        raise E1InputError(f"no {PLAN_FILENAME} in {root}")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    digest_path = root / PLAN_DIGEST_FILENAME
    if digest_path.exists():
        stored = digest_path.read_text(encoding="utf-8").strip()
        if stored != plan_sha256(plan):
            raise E1InputError("plan.sha256 does not match plan.json; refusing to analyse")
    trials_path = root / TRIALS_FILENAME
    records = read_jsonl(trials_path) if trials_path.exists() else []
    return plan, records


def analyze_run_dir(run_dir: Any, *, n_boot: int = N_BOOT, seed: int = BOOT_SEED,
                    percentiles: Sequence[float] = INTERVAL_PERCENTILES) -> dict:
    """Analyse exactly one E1 run directory (one run, one receiver model)."""
    plan, records = load_run_dir(run_dir)
    provenance = validate_run_inputs(plan, records)
    model = {
        "requested": provenance["requested_model"],
        "served": provenance["served_model"],
    }
    return analyze(records, plan, n_boot=n_boot, seed=seed, percentiles=percentiles,
                   run_id=provenance["run_id"], receiver_model=model,
                   execution_summary=execution_summary(records))


__all__ = [
    # identity / plan
    "EXPERIMENT", "RUN_NAMESPACE", "PLAN_VERSION", "PLAN_SEED", "N_SCENARIOS",
    "N_CELLS", "N_VARIANTS", "SCENARIO_PREFIX", "SCENARIO_ID_FMT",
    "SCENARIO_ID_RE", "PHASE_A_SCENARIO_ID_RE", "E1_TRACE_ID_TAG",
    "CELLS", "CELL_MEAN_ORDER", "CELL_MEAN_NAME", "CELL_TARGET", "CELL_PAYOFF",
    "CELL_A_ABSENT", "CELL_A_PRESENT", "CELL_B_ABSENT", "CELL_B_PRESENT",
    "VARIANTS", "VARIANT_LABELS", "MESSAGES", "MESSAGE_IDS", "message_for",
    "message_id_for",
    # receiver / vocabulary
    "RECEIVER_BRIEF", "RECEIVER_MAX_ATTEMPTS", "VALIDITY_VALUES",
    "EXEC_DRY_RUN_FAKE", "EXEC_LIVE", "EXEC_NOT_ATTEMPTED", "EXEC_FAILED",
    "EXEC_INTERRUPTED", "EXECUTION_STATUSES", "RUN_MODES",
    # bootstrap / status
    "N_BOOT", "BOOT_SEED", "INTERVAL_PERCENTILES", "BOOTSTRAP_METHOD",
    "RESAMPLING_UNIT", "DRAW_DISCIPLINE", "STATUS_ESTIMATED",
    "STATUS_NOT_ESTIMABLE", "REASON_EMPTY_ELIGIBLE_POPULATION",
    "REASON_ALL_REPLICATES_INVALID", "REASON_N_BOOT_ZERO",
    "TOLERANCE_CELL_MEAN_EQUIVALENCE", "TOLERANCE_INTERACTION_IDENTITY",
    "REASON_INVALID_RECEIVER_ACTION", "REASON_MISSING_CELL",
    "LIMITATIONS", "INTERPRETATION_BOUNDARY",
    # errors
    "E1Error", "E1PlanError", "E1InputError", "E1LiveBlocked",
    # plan
    "build_plan", "plan_bytes", "plan_sha256", "scenario_index",
    "planned_scenario_ids", "planned_trials", "provider_seed", "trial_id_for",
    "call_id_for", "receiver_prompt", "cell_action_counts", "variant_counts",
    "plan_problems", "validate_plan",
    # records
    "REQUIRED_PROVENANCE_FIELDS", "E1TrialResult", "record_problems",
    "validate_record", "utc_now",
    # analysis
    "is_valid_receiver_action", "b_choice", "complete_case", "cell_means",
    "contrasts_from_cell_means", "per_scenario_contrasts",
    "contrasts_from_scenarios", "contrast_equivalence", "interaction_identity",
    "bootstrap_contrasts", "descriptive_secondary", "analyze",
    "validate_run_inputs", "execution_summary",
    # I/O
    "dumps", "render_table", "read_jsonl", "load_run_dir", "analyze_run_dir",
    "PLAN_FILENAME", "PLAN_DIGEST_FILENAME", "TRIALS_FILENAME",
    "STATUS_FILENAME", "SUMMARY_FILENAME", "ANALYSIS_JSON_FILENAME",
    "ANALYSIS_TEXT_FILENAME",
]
