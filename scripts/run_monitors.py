#!/usr/bin/env python3
"""Run monitor views over a frozen corpus.

Dry run (no API, fake client):
    python scripts/run_monitors.py --dry-run

Real run (requires APART_API_KEY in the environment):
    python scripts/run_monitors.py --traces data/frozen/pilot_v2_traces.jsonl
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from apart.schema import trace_from_dict  # noqa: E402
from monitors.client import FakeClient, ModelClient  # noqa: E402
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
    parser.add_argument("--dry-run", action="store_true", help="Use a fake client; no API calls.")
    parser.add_argument("--created-utc", default=None)
    args = parser.parse_args(argv)

    traces = load_traces(args.traces)
    client = FakeClient() if args.dry_run else ModelClient()

    records, ensembles = run_corpus(traces, client, args.created_utc, raw_dir=args.raw_dir)
    write_scores(records, args.out)
    write_id_map(records, args.id_map)

    print(f"{len(traces)} traces, {len(records)} records -> {args.out}")
    print("dry-run: fake client, no API calls" if args.dry_run else f"model: {client.model}")
    for view in VIEW_ORDER:
        print(f"  {view}: non-ok {non_ok_rate(records, view):.1%}")
    if should_stop(records):
        print("STOP: a view exceeds 10% non-ok on the gate/sanity set (spec §5.6).")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
