"""Leakage boundary for monitor views (monitor_spec.md §2).

``ObservableRecord`` is the only object a renderer may receive. It is built
solely from the observable Trace fields: messages, parsed actions, and payoffs.
Generator-only fields and GroundTruth fields never enter it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from apart.schema import DEFAULT_PAYOFFS, MONITOR_FORBIDDEN_FIELDS, Action, Trace

AGENT_LABELS = {"agent_1": "Agent 1", "agent_2": "Agent 2"}
FORBIDDEN_FIELDS = frozenset(MONITOR_FORBIDDEN_FIELDS)


@dataclass(frozen=True)
class ObservableRecord:
    """Exactly what every view may see (§2)."""

    messages: tuple[tuple[str, str, str], ...]  # (sender, recipient, text)
    actions: dict[str, Optional[str]]           # agent -> "A" | "B" | None
    payoffs: Optional[tuple[float, float]]


def observable(trace: Trace) -> ObservableRecord:
    """Project a Trace onto the monitor-visible evidence.

    Reads ``messages``, ``actions()`` and ``DEFAULT_PAYOFFS`` only. Nothing in
    ``MONITOR_FORBIDDEN_FIELDS`` (trace_id, seed, delivery_condition, claims,
    template_ids, private_plans) is read or copied.
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
    )
