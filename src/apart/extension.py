"""Extension budget and monitored-set selection (extension_spec.md §16.9.4, §16.9.5).

Pure functions; no network and no model calls. Used by ``scripts/generate_extension.py``
and by the tests.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

from apart.labeler import TrajectoryClass

# §7.1 class quotas (2× the pilot mix); §15.4 cell targets nest inside them.
CLASS_QUOTAS = {"C-01": 22, "C-02": 18, "C-03": 12, "C-04": 16, "C-05": 12}

# cell key = (attempt_type, outcome); outcome ∈ {"A/A", "one-B", "B/B"}.
CELL_TARGETS = {
    "C-01": {("NONE", "A/A"): 22},
    "C-02": {("NONE", "one-B"): 18},
    "C-03": {("EXPLICIT", "B/B"): 3, ("IMPLICIT", "B/B"): 3, ("AMBIG", "B/B"): 3, ("SOLO", "B/B"): 3},
    "C-04": {("NONE", "B/B"): 16},
    "C-05": {
        ("EXPLICIT", "A/A"): 2, ("IMPLICIT", "A/A"): 2, ("AMBIG", "A/A"): 2, ("SOLO", "A/A"): 2,
        ("EXPLICIT", "one-B"): 1, ("IMPLICIT", "one-B"): 1, ("AMBIG", "one-B"): 1, ("SOLO", "one-B"): 1,
    },
}

RESERVE_CAP_USD = 30.0
AGENT_EXTRACTION_CAP_USD = 3.0
SAFETY_FACTOR = 1.10
MAX_N = 80


# ---------------------------------------------------------------- budget (§16.9.5)

def compute_c_set(per_trace: Iterable[dict]) -> Optional[float]:
    """Synthetic per-trace helper for tests and demos; NOT the generation path.

    The authoritative C_set is ``monitors.ledger.c_set_micro`` (committed ledger
    events, de-duplicated score records, conservative unattributed spend). This
    dollar-row helper is kept only for the synthetic unit test.
    """
    rows = list(per_trace)
    if not rows:
        return None
    total_spend = sum(r.get("m3_spend", 0.0) + r.get("m4_spend", 0.0) + r.get("m3ens_spend", 0.0) for r in rows)
    complete = sum(
        1 for r in rows if r.get("m3_valid") and r.get("m4_valid") and r.get("m3ens_valid")
    )
    if complete == 0:
        return None
    return total_spend / complete


def compute_n(
    c_set: Optional[float],
    gate_spend: float,
    bridge_spend: float,
    reserve_cap: float = RESERVE_CAP_USD,
    agent_cap: float = AGENT_EXTRACTION_CAP_USD,
    safety: float = SAFETY_FACTOR,
) -> int:
    """§16.9.5: N = min(80, largest even N with N × 1.10 × C_set ≤ 30 − gate − bridge − 3)."""
    if c_set is None or c_set <= 0:
        return 0
    budget = reserve_cap - gate_spend - bridge_spend - agent_cap
    if budget <= 0:
        return 0
    for n in range(MAX_N, 0, -2):
        if n * safety * c_set <= budget + 1e-12:
            return n
    return 0


def require_c_set(c_set: Optional[float]) -> float:
    """§16.9.5: generation must not begin until a measured C_set is supplied."""
    if c_set is None or c_set <= 0:
        raise ValueError("no measured C_set: run the gate first to fix C_set and N")
    return c_set


# ---------------------------------------------------------------- selection (§16.9.4)

def outcome_code(num_b: int) -> str:
    if num_b == 0:
        return "A/A"
    if num_b == 1:
        return "one-B"
    return "B/B"


def normalized_text(s: Optional[str]) -> str:
    """Whitespace-normalise message text for the duplicate rule (§7.2)."""
    return " ".join((s or "").split())


def observable_key(messages, raw_actions) -> tuple:
    """The observable record used for exact-duplicate detection (§7.2)."""
    return (
        tuple(normalized_text(m["text"]) for m in messages),
        tuple(sorted(raw_actions.items())),
    )


def select_monitored_set(
    traces: Iterable[dict],
    class_quotas: Optional[dict] = None,
    cell_targets: Optional[dict] = None,
) -> dict:
    """Class quotas primary, cell quotas secondary, lowest-seed first, never reclassify.

    Each trace dict needs: ``seed``, ``class`` (historical label, "C-01".."C-05"),
    ``attempt_type``, and ``outcome``. Returns selected seeds and a shortfall report.
    """
    class_quotas = dict(class_quotas or CLASS_QUOTAS)
    cell_targets = cell_targets if cell_targets is not None else CELL_TARGETS

    traces = sorted(traces, key=lambda t: (t["seed"], t["class"]))
    by_cell = defaultdict(list)
    for t in traces:
        by_cell[(t["class"], (t["attempt_type"], t["outcome"]))].append(t)
    for key in by_cell:
        by_cell[key].sort(key=lambda t: t["seed"])

    selected: dict[int, dict] = {}

    def _taken(cls, cell):
        return sum(1 for s in selected.values() if s["class"] == cls and s["cell"] == cell)

    # Step 1: fill each cell's target with the lowest-seed eligible traces.
    for cls, cells in cell_targets.items():
        for cell, target in cells.items():
            for t in by_cell.get((cls, cell), []):
                if _taken(cls, cell) >= target:
                    break
                selected[t["seed"]] = dict(t, cell=cell)

    # Step 2: fill any remaining class quota with the lowest-seed eligible traces.
    for cls, quota in class_quotas.items():
        if sum(1 for s in selected.values() if s["class"] == cls) >= quota:
            continue
        candidates = sorted(
            (t for t in traces if t["class"] == cls and t["seed"] not in selected),
            key=lambda t: t["seed"],
        )
        for t in candidates:
            if sum(1 for x in selected.values() if x["class"] == cls) >= quota:
                break
            selected[t["seed"]] = dict(t, cell=(t["attempt_type"], t["outcome"]))

    report = {"selected_seeds": sorted(selected), "selected_n": len(selected), "shortfalls": []}
    for cls, quota in class_quotas.items():
        taken = sum(1 for t in selected.values() if t["class"] == cls)
        if taken < quota:
            report["shortfalls"].append({"kind": "class", "class": cls, "quota": quota, "filled": taken})
    for cls, cells in cell_targets.items():
        for cell, target in cells.items():
            taken = sum(1 for t in selected.values() if t["class"] == cls and t["cell"] == cell)
            if taken < target:
                report["shortfalls"].append(
                    {"kind": "cell", "class": cls, "cell": list(cell), "target": target, "filled": taken}
                )
    return report


# ---------------------------------------------------------------- audited selection (P1)

MAIN_SEED_RANGE = (1000, 1999)            # §7.2: main-corpus seeds; probe seeds start at 2000
# Exact labeler values only: a prefix match would accept "C-019_FOO" as C-01.
EXACT_CLASSES = frozenset(c.value for c in TrajectoryClass if c.value.startswith("C-0"))
EXCLUDED_CLASSES = frozenset(c.value for c in TrajectoryClass if not c.value.startswith("C-0"))
TAXONOMY = ("C-01", "C-02", "C-03", "C-04", "C-05")
VALID_OUTCOMES = frozenset({"A/A", "one-B", "B/B"})


class DuplicateSeedError(ValueError):
    """Two candidates share a seed. Seeds are assigned once each (§7.2) and the
    trace id is derived from the seed, so this is a data error, not an exclusion."""


VALID_ATTEMPT_TYPES = frozenset({"EXPLICIT", "IMPLICIT", "AMBIG", "SOLO", "NONE"})


class QuotaShortfall(RuntimeError):
    """The eligible pool cannot fill a quota; nothing is padded. ``report`` has the details."""

    def __init__(self, report: dict) -> None:
        super().__init__(f"quota shortfall: {report['shortfalls']}")
        self.report = report


def audited_selection(
    candidates: Iterable[dict],
    class_quotas: Optional[dict] = None,
    cell_targets: Optional[dict] = None,
    seed_range: tuple[int, int] = MAIN_SEED_RANGE,
    strict: bool = False,
) -> dict:
    """§7.2 exclusions, then ``select_monitored_set``, with a full audit report.

    Each candidate needs ``seed``, ``trajectory_class`` (an exact labeler value),
    ``attempt_type`` (the resolved annotation, or None), ``outcome``,
    ``agent_status`` and ``observable_key``. Administrative fields:
    ``annotation_status`` (resolved | needs_adjudication | partially_resolved |
    not_applicable), ``message_status`` (delivered | declined |
    not_sent_by_design | generation_failed) and ``cell_attempt_type`` (the
    allocation code for a message that does not exist by design).

    Exclusion reasons, in order: seed_out_of_range (incl. probe seeds),
    needs_adjudication, message_generation_failed, declined_no_message,
    agent_status, excluded_class, unknown_class, unadjudicated_attempt,
    invalid_attempt_type, invalid_outcome, duplicate_observable (the lowest
    seed of an identical observable record is kept). Never pads a quota.
    Duplicate seeds raise ``DuplicateSeedError`` (seeds are unique by design).
    """
    class_quotas = dict(class_quotas or CLASS_QUOTAS)
    exclusions: dict[str, list[int]] = defaultdict(list)
    seen_seeds: set[int] = set()
    seen_observables: dict = {}
    eligible: list[dict] = []
    ordered = sorted(candidates, key=lambda t: t["seed"])
    counts = defaultdict(int)
    for c in ordered:
        counts[c["seed"]] += 1
    duplicated = sorted(s for s, n in counts.items() if n > 1)
    if duplicated:
        # Fail closed instead of keeping whichever copy happened to come first.
        raise DuplicateSeedError(f"duplicate candidate seeds: {duplicated}")
    for c in ordered:
        seed = c["seed"]
        cls = str(c.get("trajectory_class") or "")
        annotation_status = c.get("annotation_status")
        message_status = c.get("message_status")
        attempt = c.get("attempt_type")
        if not (seed_range[0] <= seed <= seed_range[1]):
            exclusions["seed_out_of_range"].append(seed)
        elif c.get("needs_adjudication") or annotation_status in ("needs_adjudication", "partially_resolved"):
            exclusions["needs_adjudication"].append(seed)
        elif message_status == "generation_failed":
            exclusions["message_generation_failed"].append(seed)
        elif message_status == "declined":
            exclusions["declined_no_message"].append(seed)
        elif c.get("agent_status") != "ok":
            exclusions["agent_status"].append(seed)
        elif cls in EXCLUDED_CLASSES:
            exclusions["excluded_class"].append(seed)
        elif cls not in EXACT_CLASSES:
            exclusions["unknown_class"].append(seed)
        elif annotation_status == "not_applicable" and c.get("cell_attempt_type") is None:
            exclusions["unadjudicated_attempt"].append(seed)
        else:
            if annotation_status == "not_applicable":
                attempt = c["cell_attempt_type"]
            if attempt is None:
                exclusions["unadjudicated_attempt"].append(seed)
            elif attempt not in VALID_ATTEMPT_TYPES:
                exclusions["invalid_attempt_type"].append(seed)
            elif c.get("outcome") not in VALID_OUTCOMES:
                exclusions["invalid_outcome"].append(seed)
            elif c["observable_key"] in seen_observables:
                exclusions["duplicate_observable"].append(seed)
            else:
                seen_observables[c["observable_key"]] = seed
                eligible.append({"seed": seed, "class": cls.split("_", 1)[0],
                                 "attempt_type": attempt, "outcome": c["outcome"]})
        seen_seeds.add(seed)
    selection = select_monitored_set(eligible, class_quotas, cell_targets)
    report = {
        "requested_quotas": class_quotas,
        "candidates": len(ordered),
        "eligible_by_class": {k: sum(1 for e in eligible if e["class"] == k) for k in TAXONOMY},
        "exclusions": {k: sorted(v) for k, v in sorted(exclusions.items())},
        "selected_seeds": selection["selected_seeds"],
        "selected_n": selection["selected_n"],
        "shortfalls": selection["shortfalls"],
        "complete": not selection["shortfalls"],
    }
    if strict and report["shortfalls"]:
        raise QuotaShortfall(report)
    return report
