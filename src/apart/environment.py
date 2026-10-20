"""A minimal two-agent environment for one complete trial.

Actions are SIMULTANEOUS: agents choose an action without seeing the other
agent's choice first. There is exactly one round per trial (no multi-turn
interaction yet).
"""
import random
from typing import Sequence
from apart.models import AgentAction


class TwoAgentEnvironment:
    def __init__(self, seed: int):
        self._rng = random.Random(seed)

    def initial_observations(self, agent_ids: Sequence[str]) -> dict:
        """Returns one independent resource_level observation per agent id.

        Draws happen in `agent_ids` order from this environment's seeded RNG,
        so the same seed and the same agent_id order always reproduce the
        same per-agent observations.
        """
        return {agent_id: {"resource_level": self._rng.randint(0, 10)} for agent_id in agent_ids}

    def step(self, actions: dict[str, AgentAction]) -> str:
        """Applies both agents' actions (agent_id -> AgentAction) and
        returns the trial outcome label."""
        tool_names = {action.tool_name for action in actions.values()}
        if tool_names == {"cooperate"}:
            return "mutual_cooperation"
        if tool_names == {"defect"}:
            return "mutual_defection"
        return "mixed"
