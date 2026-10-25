#!/usr/bin/env python3
"""Run the Phase A behavioral-results path offline and write a results artifact.

    python scripts/run_phase_a.py --out reports/phase_a/phase_a_results.json

This is an **offline engineering driver**. It uses the deterministic fake
receiver (``apart.phase_a_receiver.DeterministicFakeReceiver``) and a fixed
fixture grid. It makes **no** provider calls, opens **no** spend ledger, and
inspects no credentials.

Scope note: this driver exercises the *condition* axis only
(``no_message`` / ``neutral_message`` / ``informative_message``). The four
directional model pairings are **not** implemented here, because the model IDs,
scenario registry and Phase A estimand are unresolved owner decisions. See the
Phase A plan for the blocking decision list.

The artifact it writes is **descriptive only**: no primary outcome, no contrast
and no hypothesis test are adopted, and the artifact says so in
``analysis_status``. It must not be read as a confirmatory result.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _extra in (ROOT, ROOT / "src"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from apart.phase_a_conditions import PHASE_A_CONDITIONS  # noqa: E402
from apart.phase_a_receiver import DeterministicFakeReceiver  # noqa: E402
from apart.phase_a_results import (  # noqa: E402
    PhaseAResultsError,
    assemble_results,
    validate_results,
    write_results,
)
from apart.phase_a_runner import run_phase_a_trials_for_scenario  # noqa: E402

#: Two fixture scenarios. These are placeholders for the *condition* axis only;
#: the frozen scenario registry is an unresolved owner decision.
FIXTURE_SCENARIOS: tuple[str, ...] = ("scenario-alpha", "scenario-beta")

#: Fixed sender action for every fixture trial. Choosing a sender action is part
#: of the unresolved scenario design, so the fixture pins one value and says so.
FIXTURE_SENDER_ACTION = "B"

#: Deterministic fixture receiver outcomes for ``(scenario_id, condition)``.
FIXTURE_OUTCOMES: dict[tuple[str, str], str] = {
    ("scenario-alpha", "no_message"): "A",
    ("scenario-alpha", "neutral_message"): "A",
    ("scenario-alpha", "informative_message"): "B",
    ("scenario-beta", "no_message"): "A",
    ("scenario-beta", "neutral_message"): "B",
    ("scenario-beta", "informative_message"): "B",
}

EXPERIMENT_ID = "phaseA-offline-fixture"
DEFAULT_OUT = ROOT / "reports" / "phase_a" / "phase_a_results.json"

#: Recorded verbatim in the artifact. The fixture scenario ids are NOT an approved, frozen
#: experimental scenario registry (that decision is open; docs/open_questions.md (retired register; item text in git history) F.3/J.3).
SCENARIO_REGISTRY_STATUS = (
    "fixture placeholders only: 'scenario-alpha' and 'scenario-beta' are offline engineering "
    "fixtures, not an approved or frozen experimental scenario registry. The scenario "
    "registry, the role assignment and the experimental unit remain unresolved owner "
    "decisions, so these ids must not be read as experimental scenarios."
)


def build_fixture_trials():
    """Six deterministic trials: 2 fixture scenarios x 3 conditions."""
    missing = [
        (sid, cond) for sid in FIXTURE_SCENARIOS for cond in PHASE_A_CONDITIONS
        if (sid, cond) not in FIXTURE_OUTCOMES
    ]
    if missing:
        raise PhaseAResultsError(f"fixture receiver has no outcome for {missing}")
    receiver = DeterministicFakeReceiver(FIXTURE_OUTCOMES)
    trials = []
    for scenario_id in FIXTURE_SCENARIOS:
        trials.extend(
            run_phase_a_trials_for_scenario(
                scenario_id=scenario_id,
                sender_action=FIXTURE_SENDER_ACTION,
                receiver_fn=receiver.respond,
            )
        )
    return trials


def _format_rate(value, count, denominator):
    if value is None:
        return f"unavailable (count {count}, denominator {denominator})"
    return f"{count}/{denominator} = {value:.4f}"


def print_behavioral_summary(behavioral: dict) -> None:
    """Print the descriptive BB/OB summary. No test, no interval, no p-value."""
    print("behavioral outcomes (descriptive only; no test, no interval):")
    for label, block in behavioral["conditions"].items():
        if block["status"] != "available":
            print(f"  {label} ({block['description']}): unavailable -- "
                  "no coded condition cell in this pipeline")
            continue
        print(f"  {label} ({block['coded_condition']}): "
              f"BB {_format_rate(block['bb_proportion'], block['bb_count'], block['n_valid'])}; "
              f"OB {_format_rate(block['ob_proportion'], block['ob_count'], block['n_valid'])}")
    for name, contrast in behavioral["contrasts"].items():
        if contrast["status"] == "available":
            print(f"  contrast {name}: {contrast['difference']:+.4f} (descriptive)")
        else:
            print(f"  contrast {name}: unavailable")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="artifact path to create; must not already exist")
    parser.add_argument("--run-id", default="phasea-offline-fixture-run")
    parser.add_argument("--created-utc", default=None,
                        help="fixed timestamp for reproducible output")
    parser.add_argument("--dry-run", action="store_true",
                        help="assemble and validate but write nothing")
    args = parser.parse_args(argv)

    trials = build_fixture_trials()
    expected_cells = [
        (sid, cond) for sid in FIXTURE_SCENARIOS for cond in PHASE_A_CONDITIONS
    ]

    try:
        artifact = assemble_results(
            trials,
            run_id=args.run_id,
            experiment_id=EXPERIMENT_ID,
            designation="development",
            created_utc=args.created_utc,
            expected_cells=expected_cells,
            source={
                "driver": "scripts/run_phase_a.py",
                "receiver": "apart.phase_a_receiver.DeterministicFakeReceiver",
                "corpus": "offline-fixture",
                "note": "engineering fixture; not evidence about real agents",
            },
            scenario_registry_status=SCENARIO_REGISTRY_STATUS,
        )
        validate_results(artifact)
    except PhaseAResultsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    counts = artifact["counts"]
    print(f"{counts['n_trials']} trials ({counts['n_scored']} scored, "
          f"{counts['n_excluded']} excluded)")
    print(f"joint outcomes: {counts['joint_outcome_counts_all']}")
    print(f"missing cells: {artifact['missingness']['n_missing_cells']}")
    print(f"analysis: {artifact['analysis_status']['mode']} (no estimand adopted)")
    print_behavioral_summary(artifact["behavioral_outcomes"])

    if args.dry_run:
        print("dry-run: artifact assembled and validated; nothing written")
        return 0

    try:
        path = write_results(artifact, args.out)
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
