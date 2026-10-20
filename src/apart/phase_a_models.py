"""Phase A trial data types: scenario-identified trials for the proposed
No Message / Neutral Message / Informative Message experiment. Kept
separate from `apart.models`'s `TrialMetadata`/
`TrialResult` (the legacy seed-based schema) rather than extending them,
so legacy serialized records and legacy tests are unaffected.

`scenario_id` is a new, explicit field -- the legacy `seed` field is never
reused as a pairing identifier here (it is already repurposed as a
deterministic pair index in `apart.exhaustive`, for an unrelated
experiment).
"""
from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True)
class PhaseATrialMetadata:
    scenario_id: str
    condition: str
    trial_id: str


@dataclass
class PhaseATrialResult:
    metadata: PhaseATrialMetadata
    sender_action: str
    message_content: str
    message_delivered: bool
    receiver_raw_output: Optional[str] = None
    parsed_action: Optional[str] = None
    validity: Optional[Literal["valid", "ambiguous", "invalid"]] = None
    reason: Optional[str] = None
    error: Optional[str] = None
