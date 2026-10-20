"""Tests for trial orchestration: structure, error handling, and the
communication-sensitive positive control (sender announces its action;
receiver copies it if delivered, else falls back to its own threshold rule).

Seed fixtures below are pinned to known random.Random(seed).randint(0, 10)
draws (verified locally, not re-derived at test time):
  seed=0: agent_a=6 (cooperate), agent_b=6 (cooperate)  -- both agree anyway
  seed=1: agent_a=2 (defect),    agent_b=9 (cooperate)  -- they disagree
  seed=6: agent_a=9 (cooperate), agent_b=1 (defect)     -- disagree, mirrored
"""
from apart.runner import run_trial


def test_normal_trial_is_structured_correctly():
    result = run_trial("communication_enabled", seed=0)

    assert result.error is None
    assert result.metadata.condition == "communication_enabled"
    assert result.metadata.seed == 0
    assert result.metadata.trial_id == "communication_enabled_seed0"
    assert set(result.actions.keys()) == {"agent_a", "agent_b"}
    assert result.outcome in {"mutual_cooperation", "mutual_defection", "mixed"}
    assert len(result.messages) == 1


def test_unknown_condition_is_recorded_as_error_not_raised():
    # get_condition raises ValueError for an unknown condition name; this
    # exercises run_trial's try/except without needing to fake a failure.
    result = run_trial("not_a_real_condition", seed=0)

    assert result.error is not None
    assert "not_a_real_condition" in result.error
    assert result.outcome is None
    assert result.actions == {}
    assert result.messages == []


def test_message_delivered_when_communication_enabled():
    result = run_trial("communication_enabled", seed=1)
    message = result.messages[0]

    assert message.withheld is False
    assert message.message_type == "deliver"
    assert message.sender_id == "agent_a"
    assert message.receiver_id == "agent_b"


def test_message_withheld_when_communication_disabled():
    result = run_trial("communication_disabled", seed=1)
    message = result.messages[0]

    assert message.withheld is True
    assert message.message_type == "withheld"
    assert message.receiver_id is None


def test_message_content_is_the_same_regardless_of_delivery():
    """The sender's announced content must not depend on the delivery
    condition -- requirement 1/4 of the positive control."""
    enabled = run_trial("communication_enabled", seed=1)
    disabled = run_trial("communication_disabled", seed=1)
    assert enabled.messages[0].message_content == disabled.messages[0].message_content
    assert enabled.actions["agent_a"] == disabled.actions["agent_a"]


def test_positive_control_receiver_copies_sender_when_delivered():
    """Across seeds where the sender and receiver's own threshold rules
    would disagree, delivery must make the receiver copy the sender exactly."""
    for seed in (1, 6):
        result = run_trial("communication_enabled", seed=seed)
        assert result.actions["agent_b"].tool_name == result.actions["agent_a"].tool_name


def test_withholding_isolation_receiver_ignores_sender_when_withheld():
    """When withheld, the receiver's action must match its OWN threshold
    rule, not the sender's -- proving the sender's action cannot leak
    through when the message isn't delivered."""
    # seed=1: sender(agent_a)=defect, receiver(agent_b)'s own rule=cooperate.
    result = run_trial("communication_disabled", seed=1)
    assert result.actions["agent_b"].tool_name == "cooperate"
    assert result.actions["agent_a"].tool_name == "defect"
    assert result.actions["agent_b"].tool_name != result.actions["agent_a"].tool_name

    # seed=6: mirrored -- sender=cooperate, receiver's own rule=defect.
    result = run_trial("communication_disabled", seed=6)
    assert result.actions["agent_b"].tool_name == "defect"
    assert result.actions["agent_a"].tool_name == "cooperate"
    assert result.actions["agent_b"].tool_name != result.actions["agent_a"].tool_name


def test_same_seed_and_condition_gives_same_trial():
    """Reproducibility: identical seed + condition must give identical
    observations, actions, and messages."""
    first = run_trial("communication_enabled", seed=1)
    second = run_trial("communication_enabled", seed=1)
    assert first.actions == second.actions
    assert first.messages == second.messages
    assert first.outcome == second.outcome


def test_direct_same_seed_cross_condition_positive_control():
    """Single direct comparison, both conditions run with identical exogenous
    inputs (same seed): sender is invariant, receiver's action differs, and
    the withheld message cannot reach the receiver -- all asserted together
    in one test, rather than split across the separate tests above."""
    seed = 1  # agent_a (sender) = defect, agent_b (receiver)'s own rule = cooperate.
    enabled = run_trial("communication_enabled", seed=seed)
    disabled = run_trial("communication_disabled", seed=seed)

    # Identical exogenous inputs: same seed produced the same sender action
    # and the same announced message content in both conditions.
    assert enabled.actions["agent_a"] == disabled.actions["agent_a"]
    assert enabled.messages[0].message_content == disabled.messages[0].message_content

    # The positive control: the receiver's action differs purely because of
    # delivery, not because of any difference in exogenous inputs.
    assert enabled.actions["agent_b"].tool_name != disabled.actions["agent_b"].tool_name
    assert enabled.actions["agent_b"].tool_name == "defect"  # copied the sender's action
    assert disabled.actions["agent_b"].tool_name == "cooperate"  # receiver's own threshold rule

    # The withheld message cannot reach the receiver: it's marked withheld,
    # has no receiver_id, and the receiver's resulting action does not match
    # the content that would have been announced.
    assert disabled.messages[0].withheld is True
    assert disabled.messages[0].receiver_id is None
    assert disabled.actions["agent_b"].tool_name != disabled.messages[0].message_content
