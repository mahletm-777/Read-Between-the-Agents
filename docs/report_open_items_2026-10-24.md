# Report open items and post-annotation commands (draft, 2026-10-24)

Draft for MM. Nothing here has been run on real annotations; no results exist yet.

## Open items the report must state

1. **No inferential test for the primary AUROC difference.** The M3 vs M1 paired AUROC
   difference is reported with the approved scenario-cluster percentile bootstrap 95%
   interval only. `p_boot` in the reports is a trace-level placeholder
   (`P_VALUE_STATUS = UNRESOLVED_no_approved_test_for_the_auroc_difference`) and must not
   be quoted as a p-value. Holm on the secondary contrasts uses that same placeholder,
   so the secondary Holm-adjusted values are also not interpretable as tests yet.
2. **Inter-annotator reliability rests on 10 messages.** Each receiver run has 10 unique
   message texts (fanned out to 40 delivered traces via `annotation_key.csv`). Any
   agreement statistic is over 10 items and should be reported as such, with no claim of
   reliable annotation beyond this pilot.
3. **About $0.42 remains in the spend ledger from the void first attempt.** It is
   recorded as spent, not reversed. The ledger was not altered; the report should state
   the amount and that the attempt produced no usable data.
4. Also already recorded elsewhere: scenarios-as-independent-units is an unresolved
   assumption (`docs/open_questions.md` J.2 — that register is retired; the item remains
   unresolved and its text is in git history); 13 adapter cells are unmapped and count as
   UNRESOLVED.

## What was built (offline)

* `src/apart/e_sheet_labels.py`: two finished sheets (+ optional adjudication sheet) →
  `e_labels.jsonl` (one E record per trace, all four conditions) + `label_manifest.json`
  (annotator/guide/adjudicator provenance, sheet SHA-256s, disagreements, unmapped
  cells, the 20% unresolved gate). Disagreements on the translated reason/target block
  label output (exit 2) until an adjudication sheet covers them.
* `src/apart/e_matrix.py`: runs `analysis_e.run_analysis_e` for receiver {sonnet, gpt} ×
  monitor {sonnet `scores_c*.jsonl`, gpt `scores_gptmon_c*.jsonl`}; writes one full report
  per combination plus `matrix_summary.json`. Skips a combination when the gate is
  triggered (unless `--proceed-despite-gate`) or its score files are missing.
* `tests/test_e_sheet_labels_matrix.py`: synthetic annotations and scores only.

## Commands once annotations arrive

Write outputs to new directories, not into `runs/`. Replace names and the guide version.

```bash
PYTHONPATH=src .venv/bin/python -m apart.e_sheet_labels --run-dir runs/phase-a-live-v2 --annotator ann1=PATH/ann1_sonnet.csv --annotator ann2=PATH/ann2_sonnet.csv --guide-version GUIDE_VERSION --out-dir results/e_labels/sonnet_receiver
```

```bash
PYTHONPATH=src .venv/bin/python -m apart.e_sheet_labels --run-dir runs/phase-a-live-gpt54mini-v1 --annotator ann1=PATH/ann1_gpt.csv --annotator ann2=PATH/ann2_gpt.csv --guide-version GUIDE_VERSION --out-dir results/e_labels/gpt_receiver
```

If either prints `BLOCKED`, adjudicate the listed messages in a sheet of the same format
and re-run with `--adjudication PATH --adjudicator NAME`.

```bash
PYTHONPATH=src .venv/bin/python -m apart.e_matrix --labels-sonnet results/e_labels/sonnet_receiver --labels-gpt results/e_labels/gpt_receiver --out-dir results/e_matrix
```


## Proposed decision-log entry (not written; decision_log.md was being edited elsewhere)

2026-10-24: Implementation choices for the post-annotation step, for MM's approval:
(a) unmapped adapter cells are recorded with reason `INSUFFICIENT_EVIDENCE` (E unchanged,
UNRESOLVED either way); (b) a disagreement is a difference in the translated reason or
target, and raw-field differences that translate identically are reported but do not
block; (c) a triggered unresolved gate stops the matrix run by default.
