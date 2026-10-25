"""Orchestrates Phase A (No Message / Neutral Message / Informative
Message) trials. Mirrors the pairing pattern
already used in `apart.exhaustive` (one trial per condition per pairing
key, with pairing completeness verified), but keyed by `scenario_id`
instead of a seed-derived pair index, and driven by a caller-supplied
receiver (e.g. `apart.phase_a_receiver.DeterministicFakeReceiver` for
offline testing). A real LLM-backed receiver is out of scope here.
"""
from typing import Callable, Dict, List, Optional

from apart.phase_a_conditions import PHASE_A_CONDITIONS, build_phase_a_message, get_phase_a_condition
from apart.phase_a_models import PhaseATrialMetadata, PhaseATrialResult
from apart.phase_a_parser import parse_receiver_response
from apart.phase_a_sender import compose_sender_message


def run_phase_a_trial(
    *,
    scenario_id: str,
    condition_name: str,
    sender_action: str,
    receiver_fn: Callable[[str, str, Optional[str]], str],
) -> PhaseATrialResult:
    """Runs one Phase A trial for one scenario under one condition.

    `receiver_fn(scenario_id, condition_name, delivered_message) -> raw_output`
    is caller-supplied; any exception it raises is caught and recorded in
    `.error`, never silently treated as a successful trial.

    Fail-closed and stage-tagged: every returned result carries an explicit
    outcome. When a trial fails, `.error_stage` records which step raised
    ("condition", "message" or "receiver"), so per-condition exclusion
    accounting does not misattribute a configuration problem to the receiver.
    """
    trial_id = f"{scenario_id}_{condition_name}"
    metadata = PhaseATrialMetadata(scenario_id=scenario_id, condition=condition_name, trial_id=trial_id)
    stage = "condition"
    try:
        condition = get_phase_a_condition(condition_name)
        stage = "message"
        announced_content = compose_sender_message(sender_action)
        message = build_phase_a_message(announced_content, condition)
        delivered_message = message.content if message.delivered else None

        stage = "receiver"
        raw_output = receiver_fn(scenario_id, condition_name, delivered_message)
        parsed = parse_receiver_response(raw_output)
    except Exception as exc:
        return PhaseATrialResult(
            metadata=metadata,
            sender_action=sender_action,
            message_content="",
            message_delivered=False,
            error=str(exc),
            error_stage=stage,
        )

    return PhaseATrialResult(
        metadata=metadata,
        sender_action=sender_action,
        message_content=message.content,
        message_delivered=message.delivered,
        receiver_raw_output=raw_output,
        parsed_action=parsed.action,
        validity=parsed.validity,
        reason=parsed.reason,
    )


def run_phase_a_trials_for_scenario(
    *,
    scenario_id: str,
    sender_action: str,
    receiver_fn: Callable[[str, str, Optional[str]], str],
) -> List[PhaseATrialResult]:
    return [
        run_phase_a_trial(
            scenario_id=scenario_id,
            condition_name=condition_name,
            sender_action=sender_action,
            receiver_fn=receiver_fn,
        )
        for condition_name in PHASE_A_CONDITIONS
    ]


def group_by_scenario(trials: List[PhaseATrialResult]) -> Dict[str, Dict[str, PhaseATrialResult]]:
    """Groups trials by scenario_id, keyed by condition within each
    scenario. Raises if any scenario is missing a condition's trial --
    mirrors `apart.exhaustive._group_by_pair`'s pairing-completeness
    check."""
    by_scenario: Dict[str, Dict[str, PhaseATrialResult]] = {}
    for t in trials:
        by_scenario.setdefault(t.metadata.scenario_id, {})[t.metadata.condition] = t
    for scenario_id, by_condition in by_scenario.items():
        if set(by_condition.keys()) != set(PHASE_A_CONDITIONS):
            raise ValueError(f"Scenario {scenario_id} is missing one condition's trial.")
    return by_scenario
