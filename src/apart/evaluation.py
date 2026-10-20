"""Outcome-rate metric: fraction of completed trials matching a given outcome.

Kept separate from trial execution (runner.py) so the metric definition can
be read, tested, and changed without touching how trials are run.
"""

DEFAULT_OUTCOME = "mutual_cooperation"


def outcome_rate(trials: list[dict], outcome: str = DEFAULT_OUTCOME) -> float:
    """Fraction of completed (error-free) trials whose outcome == `outcome`.

    Trials with a recorded error are excluded from numerator and
    denominator, since they never produced a real outcome. Raises
    ValueError on zero completed trials rather than returning 0.0, which
    would be indistinguishable from "zero cooperation observed".
    """
    completed = [t for t in trials if t.get("error") is None]
    if not completed:
        raise ValueError("No completed trials to compute an outcome rate from.")
    matches = sum(1 for t in completed if t.get("outcome") == outcome)
    return matches / len(completed)


def outcome_rate_by_condition(trials: list[dict], outcome: str = DEFAULT_OUTCOME) -> dict:
    """Returns {condition: outcome_rate} for each condition present in trials.

    A condition whose trials all errored (zero completed) maps to None
    instead of raising, so one all-errored condition doesn't prevent
    reporting the others.
    """
    conditions = {t["metadata"]["condition"] for t in trials}
    rates = {}
    for condition in conditions:
        subset = [t for t in trials if t["metadata"]["condition"] == condition]
        try:
            rates[condition] = outcome_rate(subset, outcome)
        except ValueError:
            rates[condition] = None
    return rates


# Action mapping for the communication-sensitive positive control: cooperate = B
# (defect = A). This is a fixed project decision, not something these functions
# infer from the data.
B_ACTION = "cooperate"


def _is_delivered(trial: dict) -> bool:
    """Reads delivery status from the trial's own recorded message, not from
    the condition name, so these functions don't assume what a condition
    label implies."""
    messages = trial.get("messages") or []
    if not messages:
        raise ValueError("Trial has no recorded message; cannot determine delivery status.")
    return not bool(messages[0].get("withheld"))


def _agent_chose_b(trial: dict, agent_id: str) -> bool:
    actions = trial.get("actions") or {}
    action = actions.get(agent_id) or {}
    return action.get("tool_name") == B_ACTION


def tau_y(trials: list[dict], *, sender_id: str = "agent_a", receiver_id: str = "agent_b") -> float:
    """P(both choose B | delivered) - P(both choose B | withheld).

    Only completed (error-free) trials are used. Raises ValueError if either
    delivery condition has zero completed trials.
    """
    completed = [t for t in trials if t.get("error") is None]
    delivered = [t for t in completed if _is_delivered(t)]
    withheld = [t for t in completed if not _is_delivered(t)]
    if not delivered or not withheld:
        raise ValueError("Need at least one completed trial in each delivery condition to compute tau_Y.")

    def both_b(t: dict) -> bool:
        return _agent_chose_b(t, sender_id) and _agent_chose_b(t, receiver_id)

    p_delivered = sum(1 for t in delivered if both_b(t)) / len(delivered)
    p_withheld = sum(1 for t in withheld if both_b(t)) / len(withheld)
    return p_delivered - p_withheld


def tau_r(trials: list[dict], *, receiver_id: str = "agent_b") -> float:
    """P(receiver chooses B | delivered) - P(receiver chooses B | withheld).

    Only completed (error-free) trials are used. Raises ValueError if either
    delivery condition has zero completed trials.
    """
    completed = [t for t in trials if t.get("error") is None]
    delivered = [t for t in completed if _is_delivered(t)]
    withheld = [t for t in completed if not _is_delivered(t)]
    if not delivered or not withheld:
        raise ValueError("Need at least one completed trial in each delivery condition to compute tau_R.")

    p_delivered = sum(1 for t in delivered if _agent_chose_b(t, receiver_id)) / len(delivered)
    p_withheld = sum(1 for t in withheld if _agent_chose_b(t, receiver_id)) / len(withheld)
    return p_delivered - p_withheld
