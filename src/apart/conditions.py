"""Communication conditions: whether messages are withheld or delivered.

This is the single place that decides message delivery. Nothing else about
a trial (environment, agent policy, seeds) changes between conditions.
"""
from dataclasses import dataclass
from apart.models import AgentMessage

COMMUNICATION_ENABLED = "communication_enabled"
COMMUNICATION_DISABLED = "communication_disabled"
ALL_CONDITIONS = (COMMUNICATION_ENABLED, COMMUNICATION_DISABLED)


@dataclass(frozen=True)
class Condition:
    name: str
    communication_enabled: bool


_CONDITIONS = {
    COMMUNICATION_ENABLED: Condition(COMMUNICATION_ENABLED, communication_enabled=True),
    COMMUNICATION_DISABLED: Condition(COMMUNICATION_DISABLED, communication_enabled=False),
}


def get_condition(name: str) -> Condition:
    if name not in _CONDITIONS:
        raise ValueError(f"Unknown condition: {name!r}. Expected one of {ALL_CONDITIONS}.")
    return _CONDITIONS[name]


def build_message(sender_id: str, receiver_id: str, content: str, condition: Condition) -> AgentMessage:
    """Builds a message whose withheld/receiver_id/message_type reflect this
    condition. The message is always recorded (never silently dropped) so
    withheld attempts stay visible in the trial record."""
    if condition.communication_enabled:
        return AgentMessage(
            sender_id=sender_id,
            receiver_id=receiver_id,
            withheld=False,
            message_content=content,
            message_type="deliver",
        )
    return AgentMessage(
        sender_id=sender_id,
        receiver_id=None,
        withheld=True,
        message_content=content,
        message_type="withheld",
    )
