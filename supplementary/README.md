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
- Per-call provider request/response payloads (`runs/*/raw*/`) — raw provider logs.
- Account, organisation and billing identifiers — none are present in the repository at all.
- `apart_code_review.zip` — an internal archive snapshot.
- The **aborted** `gptmon` monitor run. Only the completed `gptmon2` run is used.

`.gitignore` covers all of the above (see the top-level README's "What needs an API key" section).

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
