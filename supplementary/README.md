# Supplementary package — APART coordination-monitoring study

> **This package is not a validated safety monitor.** It is a research artefact. The monitors here
> are exploratory probes evaluated on a small, fixed, synthetic corpus under one game and one prompt
> set. No result in this package supports deploying, relying on, or certifying any monitoring
> capability, and none generalises beyond the corpus it was computed on.

## What the study is

The study asks whether an LLM auditor can detect **coordination toward an operator-dispreferred
option** from the evidence it is given. Two agents play a fixed 2×2 game (A pays 7, B pays 10; the
operator prefers A, B is available). One agent may send one message. We ask whether the message
record, the actions, or both carry evidence that the agents attempted to coordinate on **B**.

The package has **three components**:

### 1. Sender-receiver interaction
A scripted sender and a **live receiver** play 20 scenarios × 4 conditions (C0 no message, C1 neutral
notice, C2 A-directed proposal, C3 B-directed proposal) = **80 trials per receiver**, for two
receivers (Sonnet 4.6, GPT-5.4-mini). Outcomes are the receiver's **B-choice**, **BB** (both chose B)
and **OB** (at least one chose B). Descriptions only — no test, no interval is claimed for these
contrasts.

### 2. Monitor evaluation
Four auditor views of the same records are compared on the **resolved** population
(40 traces per receiver: 20 E-positive, 20 E-negative). M1 sees messages only; M2 sees actions and
payoffs only; M3 sees both; M4 sees M3's evidence plus a structured checklist. The primary contrast
is **M3 − M1** (AUROC difference), with a scenario-cluster paired percentile bootstrap interval.

### 3. Exploratory receiver follow-up
A separate 2×2 message-factorial run (target × explicit payoff mention) on 20 scenario labels × 4
cells = 80 trials per arm, analysed with the within-model contrasts **T**, **J** and **I**. This is
**exploratory** and is kept strictly separate from components 1 and 2.

## What is released, and what is not

| Released | Where |
|---|---|
| Sender-receiver trial records for both receivers | `data/sender_receiver_interaction/` |
| Monitor scores for the **Sonnet** monitor and the **GPT** monitor (`gptmon2` run only) | `data/monitor_scores/` |
| E labels and `evidence_status` per trace | `data/monitor_scores/e_labels_receiver-*.jsonl` |
| Exploratory follow-up trials, plans and stored analyses | `data/exploratory_receiver_follow-up/` |
| Annotations: first pass and revised, **both annotators** | `data/annotations/` |
| The analysis code that produces every reported number | `analysis/` |
| The report's summary figure | `figures/summary_figure.png` |

**Not released, deliberately:**

- `.env` and any API key or credential — **never** copied into this package.
- Spend ledgers, spend guards and budget backups (`results/spend/`, `results/spend_e1/`,
  `runs/*/spend/`, `*.spend_guard.json`) — budget and billing state.
- Spend policy configs (`configs/spend.json`, `configs/spend_gpt54mini.json`) — they hold caps and
  price tables.
- Per-call provider request/response payloads (`runs/*/raw*/`) — raw provider logs.
- Account, organisation and billing identifiers — none are present in the repository at all.
- `apart_code_review.zip` — an internal archive snapshot.
- The **aborted** `gptmon` monitor run. Only the completed `gptmon2` run is used.

`.gitignore` covers all of the above (see the top-level README's "What needs an API key" section).

## Two known artefact issues (flagged, not fixed)

**1. The summary figure has no generating script.** `figures/summary_figure.png` is the report's
summary figure, but **no script in the repository writes that file name** — searching
`figures/**/*.py` and the whole tree for `Summary_figure` returns only prose references. It was
produced out-of-band and cannot be regenerated from this package.

**2. Annotator 2's label manifest pins the original sheet's hash while naming the revised sheet.**
Both `data/monitor_scores/e_label_manifest_receiver-*.json` files record

```json
"Annotator 2": { "sha256": "3d1f2d63fe49979f68952c12bd34addffd08198c0ffd8484e5579eea7aaa5b2e",
                 "sheet":  "annotator_2_revised.csv" }
```

but `data/annotations/annotator_2_revised.csv` actually hashes to
`0648165e6a6e14eeeaecbb092173143b313b45d7a8fcc00af5b7979ec81237c0`. The pinned
`3d1f2d63…` is the hash of `annotator_2_first_pass.csv`. Annotator 1's entry is self-consistent.
**E is unaffected:** both annotators agreed on `coordination_attempt` and `target` for all ten
messages from the outset, so the E rule yields identical labels from either sheet, and no number in
this package changes. The discrepancy is a provenance defect that needs the owner to say which
artefact the manifest was meant to pin.

## Anonymisation and path normalisation

Personal initials do not appear anywhere in this package — not in file names and not in file
contents. Two documented transforms were applied to six data files, and every affected entry in
`manifests/file_manifest.json` carries `"copied_verbatim": false` plus a `transformations` list:

- **`MM` → `Annotator 1`, `LL` → `Annotator 2`**, and the sheet file names
  `annotation_sheet_MM[_original].csv` / `annotation_sheet_LL[_original].csv` → the neutral names
  used in `data/annotations/`. Applied to the four `e_labels` / `e_label_manifest` files.
- **Absolute local path prefix → `<repo>/`** in the two exploratory-arm `summary.json` files.
  Nothing under `supplementary/` contains a `/Users/...` path.

The originals are unmodified in the repository at the four `annotation_sheet_*` paths, and the
`source_path` / `source_sha256` fields of the manifest record exactly where each copy came from.

## Reproducing the tables and figures offline

No network access and no API key are needed.

```bash
cd supplementary/analysis
python run_all.py            # writes ../outputs/reproduced_tables.{md,json} + reproduction_report.md
python summary_figure.py     # optional: rebuilds ../outputs/figure_summary_rebuilt.png (needs matplotlib)
```

`run_all.py` compares every reported number against the value stated in the report and exits non-zero
on any mismatch. Current status: **35 checks, 35 OK, 0 MISMATCH**
(`outputs/reproduction_report.md`).

Dependencies: `environment/requirements.txt` (standard library only for `run_all.py`; matplotlib is
optional and only needed for the figure).

## Which steps need provider access

| Step | Needs an API key? |
|---|---|
| Reproducing every table and figure in this package | **No** |
| Re-deriving E labels and `evidence_status` | No |
| Running the monitors on new traces | **Yes** — `scripts/run_monitors.py` (OpenRouter) |
| Running the sender-receiver interaction again | **Yes** — `scripts/run_phase_a_live.py` (OpenRouter) |
| Running the exploratory follow-up again | **Yes** — `scripts/run_experiment_e1.py --confirm-live` |

Nothing in this package calls a provider. Set `APART_API_KEY` in an **untracked** `.env` at the
repository root if you run the live entry points; never commit it.

## Layout

```
supplementary/
├── README.md                  this file
├── data_dictionary.md         every field in data/, with units and allowed values
├── design/                    game, conditions, templates, monitor views, construct definitions
├── data/                      the released data payload
├── analysis/                  the scripts that produce the reported numbers
├── environment/requirements.txt
├── figures/summary_figure.png
├── manifests/file_manifest.json   path, sha256 and source of every released file
└── outputs/                   generated by analysis/run_all.py
```

See `data_dictionary.md` for field-level detail and `manifests/file_manifest.json` for provenance
(sha256, source path and source sha256) of every file in this package.
