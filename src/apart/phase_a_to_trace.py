"""Phase A -> monitor ``Trace`` converter: ``PhaseATrialResult`` -> ``Trace``.

Bridges the Phase A (No Message / Neutral Message / Informative Message)
trial record to the canonical ``Trace`` consumed by the monitor pipeline
(``monitors.observable`` and ``monitors.runner``). The converter is purely
additive: it does not modify ``apart.schema`` or any existing module, and it
never performs network or model calls.

Approved contract (see the integration checkpoint and authorization):

- ``Trace.seed`` is a deterministic, NON-RANDOM integer derived from
  ``scenario_id`` ALONE (``derive_scenario_pairing_key``). It is a scenario
  pairing key, not a PRNG seed, and
  it is identical for the three conditions of a scenario. Because the monitor
  runner keys every trace by ``trace.seed``, callers MUST invoke
  ``monitors.runner.run_corpus`` ONE CONDITION AT A TIME so seeds are unique
  within each invocation.
- ``Trace.trace_id`` is an opaque, stable, per-(scenario, condition)
  identifier. It is a bookkeeping handle only, NOT a security boundary.
- ``Trace.scenario_id`` now carries the Phase A ``scenario_id`` verbatim, so
  scenario grouping survives into the trace, the monitor score rows and the
  E-label rows. It is additional metadata for grouping observations; every
  record-level join still uses ``trace_id``, and ``scenario_id`` is never
  derived from ``seed`` or ``trace_id``.
- Ambiguous, invalid, failed and rejected trials are preserved in the
  provenance sidecar and are NEVER emitted as a scored ``Trace``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable, Literal, Optional

from apart.phase_a_conditions import (
    INFORMATIVE_MESSAGE,
    NEUTRAL_MESSAGE,
    NO_MESSAGE,
)
from apart.phase_a_models import PhaseATrialResult
from apart.schema import DeliveryCondition, Message, Trace

# Assigned condition (Phase A string) -> assigned delivery condition (Trace enum).
_CONDITION_TO_DELIVERY = {
    NO_MESSAGE: DeliveryCondition.NO_MSG,
    NEUTRAL_MESSAGE: DeliveryCondition.NEUTRAL_MSG,
    INFORMATIVE_MESSAGE: DeliveryCondition.INFORM_MSG,
    # Live pilot conditions (apart.phase_a_live, approved 2026-10-24). C2 and C3 both
    # deliver a sender-authored message, so both map to FREE_MSG; the condition label
    # itself stays in the provenance sidecar and is never shown to a monitor.
    "c0_no_message": DeliveryCondition.NO_MSG,
    "c1_neutral": DeliveryCondition.NEUTRAL_MSG,
    "c2_a_coordination": DeliveryCondition.FREE_MSG,
    "c3_b_coordination": DeliveryCondition.FREE_MSG,
}

# Fixed agent convention: sender is agent_1, receiver is agent_2. This matches
# DEFAULT_PAYOFFS (row = agent_1, col = agent_2) and the M2/M3/M4 renderings.
_SENDER_AGENT = "agent_1"
_RECEIVER_AGENT = "agent_2"

_VALID_ACTIONS = ("A", "B")

ConversionStatus = Literal["valid", "ambiguous", "invalid", "failed", "rejected"]


@dataclass(frozen=True)
class PhaseAProvenance:
    """Sidecar provenance for one Phase A trial.

    Everything Phase A records about a trial that is NOT observable monitor
    evidence lives here. It is never placed on a ``Trace`` and never shown to
    a monitor.
    """

    scenario_id: str
    trial_id: str
    condition: str
    sender_action: str
    message_delivered: bool
    receiver_raw_output: Optional[str] = None
    parsed_action: Optional[str] = None
    validity: Optional[str] = None
    reason: Optional[str] = None
    error: Optional[str] = None
    error_stage: Optional[str] = None
    execution_status: str = "deterministic-fixture"
    model: Optional[str] = None
    provider: Optional[str] = None
    randomized: bool = False
    # Deterministic pairing key, also written to ``Trace.seed``. Recorded here so
    # an auditor can see it was never drawn from a random-number generator.
    scenario_pairing_key: Optional[int] = None


@dataclass
class ConvertedTrace:
    """Discriminated result of converting one Phase A trial.

    Only ``status == "valid"`` carries a ``trace`` that may enter the
    monitor-scored corpus. Every other status is sidecar-only.
    """

    status: ConversionStatus
    provenance: PhaseAProvenance
    trace: Optional[Trace] = None
    failures: list[str] = field(default_factory=list)


def derive_scenario_pairing_key(scenario_id: str) -> int:
    """Deterministic, non-random scenario pairing key (NOT a PRNG seed).

    Derived from ``scenario_id`` alone, so all three conditions of a scenario
    share it. Callers must therefore batch by condition before calling the
    monitor runner. The value is written to ``Trace.seed`` only because that is
    the canonical schema's slot for it; nothing here draws from a
    random-number generator, so the field is not a reproducible RNG seed.
    """
    digest = hashlib.sha256(scenario_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


# Backwards-compatible alias. This function has never returned a PRNG seed; the
# older name is kept because existing callers and tests import it.
derive_seed = derive_scenario_pairing_key


def derive_trace_id(scenario_id: str, condition: str) -> str:
    """Opaque, stable per-(scenario, condition) identifier.

    The condition is hashed inside the digest, so it is not recoverable from
    the returned id. This is a bookkeeping handle, not a security boundary.
    """
    digest = hashlib.sha256(f"{scenario_id}|{condition}".encode("utf-8")).hexdigest()
    return f"p1-{digest[:16]}"


def _make_provenance(
    result: PhaseATrialResult,
    *,
    execution_status: str,
    model: Optional[str],
    provider: Optional[str],
    randomized: bool,
) -> PhaseAProvenance:
    meta = result.metadata
    return PhaseAProvenance(
        scenario_id=meta.scenario_id,
        trial_id=meta.trial_id,
        condition=meta.condition,
        sender_action=result.sender_action,
        message_delivered=result.message_delivered,
        receiver_raw_output=result.receiver_raw_output,
        parsed_action=result.parsed_action,
        validity=result.validity,
        reason=result.reason,
        error=result.error,
        error_stage=result.error_stage,
        execution_status=execution_status,
        model=model,
        provider=provider,
        randomized=randomized,
        scenario_pairing_key=derive_scenario_pairing_key(meta.scenario_id),
    )


def convert_phase_a_result(
    result: PhaseATrialResult,
    *,
    execution_status: str = "deterministic-fixture",
    model: Optional[str] = None,
    provider: Optional[str] = None,
    randomized: bool = False,
) -> ConvertedTrace:
    """Convert one Phase A trial to a scored ``Trace`` or a sidecar-only result.

    Fail-closed ordering: any terminal problem (failure, invalid, ambiguous,
    unknown condition, non-A/B sender action, inconsistent delivery, missing
    receiver action) yields a non-``valid`` status and no ``Trace``.

    ``randomized`` records whether the caller randomised condition assignment or
    order. It defaults to ``False`` because the scaffold does not randomise, and
    it changes only the provenance record, never the trial or the trace.
    """
    provenance = _make_provenance(
        result,
        execution_status=execution_status,
        model=model,
        provider=provider,
        randomized=randomized,
    )

    if result.error is not None:
        return ConvertedTrace(
            "failed", provenance,
            failures=[f"{result.error_stage or 'trial'} error: {result.error}"],
        )

    if result.validity == "invalid":
        return ConvertedTrace(
            "invalid", provenance,
            failures=[f"invalid receiver output: {result.reason}"],
        )

    if result.validity == "ambiguous":
        return ConvertedTrace(
            "ambiguous", provenance,
            failures=[f"ambiguous receiver output: {result.reason}"],
        )

    condition = result.metadata.condition
    if condition not in _CONDITION_TO_DELIVERY:
        return ConvertedTrace(
            "rejected", provenance, failures=[f"unknown condition: {condition!r}"]
        )

    if result.sender_action not in _VALID_ACTIONS:
        return ConvertedTrace(
            "rejected", provenance,
            failures=[f"sender_action must be A or B, got {result.sender_action!r}"],
        )

    delivery_condition = _CONDITION_TO_DELIVERY[condition]

    if delivery_condition is DeliveryCondition.NO_MSG:
        if result.message_delivered:
            return ConvertedTrace(
                "rejected", provenance,
                failures=["NO_MESSAGE trial delivered a message"],
            )
        messages: list[Message] = []
    else:
        if not result.message_delivered:
            return ConvertedTrace(
                "rejected", provenance,
                failures=[f"{condition} trial did not deliver a message"],
            )
        if not result.message_content:
            return ConvertedTrace(
                "rejected", provenance,
                failures=[f"{condition} trial delivered empty content"],
            )
        messages = [
            Message(
                sender=_SENDER_AGENT,
                recipient=_RECEIVER_AGENT,
                text=result.message_content,
            )
        ]

    if result.validity != "valid" or result.parsed_action not in _VALID_ACTIONS:
        return ConvertedTrace(
            "rejected", provenance,
            failures=[
                "valid conversion requires parsed_action in A/B "
                f"(got {result.parsed_action!r}, validity={result.validity!r})"
            ],
        )

    trace = Trace(
        trace_id=derive_trace_id(result.metadata.scenario_id, condition),
        # `Trace.seed` carries the deterministic scenario pairing key -- this
        # scaffold never draws a random-number-generator seed.
        seed=derive_scenario_pairing_key(result.metadata.scenario_id),
        # The scenario identity is carried verbatim from the Phase A trial
        # metadata (and remains in `PhaseAProvenance`). It is additional to
        # `trace_id`, never a replacement for it, and is never derived from
        # `seed` or from the trace id.
        scenario_id=result.metadata.scenario_id,
        messages=messages,
        raw_actions={
            _SENDER_AGENT: result.sender_action,
            _RECEIVER_AGENT: result.parsed_action,
        },
        delivery_condition=delivery_condition,
        claims=[],
        template_ids=[],
        private_plans={},
    )
    trace.validate()
    return ConvertedTrace("valid", provenance, trace=trace)


def convert_many(
    results: Iterable[PhaseATrialResult],
    **kwargs,
) -> tuple[list[Trace], list[ConvertedTrace]]:
    """Convert many trials; return (valid scored traces, all sidecar results)."""
    converted = [convert_phase_a_result(result, **kwargs) for result in results]
    valid = [c.trace for c in converted if c.status == "valid" and c.trace is not None]
    return valid, converted


def batch_valid_by_condition(
    traces: Iterable[Trace],
) -> dict[DeliveryCondition, list[Trace]]:
    """Group valid traces by assigned delivery condition.

    This encodes the batching requirement: pass each group to
    ``monitors.runner.run_corpus`` separately so every invocation has unique
    scenario-derived seeds.
    """
    batches: dict[DeliveryCondition, list[Trace]] = {}
    for trace in traces:
        batches.setdefault(trace.delivery_condition, []).append(trace)
    return batches
