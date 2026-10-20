"""Tests for the Phase A scripted sender."""
from apart.phase_a_sender import compose_sender_message


def test_compose_sender_message_returns_action_verbatim():
    assert compose_sender_message("B") == "B"
    assert compose_sender_message("A") == "A"
