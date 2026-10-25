#!/usr/bin/env bash
# GPT-5.4-mini receiver arm (approved by MM 2026-10-24, docs/decision_log.md).
# Same plan, templates, seeds and order as runs/phase-a-live-v2; only the receiver model
# changes. Monitors stay Sonnet 4.6 so the monitor is constant across receivers.
# Billable. Run only after scripts/run_pilot_live.sh has finished.
# Usage (from the project root):  bash scripts/run_gpt_arm.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
SONNET=configs/spend.json
GPT=configs/spend_gpt54mini.json
OUT=runs/phase-a-live-gpt54mini-v1

if [ ! -s runs/phase-a-live-v2/scores_c3_b_coordination.jsonl ]; then
  echo "the Sonnet Phase B run has not finished; wait for run_pilot_live.sh" >&2
  exit 1
fi

$PY scripts/rebind_spend_config.py --from "$SONNET" --to "$GPT" --by MM \
  --reason "GPT-5.4-mini receiver arm (Phase A)"
# Whatever happens next, rebind back to the Sonnet config.
trap '$PY scripts/rebind_spend_config.py --from "$GPT" --to "$SONNET" --by MM \
  --reason "back to Sonnet after GPT receiver arm"' EXIT

$PY scripts/run_phase_a_live.py --out-dir "$OUT" --confirm-live \
  --spend-config "$GPT" --run-id phase-a-live-gpt54mini-v1-20261024

$PY -c "import json,sys; s=json.load(open('$OUT/summary.json')); \
f=sum(c['excluded'].get('failed',0) for c in s['by_condition'].values()); \
print(f\"Phase A (GPT): {s['valid_traces']}/{s['trials']} valid traces, {f} transport/API failures\"); \
sys.exit(1 if f else 0)"

trap - EXIT
$PY scripts/rebind_spend_config.py --from "$GPT" --to "$SONNET" --by MM \
  --reason "back to Sonnet after GPT receiver arm"

for c in c0_no_message c1_neutral c2_a_coordination c3_b_coordination; do
  if [ ! -s "$OUT/traces_$c.jsonl" ]; then
    echo "no valid traces for $c; skipping its monitor run" >&2
    continue
  fi
  $PY scripts/run_monitors.py --allocation pilot_audit --phase gate \
    --spend-config "$SONNET" --views M1,M2,M3,M4 \
    --traces "$OUT/traces_$c.jsonl" --out "$OUT/scores_$c.jsonl" \
    --id-map "$OUT/id_map_$c.csv" --raw-dir "$OUT/raw_$c" \
    --run-id "phaseb-pilot-gpt54mini-v1-$c"
done

echo "done: outputs in $OUT"
