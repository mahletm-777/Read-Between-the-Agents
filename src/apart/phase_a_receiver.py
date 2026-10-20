"""A deterministic fake receiver for exercising the Phase A pipeline
without any real model call. This is a test fixture only -- it is not a
model of real receiver behavior, "behavioral independence," or anything
empirical. A real receiver (a `ModelClient`-backed policy) remains out of
scope for this offline task.
"""
from typing import Dict, Optional, Tuple


class DeterministicFakeReceiver:
    """Returns a predefined raw output for each (scenario_id, condition_name)
    pair, supplied at construction. Never guesses: a missing key is an
    error, not a silent default, so every trial in a test is explicit
    about what the fake receiver "said."
    """

    def __init__(self, outcomes: Dict[Tuple[str, str], str]):
        self._outcomes = dict(outcomes)

    def respond(self, scenario_id: str, condition_name: str, delivered_message: Optional[str]) -> str:
        key = (scenario_id, condition_name)
        if key not in self._outcomes:
            raise KeyError(
                f"No predefined outcome for scenario_id={scenario_id!r}, "
                f"condition={condition_name!r}; the fake receiver never guesses."
            )
        return self._outcomes[key]
