"""Tests for the two-agent environment's state transition and outcome."""
from apart.environment import TwoAgentEnvironment
from apart.models import AgentAction


def test_agents_get_independent_observations():
    env = TwoAgentEnvironment(seed=1)
    observations = env.initial_observations(["agent_a", "agent_b"])
    assert set(observations.keys()) == {"agent_a", "agent_b"}
    # Independent draws from the same seeded RNG aren't required to differ for
    # every seed, but seed=1 is known (random.Random(1).randint(0,10) twice ->
    # 2, then 9) to give different values here, confirming this isn't
    # silently returning one shared observation to both agents.
    assert observations["agent_a"] == {"resource_level": 2}
    assert observations["agent_b"] == {"resource_level": 9}


def test_same_seed_and_agent_order_gives_same_observations():
    env_a = TwoAgentEnvironment(seed=0)
    env_b = TwoAgentEnvironment(seed=0)
    assert env_a.initial_observations(["agent_a", "agent_b"]) == env_b.initial_observations(
        ["agent_a", "agent_b"]
    )


def test_mutual_cooperation_outcome():
    env = TwoAgentEnvironment(seed=0)
    actions = {
        "agent_a": AgentAction(tool_name="cooperate", tool_input=""),
        "agent_b": AgentAction(tool_name="cooperate", tool_input=""),
    }
    assert env.step(actions) == "mutual_cooperation"


def test_mutual_defection_outcome():
    env = TwoAgentEnvironment(seed=0)
    actions = {
        "agent_a": AgentAction(tool_name="defect", tool_input=""),
        "agent_b": AgentAction(tool_name="defect", tool_input=""),
    }
    assert env.step(actions) == "mutual_defection"


def test_mixed_outcome():
    env = TwoAgentEnvironment(seed=0)
    actions = {
        "agent_a": AgentAction(tool_name="cooperate", tool_input=""),
        "agent_b": AgentAction(tool_name="defect", tool_input=""),
    }
    assert env.step(actions) == "mixed"
