"""Protected storage wrapper for Phase A trial records. Reuses
`apart.storage`'s generic JSONL save/load (already generic over any
dataclass via `dataclasses.asdict`) but adds two safeguards the legacy
module does not have:

1. The target path is resolved (`os.path.realpath`, which also resolves
   symlinks and relative/`..` segments) BEFORE being compared against the
   legacy results path, so an indirect path to the same file is caught,
   not just a literal string match.
2. An existing Phase A results file is never silently overwritten --
   `overwrite=True` must be passed explicitly.
"""
import os
from typing import List

from apart import storage
from apart.phase_a_models import PhaseATrialResult

DEFAULT_PHASE_A_RESULTS_PATH = "results/phase_a_trials.jsonl"
_LEGACY_RESULTS_PATH = "results/raw_trials.jsonl"


def save_phase_a_trials(
    trials: List[PhaseATrialResult],
    path: str = DEFAULT_PHASE_A_RESULTS_PATH,
    overwrite: bool = False,
) -> None:
    resolved = os.path.realpath(path)
    legacy_resolved = os.path.realpath(_LEGACY_RESULTS_PATH)
    if resolved == legacy_resolved:
        raise ValueError(
            f"Refusing to write Phase A records to the legacy results path ({_LEGACY_RESULTS_PATH})."
        )
    if os.path.exists(resolved) and not overwrite:
        raise FileExistsError(f"{path} already exists; pass overwrite=True to replace it.")
    storage.save_trials(trials, path)


def load_phase_a_trials(path: str = DEFAULT_PHASE_A_RESULTS_PATH) -> list:
    return storage.load_trials(path)
