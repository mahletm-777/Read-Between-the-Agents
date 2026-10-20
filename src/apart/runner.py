"""Executes trials across conditions and seeds. Trial execution lives here;
agent policy (agents.py) and the environment (environment.py) stay unaware
of each other and of how they are being driven.

Roles are fixed and asymmetric, not symmetric peers:
- `agent_a` is the sender: it picks its own action from its own observation
  (the threshold rule) and announces that action in a message. Both of
  these happen before the delivery condition is applied, so the sender's
  action and announcement never depend on whether the message is delivered.
- `agent_b` is the receiver: if the message is delivered, it copies the
  sender's announced action verbatim; if withheld, it falls back to the
  same threshold rule applied to its own (independently drawn) observation.
"""
from apart.agents import MockAgent
from apart.conditions import get_condition, build_message
from apart.environment import TwoAgentEnvironment
from apart.models import TrialMetadata, TrialResult


def run_trial_from_observations(
    *,
    condition_name: str,
    observations: dict,
    env: TwoAgentEnvironment,
    trial_id: str,
    seed: int,
) -> TrialResult:
    """Runs one trial given already-determined per-agent observations.

    Shared by `run_trial` (which draws `observations` from a seeded
    environment) and `apart.exhaustive`'s exhaustive enumeration (which
    injects every possible (resource_level_a, resource_level_b) pair
    directly, bypassing the RNG entirely). `env` is used only for its
    `step()` outcome-labeling logic here -- it does not redraw observations.

    Any exception is caught and recorded in TrialResult.error instead of
    crashing the whole run or being silently treated as a successful trial.
    """
    metadata = TrialMetadata(trial_id=trial_id, condition=condition_name, seed=seed)
    try:
        condition = get_condition(condition_name)
        sender = MockAgent("agent_a")
        receiver = MockAgent("agent_b")

        # Sender's action and announced message are both independent of the
        # delivery condition: computed before `build_message` ever sees `condition`.
        sender_action = sender.choose_action(observations[sender.agent_id])
        announced_content = sender.compose_message(sender_action)
        message = build_message(sender.agent_id, receiver.agent_id, announced_content, condition)

        # The receiver only ever sees the announced content if it was delivered;
        # when withheld, delivered_message is None and the receiver cannot reach
        # message.message_content at all.
        delivered_message = None if message.withheld else message.message_content
        receiver_action = receiver.choose_action(
            observations[receiver.agent_id], delivered_message=delivered_message
        )

        actions = {sender.agent_id: sender_action, receiver.agent_id: receiver_action}
        outcome = env.step(actions)
        return TrialResult(metadata=metadata, actions=actions, messages=[message], outcome=outcome)
    except Exception as exc:
        return TrialResult(metadata=metadata, error=str(exc))


def run_trial(condition_name: str, seed: int) -> TrialResult:
    """Runs one complete trial and returns its result.

    Any exception is caught and recorded in TrialResult.error instead of
    crashing the whole run or being silently treated as a successful trial.
    """
    trial_id = f"{condition_name}_seed{seed}"
    env = TwoAgentEnvironment(seed=seed)
    observations = env.initial_observations(["agent_a", "agent_b"])
    return run_trial_from_observations(
        condition_name=condition_name,
        observations=observations,
        env=env,
        trial_id=trial_id,
        seed=seed,
    )


def run_trials(condition_names: list[str], seeds: list[int]) -> list[TrialResult]:
    return [run_trial(condition_name, seed) for condition_name in condition_names for seed in seeds]
