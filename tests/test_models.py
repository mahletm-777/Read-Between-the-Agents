"""Basic construction and field-access tests for the core data types."""
from apart.models import AgentAction, AgentMessage, TrialMetadata, TrialResult


def test_agent_action_fields():
    action = AgentAction(tool_name="cooperate", tool_input="")
    assert action.tool_name == "cooperate"
    assert action.tool_input == ""


def test_agent_message_fields():
    message = AgentMessage(
        sender_id="agent_a",
        receiver_id="agent_b",
        withheld=False,
        message_content="hi",
        message_type="deliver",
    )
    assert message.receiver_id == "agent_b"
    assert message.withheld is False


def test_trial_result_defaults_to_empty_containers():
    metadata = TrialMetadata(trial_id="t1", condition="communication_enabled", seed=0)
    result = TrialResult(metadata=metadata)
    assert result.actions == {}
    assert result.messages == []
    assert result.error is None
