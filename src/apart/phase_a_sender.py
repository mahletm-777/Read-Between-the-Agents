"""Scripted sender for Phase A: deterministic, non-LLM, and content-free.

Scenario population and sender-action selection rules are out of scope
for this offline task, so this module does not decide *which* action a
sender picks -- it only composes the announced message from a
caller-supplied action, mirroring `apart.agents.MockAgent.compose_message`
(the action's own label), decoupled from `MockAgent`'s
resource-level-based `choose_action` since Phase A has no resource_level
scenarios.
"""


def compose_sender_message(sender_action: str) -> str:
    """Returns the sender's announced content: its own action label,
    verbatim. Does not depend on the delivery condition."""
    return sender_action
