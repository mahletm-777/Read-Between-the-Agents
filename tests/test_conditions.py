"""Tests that the communication condition controls message delivery."""
import pytest
from apart.conditions import get_condition, build_message


def test_message_delivered_when_communication_enabled():
    condition = get_condition("communication_enabled")
    message = build_message("agent_a", "agent_b", "hi", condition)
    assert message.withheld is False
    assert message.receiver_id == "agent_b"
    assert message.message_type == "deliver"


def test_message_withheld_when_communication_disabled():
    condition = get_condition("communication_disabled")
    message = build_message("agent_a", "agent_b", "hi", condition)
    assert message.withheld is True
    assert message.receiver_id is None
    assert message.message_type == "withheld"


def test_unknown_condition_raises():
    with pytest.raises(ValueError):
        get_condition("not_a_real_condition")
