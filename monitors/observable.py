"""Leakage boundary for monitor views (monitor_spec.md §2; contract §9).

``ObservableRecord`` is the only object a renderer may receive. It is built solely
from the observable Trace fields: message slots, parsed actions, and payoffs.
Generator-only fields and GroundTruth fields never enter it.

``MonitorMessageSlot`` carries a message's **recording status** (so that distinct
statuses are rendered distinctly, per the approved rendering contract) but **never**
an ``EAnnotation``: the reference label must not reach a monitor prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from apart.e_label import MessageStatus
from apart.schema import DEFAULT_PAYOFFS, MONITOR_FORBIDDEN_FIELDS, Action, Trace

AGENT_LABELS = {"agent_1": "Agent 1", "agent_2": "Agent 2"}
FORBIDDEN_FIELDS = frozenset(MONITOR_FORBIDDEN_FIELDS)


@dataclass(frozen=True)
class MonitorMessageSlot:
    """One monitor-visible message slot: status + text + speaker, and no label."""

    slot_id: str
    status: MessageStatus
    text: str = ""
    sender: Optional[str] = None
    recipient: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", MessageStatus(self.status))
        if not self.slot_id:
            raise ValueError("slot_id is required")
        if self.status is MessageStatus.DELIVERED:
            if not self.text.strip():
                raise ValueError(f"{self.slot_id}: a delivered message must carry text")
            if self.sender not in AGENT_LABELS or self.recipient not in AGENT_LABELS:
                raise ValueError(f"{self.slot_id}: a delivered message needs an agent sender "
                                 "and recipient")
        elif self.status is MessageStatus.SYSTEM_NOTICE:
            if self.sender is not None:
                raise ValueError(f"{self.slot_id}: a system notice is not agent speech")
        elif self.sender is not None and self.sender not in AGENT_LABELS:
            raise ValueError(f"{self.slot_id}: unknown sender {self.sender!r}")



@dataclass(frozen=True)
class ObservableRecord:
    """Exactly what every view may see (§2)."""

    messages: tuple[tuple[str, str, str], ...]  # (sender, recipient, text) — legacy view
    actions: dict[str, Optional[str]]           # agent -> "A" | "B" | None
    payoffs: Optional[tuple[float, float]]
    slots: tuple[MonitorMessageSlot, ...] = ()  # status-aware message slots


def default_slots(trace: Trace) -> tuple[MonitorMessageSlot, ...]:
    """Derive monitor slots from a trace whose message status is unambiguous.

    ``NO_MSG`` -> ``not_sent_by_design``; ``NEUTRAL_MSG`` -> ``system_notice`` (attributed
    to the system, never to an agent); delivered message(s) -> ``delivered``; a recorded
    decline -> ``declined``. A trace with no message and no recorded decline is
    **ambiguous**, so the caller must supply slots explicitly rather than have a status
    guessed.
    """
    from apart.schema import DeliveryCondition

    delivery = trace.delivery_condition
    if delivery is DeliveryCondition.NO_MSG:
        if trace.messages:
            raise ValueError(f"{trace.trace_id}: NO_MSG trace has delivered messages")
        return (MonitorMessageSlot("m1", MessageStatus.NOT_SENT_BY_DESIGN),)
    if delivery is DeliveryCondition.NEUTRAL_MSG:
        text = trace.messages[0].text if trace.messages else ""
        return (MonitorMessageSlot("m1", MessageStatus.SYSTEM_NOTICE, text=text),)
    if trace.messages:
        return tuple(
            MonitorMessageSlot(f"m{index}", MessageStatus.DELIVERED, text=message.text,
                               sender=message.sender, recipient=message.recipient)
            for index, message in enumerate(trace.messages, start=1)
        )
    if trace.message_declined:
        return (MonitorMessageSlot("m1", MessageStatus.DECLINED),)
    raise ValueError(
        f"{trace.trace_id}: message status is ambiguous (no message and no recorded "
        "decline); pass slots explicitly"
    )


def observable(trace: Trace,
               slots: Optional[Sequence[MonitorMessageSlot]] = None) -> ObservableRecord:
    """Project a Trace onto the monitor-visible evidence.

    Reads ``messages``, ``actions()`` and ``DEFAULT_PAYOFFS`` only. Nothing in
    ``MONITOR_FORBIDDEN_FIELDS`` (trace_id, seed, delivery_condition, claims,
    template_ids, private_plans, …) is read or copied.
    """
    parsed = trace.actions()
    actions = {
        agent: (parsed[agent].value if parsed.get(agent) is not None else None)
        for agent in ("agent_1", "agent_2")
    }
    payoffs = None
    if actions["agent_1"] is not None and actions["agent_2"] is not None:
        payoffs = DEFAULT_PAYOFFS[(Action(actions["agent_1"]), Action(actions["agent_2"]))]
    return ObservableRecord(
        messages=tuple((m.sender, m.recipient, m.text) for m in trace.messages),
        actions=actions,
        payoffs=payoffs,
        slots=tuple(slots) if slots is not None else default_slots(trace),
    )

