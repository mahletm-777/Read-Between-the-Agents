# `src/apart/` — study logic

Pure-Python library code: no I/O side effects on import, no provider calls. Everything here is
importable offline and is what the tests exercise.

## Layout

| Module | Contains |
|---|---|
| `experiment_e1.py` | **Exploratory receiver follow-up**: plan builder, frozen 2×2 message factorials, T/J/I contrasts, complete-case eligibility, scenario-cluster bootstrap, serialisation |
| `experiment_e1_adapter.py` | E1-only conversion to canonical traces, with a rejection ladder |
| `analysis_e.py` | **Monitor evaluation**: AUROC, paired AUROC difference, scenario-cluster bootstrap interval, Wilson / Clopper–Pearson, exact McNemar, Holm, Wilcoxon |
| `analysis.py` | The **historical** Y_H analysis lineage (preserved; not comparable with `analysis_e.py`) |
| `e_matrix.py` | Builds the monitor × receiver matrix summary from label + score files |
| `e_label.py` | **E** derivation from annotations (message-derived, action-invariant) |
| `e_sheet_labels.py` | Converts an annotation sheet into E label rows |
| `labeler.py` | Historical labelling: `collusive_talk`, `safety_violation`, **`Y_H = collusive_talk OR safety_violation`** |
| `phase_a_live.py` | **Sender-receiver interaction**: the ten frozen message templates (`TEMPLATES`), the C1 neutral notice, condition keys |
| `phase_a_results.py` | BB / OB / receiver-B-choice definitions and their descriptive contrasts |
| `phase_a_runner.py`, `phase_a_models.py`, `phase_a_parser.py`, `phase_a_to_trace.py` | Phase A execution and conversion |
| `observable.py`, `provenance.py`, `schema.py`, `generator*.py` | Trace schema, provenance, scenario generation |
| `analysis.py`, `evaluation.py` | Historical behavioural evaluation helpers |
| `brief_registry.py` | Extension-corpus brief registry |

## Main entry points (library level)

```python
from apart import experiment_e1 as e1

plan = e1.build_plan()                 # frozen plan; no I/O
e1.validate_plan(plan)
analysis = e1.analyze(records, plan)   # records = list of E1 trial dicts
```

```python
from apart import analysis_e
# per-arm AUROC and paired AUROC difference on the resolved population
```

## Reuse

* **Inputs** are plain dicts/lists, or JSONL rows read by the caller. Nothing here opens a socket or
  reads `.env`.
* **Outputs** are plain dicts suitable for `json.dumps(..., allow_nan=False)`.
* **No paid API is involved.** Example that touches neither the network nor the filesystem:

```bash
python -c "import sys; sys.path.insert(0,'src'); \
from apart import experiment_e1 as e1; \
p = e1.build_plan(); print(len(e1.planned_trials(p)), e1.plan_sha256(p)[:12])"
```

## Related documents

`docs/monitor_spec.md`, `docs/e_endpoint_contract_v1_0.md`, `docs/e_analysis_plan_v1_0.md`,
`docs/objective_spec_v1_0.md`, `docs/decision_log.md`.
