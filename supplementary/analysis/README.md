# `supplementary/analysis/` — the code that produces the reported numbers

Standard library only, except `summary_figure.py` (optional, matplotlib). Reads only
`../data/`; writes only `../outputs/`. **No network access and no API key.**

```bash
cd supplementary/analysis
python run_all.py          # all tables + the reproduction check  -> ../outputs/
python summary_figure.py   # optional figure rebuild
```

## Modules

| Module | What it produces | Mirrors |
|---|---|---|
| `common.py` | paths, JSONL IO, and the shared statistics (Mann-Whitney AUROC, Wilson, exact McNemar, Holm, Wilcoxon signed-rank, linear-interpolation percentile) | `src/apart/analysis_e.py` |
| `outcome_derivation.py` | **Table 1**: receiver B-choice, **BB**, **OB** per condition and receiver, plus validity/retry accounting | `src/apart/phase_a_results.py`, `src/apart/phase_a_live.py` |
| `e_labels.py` | **E** derivation and the `evidence_status` counts; also records the Y_H definition and its separation from E | `src/apart/e_label.py`, `src/apart/labeler.py` |
| `resolved_population.py` | the **resolved** filter (40 traces per receiver: 20 E+, 20 E−) and the per-view score vectors every metric is computed on | `src/apart/analysis_e.py` |
| `auroc_and_contrasts.py` | **Tables 2–3**: per-view AUROC and the five pre-specified contrasts (primary `M3 − M1`), with McNemar/Wilcoxon as secondary/descriptive | `src/apart/analysis_e.py` |
| `bootstrap.py` | the **scenario-cluster paired percentile bootstrap** (2000 reps, seed 0, unit = scenario, multiplicity preserved) and the exploratory shared-draw T/J/I bootstrap | `src/apart/analysis_e.py`, `src/apart/experiment_e1.py` |
| `tables_and_figures.py` | **Table 4** and the JSON bundle; assembles `../outputs/reproduced_tables.{md,json}` | `results/phaseA_reanalysis/analysis.py`, `results/phaseA_extras/phaseA_extras.py` |
| `summary_figure.py` | optional rebuild of the summary figure from the same numbers | — |
| `run_all.py` | driver + the reproduction check against the reported values | — |

## Inputs and outputs

**Inputs** (read-only): `../data/sender_receiver_interaction/*`,
`../data/monitor_scores/*`, `../data/exploratory_receiver_follow-up/*`,
`../data/annotations/*`.

**Outputs**: `../outputs/reproduced_tables.json`, `../outputs/reproduced_tables.md`,
`../outputs/reproduction_report.md`, and (optionally) `../outputs/figure_summary_rebuilt.png`.

## Minimal offline example

```python
import sys; sys.path.insert(0, "supplementary/analysis")
import outcome_derivation as od
print(od.condition_counts("sonnet")["c3_b_coordination"])
# -> {'n_trials': 20, 'denominator': 20, 'receiver_b_count': 8, 'bb_count': 3,
#     'ob_count': 15, ...}
```

## Two things to know before reusing this code

- **Denominators are conditional on validity.** `condition_counts` counts only `validity ==
  "valid"` trials; excluded trials are reported separately and never imputed.
- **The bootstrap is a scenario-cluster bootstrap over realised per-scenario contrasts.** It
  quantifies resampling variability across the 20 scenario labels of *this* corpus, under one fixed
  game and prompt set. It is not evidence of generalisation, and no cross-model contrast is computed.
