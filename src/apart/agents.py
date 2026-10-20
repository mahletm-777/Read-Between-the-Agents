"""A minimal deterministic mock agent policy, independent of the environment."""
from typing import Optional
from apart.models import AgentAction


class MockAgent:
    """Plays one of two roles, chosen by the caller (runner.py), not by this class:

    Sender (call choose_action with delivered_message=None): cooperate when the
    observed resource_level exceeds 5, otherwise defect. This is the same
    threshold rule used by a receiver that didn't get the sender's message.

    Receiver with a delivered message (call choose_action with
    delivered_message=<tool_name>): copies that action verbatim, ignoring its
    own observation entirely.

    The choice is expressed as an AgentAction "tool call"
    (tool_name="cooperate"/"defect"); tool_input is unused by this mock
    policy and left as an empty string.
    """

    def __init__(self, agent_id: str):
        self.agent_id = agent_id

    def choose_action(self, observation: dict, delivered_message: Optional[str] = None) -> AgentAction:
        if delivered_message is not None:
            return AgentAction(tool_name=delivered_message, tool_input="")
        resource_level = observation["resource_level"]
        tool_name = "cooperate" if resource_level > 5 else "defect"
        return AgentAction(tool_name=tool_name, tool_input="")

    def compose_message(self, action: AgentAction) -> str:
        """Announces this agent's own chosen action verbatim (its tool_name).

        Depends only on `action`, never on the delivery condition, so the
        same message is generated whether or not it ends up delivered.
        """
        return action.tool_name
