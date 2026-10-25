#!/usr/bin/env bash
# Phase A live pilot, then Phase B (M1-M4) on its traces, one monitor run per condition.
# Billable. Approved by MM 2026-10-24 (docs/decision_log.md). Stops at the first error.
# Usage (from the project root):  bash scripts/run_pilot_live.sh
set -euo pipefail
cd "$(dirname "$0")/.."

OUT=runs/phase-a-live-v2
PY=.venv/bin/python

$PY scripts/run_phase_a_live.py --out-dir "$OUT" --confirm-live --run-id phase-a-live-v2-20261024

# Transport/API failures mean the run did not really happen: stop before Phase B.
$PY -c "import json,sys; s=json.load(open('$OUT/summary.json')); \
f=sum(c['excluded'].get('failed',0) for c in s['by_condition'].values()); \
print(f\"Phase A: {s['valid_traces']}/{s['trials']} valid traces, {f} transport/API failures\"); \
sys.exit(1 if f else 0)"

for c in c0_no_message c1_neutral c2_a_coordination c3_b_coordination; do
  if [ ! -s "$OUT/traces_$c.jsonl" ]; then
    echo "no valid traces for $c; skipping its monitor run" >&2
    continue
  fi
  $PY scripts/run_monitors.py --allocation pilot_audit --phase gate \
    --spend-config configs/spend.json --views M1,M2,M3,M4 \
    --traces "$OUT/traces_$c.jsonl" --out "$OUT/scores_$c.jsonl" \
    --id-map "$OUT/id_map_$c.csv" --raw-dir "$OUT/raw_$c" \
    --run-id "phaseb-pilot-v2-$c"
done

echo "done: outputs in $OUT (annotation_sheet.csv is the blind sheet for annotators)"
