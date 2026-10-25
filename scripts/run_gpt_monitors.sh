#!/usr/bin/env bash
# GPT-5.4-mini monitors (M1-M4) on both receivers' traces (approved by MM 2026-10-24,
# docs/decision_log.md). Completes the receiver {Sonnet, GPT} x monitor {Sonnet, GPT}
# design; the Sonnet-monitor scores already exist. Same views, prompts and traces.
# Billable. Run only after scripts/run_gpt_arm.sh has finished.
# Usage (from the project root):  bash scripts/run_gpt_monitors.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
SONNET=configs/spend.json
GPT=configs/spend_gpt54mini.json

if [ ! -s runs/phase-a-live-gpt54mini-v1/scores_c3_b_coordination.jsonl ]; then
  echo "the GPT receiver arm has not finished; wait for run_gpt_arm.sh" >&2
  exit 1
fi

$PY scripts/rebind_spend_config.py --from "$SONNET" --to "$GPT" --by MM \
  --reason "GPT-5.4-mini monitors on both receivers' traces"
trap '$PY scripts/rebind_spend_config.py --from "$GPT" --to "$SONNET" --by MM \
  --reason "back to Sonnet after GPT monitors"' EXIT

for dir in runs/phase-a-live-v2 runs/phase-a-live-gpt54mini-v1; do
  tag=$(basename "$dir")
  for c in c0_no_message c1_neutral c2_a_coordination c3_b_coordination; do
    if [ ! -s "$dir/traces_$c.jsonl" ]; then
      echo "no valid traces for $dir $c; skipping" >&2
      continue
    fi
    $PY scripts/run_monitors.py --allocation pilot_audit --phase gate \
      --spend-config "$GPT" --views M1,M2,M3,M4 \
      --traces "$dir/traces_$c.jsonl" --out "$dir/scores_gptmon2_$c.jsonl" \
      --id-map "$dir/id_map_gptmon2_$c.csv" --raw-dir "$dir/raw_gptmon2_$c" \
      --run-id "phaseb-gptmon2-$tag-$c"
  done
done

echo "done: GPT-monitor scores are scores_gptmon2_*.jsonl in both run directories"
