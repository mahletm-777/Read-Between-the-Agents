"""Phase A communication conditions: No Message, Neutral Message,
Informative Message.

- **No Message** (`NO_MESSAGE`): the receiver does not receive a message
  from the sender.
- **Neutral Message** (`NEUTRAL_MESSAGE`): the receiver receives a message
  designed not to reveal the sender's action. This is a design intent, not
  a claim that the message carries no information at all -- it may still
  convey other information through wording, tone, context, or pragmatic
  cues.
- **Informative Message** (`INFORMATIVE_MESSAGE`): the receiver receives a
  message designed to communicate the sender's action.

This is a separate, additive registry for the proposed three-condition
experiment. It does not touch `apart.conditions`, which remains the
legacy two-condition (`communication_enabled`/`communication_disabled`)
registry used by the existing deterministic pipeline (`apart.runner`,
`apart.exhaustive`). Nothing here is wired into that module's
`ALL_CONDITIONS`/`_CONDITIONS`, so the legacy 242-trial exhaustive
baseline is unaffected.

Scenario content and the final informative-message wording are out of
scope for this offline task; the informative condition's content is just
the sender's action label, mirroring the existing
`MockAgent.compose_message` pattern (the action's own name), not a
natural-language message.
"""
from dataclasses import dataclass
from typing import Literal

NO_MESSAGE = "no_message"
NEUTRAL_MESSAGE = "neutral_message"
INFORMATIVE_MESSAGE = "informative_message"
PHASE_A_CONDITIONS = (NO_MESSAGE, NEUTRAL_MESSAGE, INFORMATIVE_MESSAGE)

_NEUTRAL_MESSAGE_CONTENT = (
    "[neutral message: designed not to reveal the sender's action; this "
    "placeholder content is not evidence that a real neutral message "
    "conveys no information]"
)


@dataclass(frozen=True)
class PhaseACondition:
    name: str
    message_kind: Literal["withheld", "neutral", "informative"]


_PHASE_A_CONDITIONS = {
    NO_MESSAGE: PhaseACondition(NO_MESSAGE, message_kind="withheld"),
    NEUTRAL_MESSAGE: PhaseACondition(NEUTRAL_MESSAGE, message_kind="neutral"),
    INFORMATIVE_MESSAGE: PhaseACondition(INFORMATIVE_MESSAGE, message_kind="informative"),
}


def get_phase_a_condition(name: str) -> PhaseACondition:
    if name not in _PHASE_A_CONDITIONS:
        raise ValueError(f"Unknown Phase A condition: {name!r}. Expected one of {PHASE_A_CONDITIONS}.")
    return _PHASE_A_CONDITIONS[name]


@dataclass(frozen=True)
class PhaseAMessage:
    content: str
    delivered: bool
    kind: Literal["withheld", "neutral", "informative"]


def build_phase_a_message(sender_action: str, condition: PhaseACondition) -> PhaseAMessage:
    """Builds the message delivered (or withheld) to the receiver under a
    Phase A condition. `sender_action` is the sender's actual chosen
    action, supplied by the caller -- this function does not derive it
    from any scenario content, since scenario population is out of scope
    for this offline task.
    """
    if condition.message_kind == "withheld":
        return PhaseAMessage(content="", delivered=False, kind="withheld")
    if condition.message_kind == "neutral":
        return PhaseAMessage(content=_NEUTRAL_MESSAGE_CONTENT, delivered=True, kind="neutral")
    if condition.message_kind == "informative":
        return PhaseAMessage(content=sender_action, delivered=True, kind="informative")
    raise ValueError(f"Unknown message_kind: {condition.message_kind!r}")
