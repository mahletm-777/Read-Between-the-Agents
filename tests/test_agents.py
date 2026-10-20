"""Tests for the mock agent's action-selection and message rules."""
from apart.agents import MockAgent
from apart.models import AgentAction


def test_cooperates_above_threshold():
    agent = MockAgent("agent_a")
    action = agent.choose_action({"resource_level": 6})
    assert action.tool_name == "cooperate"


def test_defects_at_or_below_threshold():
    agent = MockAgent("agent_a")
    action = agent.choose_action({"resource_level": 5})
    assert action.tool_name == "defect"


def test_receiver_copies_delivered_message_regardless_of_own_observation():
    agent = MockAgent("agent_b")
    # Own observation would normally mean "cooperate" (resource_level > 5),
    # but a delivered message should override it completely.
    action = agent.choose_action({"resource_level": 9}, delivered_message="defect")
    assert action.tool_name == "defect"


def test_no_delivered_message_falls_back_to_threshold_rule():
    agent = MockAgent("agent_b")
    action = agent.choose_action({"resource_level": 5}, delivered_message=None)
    assert action.tool_name == "defect"


def test_compose_message_announces_own_action():
    agent = MockAgent("agent_a")
    action = AgentAction(tool_name="cooperate", tool_input="")
    assert agent.compose_message(action) == "cooperate"
