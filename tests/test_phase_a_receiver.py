"""Tests for the deterministic Phase A fake receiver."""
import pytest

from apart.phase_a_receiver import DeterministicFakeReceiver


def test_fake_receiver_returns_predefined_outcome():
    receiver = DeterministicFakeReceiver({("s1", "informative_message"): "B"})
    assert receiver.respond("s1", "informative_message", "B") == "B"


def test_fake_receiver_raises_on_missing_key():
    receiver = DeterministicFakeReceiver({})
    with pytest.raises(KeyError):
        receiver.respond("s1", "no_message", None)
