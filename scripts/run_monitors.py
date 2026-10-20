#!/usr/bin/env python3
"""Run monitor views over a frozen corpus.

Dry run (no API, fake client):
    python scripts/run_monitors.py --dry-run

Real run (requires APART_API_KEY in the environment):
    python scripts/run_monitors.py --traces data/frozen/pilot_v2_traces.jsonl
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from apart.schema import trace_from_dict  # noqa: E402
from monitors.client import (  # noqa: E402
    PHASES,
    BudgetExceeded,
    FakeClient,
    ModelClient,
    SpendConfig,
    SpendConfigError,
    SpendGuard,
)
from monitors.runner import (  # noqa: E402
    VIEW_ORDER,
    non_ok_rate,
    run_corpus,
    should_stop,
    write_id_map,
    write_scores,
)


def load_traces(path: Path):
    with path.open(encoding="utf-8") as handle:
        return [trace_from_dict(json.loads(line)) for line in handle if line.strip()]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run M1-M4 monitor views.")
    parser.add_argument(
        "--traces", type=Path, default=ROOT / "data" / "frozen" / "pilot_v2_traces.jsonl"
    )
    parser.add_argument("--out", type=Path, default=ROOT / "results" / "scores_v1.jsonl")
    parser.add_argument("--id-map", type=Path, default=ROOT / "results" / "id_map_v1.csv")
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "results" / "raw")
    parser.add_argument(
        "--phase",
        required=True,
        choices=PHASES,
        help="Spending phase (smoke, gate, pilot, extension); required on every run.",
    )
    parser.add_argument(
        "--spend-config",
        type=Path,
        default=Path(os.environ["APART_SPEND_CONFIG"]) if os.environ.get("APART_SPEND_CONFIG") else None,
        help="JSON spend config (provider, model, phase caps, prices). Required for real runs.",
    )
    parser.add_argument(
        "--ledger", type=Path, default=ROOT / "results" / "spend_guard.json"
    )
    parser.add_argument("--run-id", default=None, help="Stable id for per-run accounting.")
    parser.add_argument("--dry-run", action="store_true", help="Use a fake client; no API calls.")
    parser.add_argument("--created-utc", default=None)
    args = parser.parse_args(argv)

    traces = load_traces(args.traces)
    guard = None
    if args.dry_run:
        client = FakeClient()
    else:
        if args.spend_config is None:
            print(
                "error: --spend-config or APART_SPEND_CONFIG is required for a real run",
                file=sys.stderr,
            )
            return 3
        try:
            spend_config = SpendConfig.from_file(args.spend_config)
            run_id = args.run_id or (
                f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            )
            guard = SpendGuard(spend_config, args.phase, args.ledger, run_id)
            client = ModelClient(spend_guard=guard)
        except SpendConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3

    try:
        records, ensembles = run_corpus(
            traces, client, args.created_utc, raw_dir=args.raw_dir
        )
    except BudgetExceeded as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 4
    write_scores(records, args.out)
    write_id_map(records, args.id_map)

    print(f"{len(traces)} traces, {len(records)} records -> {args.out}")
    print("dry-run: fake client, no API calls" if args.dry_run else f"model: {client.model}")
    for view in VIEW_ORDER:
        print(f"  {view}: non-ok {non_ok_rate(records, view):.1%}")
    if guard is not None:
        totals = guard.totals()
        print(
            f"spend: phase {totals['phase']} "
            f"${totals['phase_spent_usd']:.6f}/${totals['cap_usd']:.6f} "
            f"({totals['phase_requests']} requests); run {totals['run_id']} "
            f"${totals['run_spent_usd']:.6f}"
        )
    if should_stop(records):
        print("STOP: a view exceeds 10% non-ok on the gate/sanity set (spec §5.6).")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
