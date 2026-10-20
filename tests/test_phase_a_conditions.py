"""Tests for Phase A (No Message / Neutral Message / Informative Message)
condition semantics."""
import pytest

from apart.phase_a_conditions import (
    INFORMATIVE_MESSAGE,
    NEUTRAL_MESSAGE,
    NO_MESSAGE,
    PHASE_A_CONDITIONS,
    build_phase_a_message,
    get_phase_a_condition,
)
from apart.phase_a_parser import parse_receiver_response


def test_phase_a_conditions_are_no_neutral_informative():
    assert PHASE_A_CONDITIONS == ("no_message", "neutral_message", "informative_message")


def test_no_message_withholds_message():
    condition = get_phase_a_condition(NO_MESSAGE)
    message = build_phase_a_message("B", condition)
    assert message.delivered is False
    assert message.kind == "withheld"


def test_neutral_message_delivers_without_leaking_action():
    condition = get_phase_a_condition(NEUTRAL_MESSAGE)
    message = build_phase_a_message("B", condition)
    assert message.delivered is True
    assert message.kind == "neutral"
    assert "B" not in message.content
    assert "A" not in message.content
    # Belt-and-suspenders: the neutral message content should not even
    # parse as an action under the Phase A parser's own token rules.
    parsed = parse_receiver_response(message.content)
    assert parsed.action is None


def test_informative_message_delivers_message_with_action():
    condition = get_phase_a_condition(INFORMATIVE_MESSAGE)
    message = build_phase_a_message("B", condition)
    assert message.delivered is True
    assert message.kind == "informative"
    assert message.content == "B"


def test_unknown_phase_a_condition_raises():
    with pytest.raises(ValueError):
        get_phase_a_condition("not_a_real_condition")
