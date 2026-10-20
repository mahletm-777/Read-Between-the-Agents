from dataclasses import dataclass
from apart.conditions import ALL_CONDITIONS

# The dataclasses module provides a decorator and functions to automatically
# generate special methods like __init__(), __repr__(), and __eq__() for user-defined classes.

@dataclass(frozen=True)
class ExperimentConfig:

    model_name: str = "approved-model"

    num_agents: int = 2

    num_seeds: int = 3

    max_turns: int = 10

    condition: str = "communication_enabled"

    conditions: tuple = ALL_CONDITIONS

    output_dir: str = "results"

    def __post_init__(self):
        # frozen=True blocks attribute assignment, but raising here still
        # works: it just stops construction before a bad config is returned.
        if self.num_seeds <= 0:
            raise ValueError("num_seeds must be positive")
        if self.condition not in self.conditions:
            raise ValueError(f"condition {self.condition!r} not in conditions {self.conditions}")
