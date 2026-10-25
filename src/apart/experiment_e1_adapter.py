"""E1-only conversion: an E1 trial record -> a monitor ``Trace``, or a rejection.

This module is the E1 counterpart of ``apart.phase_a_to_trace``. It is a
separate module on purpose:

* the Phase A adapter is **not imported, wrapped, monkey-patched or edited**;
* the Phase A condition table is never extended with E1 cells, so a new E1
  condition can never leak into a Phase A conversion;
* the E1 identity scheme is independent (different scenario namespace, different
  trace-id tag, different pairing-key input), so no E1 identifier can collide
  with a Phase A identifier.

Conversion is total: every input record yields exactly one :class:`E1Converted`
with an explicit status, so no trial is ever silently dropped. The statuses
reuse the vocabulary already used for Phase A conversion
(``valid``/``ambiguous``/``invalid``/``failed``/``rejected``).

Emission scope
--------------
Producing a ``Trace`` here does **not** authorise monitor evaluation. E1 is not a
monitor-evaluation experiment and is not endpoint-E validation; nothing in this
module scores a trace, and no E label is read or derived. The trace is built only
so that E1 records can be serialized with the canonical schema and so that the
delivery/validity contract stays explicit.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping, Optional

from apart.experiment_e1 import (
    CELLS,
    EXEC_DRY_RUN_FAKE,
    EXEC_FAILED,
    EXEC_INTERRUPTED,
    EXEC_LIVE,
    EXEC_NOT_ATTEMPTED,
    EXPERIMENT,
    MESSAGES,
    PLAN_VERSION,
    record_problems,
)
from apart.schema import DeliveryCondition, Message, Trace

#: Every E1 cell delivers a sender-authored message, so the delivery condition is
#: a constant. It carries no condition information: the condition lives in the
#: provenance sidecar and is never shown to a monitor.
E1_DELIVERY_BY_CELL: dict[str, DeliveryCondition] = {cell: DeliveryCondition.FREE_MSG for cell in CELLS}

#: Fixed agent convention, identical to Phase A: agent_1 is the scripted sender,
#: agent_2 is the receiver. Matches ``DEFAULT_PAYOFFS`` (row = agent_1).
E1_SENDER_AGENT = "agent_1"
E1_RECEIVER_AGENT = "agent_2"

_VALID_ACTIONS = ("A", "B")

ConversionStatus = Literal["valid", "ambiguous", "invalid", "failed", "rejected"]
CONVERSION_STATUSES: tuple[str, ...] = ("valid", "ambiguous", "invalid", "failed", "rejected")


def e1_pairing_key(scenario_id: str) -> int:
    """Deterministic E1 scenario pairing key (``Trace.seed``).

    A scenario pairing key, **not** a PRNG seed: it is derived from the scenario
    label alone, is identical for the four cells of a scenario, and is never
    drawn at random. The input is namespaced by ``EXPERIMENT`` and
    ``PLAN_VERSION``, so an E1 key can never coincide with a Phase A key computed
    from an otherwise identical label. The frozen Phase A derivation
    (``apart.phase_a_to_trace.derive_scenario_pairing_key``) is untouched.
    """
    digest = hashlib.sha256(f"{EXPERIMENT}|{PLAN_VERSION}|{scenario_id}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


def e1_trace_id(scenario_id: str, cell_id: str) -> str:
    """Opaque, stable, per-(scenario, cell) E1 trace identifier.

    Bookkeeping only, never a security boundary. Uses the ``e1-`` tag so it can
    never be confused with a Phase A ``p1-`` trace id.
    """
    digest = hashlib.sha256(f"{EXPERIMENT}|{PLAN_VERSION}|{scenario_id}|{cell_id}".encode()).hexdigest()
    return f"{EXPERIMENT}-{digest[:16]}"


@dataclass(frozen=True)
class E1Provenance:
    """Sidecar for one E1 trial: everything a monitor must never see."""

    run_id: str
    trial_id: str
    scenario_id: str
    cell_id: str
    target: str
    payoff_mention: str
    variant_index: int
    variant_label: str
    message_id: str
    sender_action: str
    execution_status: str
    run_mode: str
    validity: Optional[str] = None
    parsed_action: Optional[str] = None
    reason: Optional[str] = None
    error: Optional[str] = None
    error_stage: Optional[str] = None
    receiver_raw_output: Optional[str] = None
    plan_version: str = PLAN_VERSION

    @property
    def scenario_pairing_key(self) -> int:
        return e1_pairing_key(self.scenario_id)


@dataclass
class E1Converted:
    """One conversion outcome: a status, its sidecar, and any failures."""

    status: str
    provenance: Optional[E1Provenance] = None
    trace: Optional[Trace] = None
    failures: list[str] = field(default_factory=list)


def _provenance(record: Mapping[str, Any]) -> E1Provenance:
    return E1Provenance(
        run_id=record.get("run_id", ""),
        trial_id=record.get("trial_id", ""),
        scenario_id=record.get("scenario_id", ""),
        cell_id=record.get("cell_id", ""),
        target=record.get("target", ""),
        payoff_mention=record.get("payoff_mention", ""),
        variant_index=record.get("variant_index", 0),
        variant_label=record.get("variant_label", ""),
        message_id=record.get("message_id", ""),
        sender_action=record.get("sender_action", ""),
        execution_status=record.get("execution_status", ""),
        run_mode=record.get("run_mode", ""),
        validity=record.get("validity"),
        parsed_action=record.get("parsed_action"),
        reason=record.get("reason"),
        error=record.get("error"),
        error_stage=record.get("error_stage"),
        receiver_raw_output=record.get("receiver_raw_output"),
    )


def convert_e1_result(record: Mapping[str, Any]) -> E1Converted:
    """Classify one E1 record. Total: always returns, never raises.

    Rejection is fail-closed and ordered: a malformed record, a non-attempted or
    interrupted trial, a transport failure, an unknown cell, a non-A/B sender
    action, a message that is not the exact frozen literal, and finally a
    receiver action that is not a valid A/B are each rejected for their own
    reason. Nothing is imputed and no invalid action is ever coerced to A or B.
    """
    provenance = _provenance(record)
    problems = record_problems(record)
    if problems:
        return E1Converted("rejected", provenance,
                           failures=[f"malformed record: {problem}" for problem in problems])

    status = record["execution_status"]
    cell_id = record["cell_id"]
    variant_index = record["variant_index"]

    if status == EXEC_NOT_ATTEMPTED:
        return E1Converted("rejected", provenance, failures=["trial was not attempted"])
    if status == EXEC_INTERRUPTED:
        return E1Converted("rejected", provenance,
                           failures=["trial was interrupted before completion"])
    if status == EXEC_FAILED:
        return E1Converted("failed", provenance, failures=[
            f"transport failure at stage {record.get('error_stage')!r}: {record.get('error')!r}"])
    if status not in (EXEC_DRY_RUN_FAKE, EXEC_LIVE):
        return E1Converted("rejected", provenance,
                           failures=[f"unexpected execution_status {status!r}"])

    if cell_id not in E1_DELIVERY_BY_CELL:
        return E1Converted("rejected", provenance, failures=[f"unknown E1 cell {cell_id!r}"])
    if record["sender_action"] not in _VALID_ACTIONS:
        return E1Converted("rejected", provenance, failures=[
            f"sender_action must be A or B, got {record['sender_action']!r}"])

    expected_message = MESSAGES.get((cell_id, variant_index))
    if expected_message is None:
        return E1Converted("rejected", provenance, failures=[
            f"no frozen message for cell {cell_id!r} variant {variant_index!r}"])
    if record["message_text"] != expected_message:
        # Exact equality only. Prefix/substring matching would silently accept a
        # Phase A template, two of which begin with an E1 literal.
        return E1Converted("rejected", provenance, failures=[
            "message_text is not the exact frozen literal for this cell/variant"])

    validity = record.get("validity")
    if validity == "ambiguous":
        return E1Converted("ambiguous", provenance,
                           failures=["receiver produced no A/B action"])
    if validity == "invalid":
        return E1Converted("invalid", provenance, failures=[
            f"receiver reply was not parseable ({record.get('reason')!r})"])
    if validity != "valid" or record.get("parsed_action") not in _VALID_ACTIONS:
        return E1Converted("rejected", provenance, failures=[
            "valid conversion requires parsed_action in A/B "
            f"(got {record.get('parsed_action')!r}, validity={validity!r})"])

    try:
        trace = Trace(
            trace_id=e1_trace_id(record["scenario_id"], cell_id),
            # `Trace.seed` carries the deterministic E1 scenario pairing key;
            # this module never draws a random-number-generator seed.
            seed=e1_pairing_key(record["scenario_id"]),
            scenario_id=record["scenario_id"],
            messages=[Message(sender=E1_SENDER_AGENT, recipient=E1_RECEIVER_AGENT,
                              text=record["message_text"])],
            raw_actions={E1_SENDER_AGENT: record["sender_action"],
                         E1_RECEIVER_AGENT: record["parsed_action"]},
            delivery_condition=E1_DELIVERY_BY_CELL[cell_id],
            claims=[], template_ids=[], private_plans={},
        )
        trace.validate()
    except ValueError as exc:
        return E1Converted("rejected", provenance,
                           failures=[f"trace failed validation: {exc}"])
    return E1Converted("valid", provenance, trace=trace)


def convert_many(records: Iterable[Mapping[str, Any]]) -> tuple[list[Trace], list[E1Converted]]:
    """Convert many records; return ``(valid traces, every conversion outcome)``.

    The second list has one entry per input record, so a rejected trial is always
    inspectable rather than silently absent.
    """
    converted = [convert_e1_result(record) for record in records]
    valid = [c.trace for c in converted if c.status == "valid" and c.trace is not None]
    return valid, converted


def conversion_report(converted: Iterable[E1Converted]) -> dict:
    """Status counts plus a per-trial failure list for every non-valid record."""
    items = list(converted)
    counts: dict[str, int] = {status: 0 for status in CONVERSION_STATUSES}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
    return {
        "n_records": len(items),
        "counts_by_status": counts,
        "exclusions": [
            {"trial_id": item.provenance.trial_id if item.provenance else None,
             "scenario_id": item.provenance.scenario_id if item.provenance else None,
             "cell_id": item.provenance.cell_id if item.provenance else None,
             "status": item.status, "failures": list(item.failures)}
            for item in items if item.status != "valid"
        ],
    }


def batch_valid_by_delivery(traces: Iterable[Trace]) -> dict[DeliveryCondition, list[Trace]]:
    """Group valid traces by delivery condition (all E1 traces are ``FREE_MSG``)."""
    batches: dict[DeliveryCondition, list[Trace]] = {}
    for trace in traces:
        batches.setdefault(trace.delivery_condition, []).append(trace)
    return batches


__all__ = [
    "E1_DELIVERY_BY_CELL", "E1_SENDER_AGENT", "E1_RECEIVER_AGENT",
    "ConversionStatus", "CONVERSION_STATUSES", "E1Provenance", "E1Converted",
    "e1_pairing_key", "e1_trace_id", "convert_e1_result", "convert_many",
    "conversion_report", "batch_valid_by_delivery",
]
