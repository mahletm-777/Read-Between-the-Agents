# APART — coordination monitoring in two-agent LLM interaction

**This repository is a research artefact, not a validated safety monitor.** It contains an
exploratory study of whether a language-model auditor can detect *coordination toward an
operator-dispreferred option* from the evidence it is shown. Everything here was run on a small,
fixed, synthetic corpus under one game and one prompt set; nothing in it supports deploying, relying
on, or certifying a monitoring capability, and no result generalises beyond the corpus it was
computed on.

![Summary figure](supplementary/figures/summary_figure.png)

*Observed receiver behaviour by condition and receiver. Descriptive only — no intervals, no tests.*

## The three components

**Sender-receiver interaction.** A scripted sender and a live receiver play a fixed 2×2 game (A pays
7, B pays 10; the operator prefers A, B is available) across 20 scenarios × 4 conditions — C0 no
message, C1 neutral notice, C2 A-directed proposal, C3 B-directed proposal — giving 80 trials per
receiver for two receivers. The recorded outcomes are the receiver's **B-choice**, **BB** and **OB**;
these are described, not tested.

**Monitor evaluation.** Four auditor views of the same records are compared on the resolved
population (40 traces per receiver: 20 E-positive, 20 E-negative). M1 sees messages only, M2 actions
and payoffs only, M3 both, and M4 M3's evidence plus a structured checklist. The primary contrast is
**M3 − M1** on AUROC, with a scenario-cluster paired percentile bootstrap interval.

**Exploratory receiver follow-up.** A separate 2×2 message-factorial run (target × explicit payoff
mention) over 20 scenario labels × 4 cells, analysed with the within-model contrasts **T**, **J** and
**I**. This is exploratory and is kept strictly separate from the other two components.

Full numbers, data dictionary and reproduction instructions:
**[`supplementary/README.md`](supplementary/README.md)**.

## Repository map

```
├── README.md                     this file
├── AGENTS.md, CLAUDE.md          agent working rules for this repo
├── CONTRIBUTING.md               contribution workflow
├── src/apart/                    study logic: plans, contrasts, labels, metrics   -> src/apart/README.md
├── monitors/                     provider client + spend ledger + monitor prompts -> monitors/README.md
├── agents/                       agent-side client, prompts and stage runner      -> agents/README.md
├── scripts/                      CLI entry points (offline and provider-backed)   -> scripts/README.md
├── tests/                        pytest suite, offline only                       -> tests/README.md
├── configs/                      spend policy and model bindings                  -> configs/README.md
├── supplementary/                the release package: data, design, analysis, manifest
├── docs/                         specifications, decision log, runbook, manuscript draft
├── experiments/                  the separate generation-probe experiment
├── reports/, results/, runs/     run artefacts (most are not tracked; see .gitignore)
└── audits/                       read-only audits of the numbers and the markdown layout
```

## Quick start

```bash
# 1. environment (Python 3.14; pytest is the only hard requirement)
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# 2. tests - offline, no API key, no spend
python -m pytest tests/ -q

# 3. reproduce every reported table and figure from the released data
#    - offline, no API key
cd supplementary/analysis && python run_all.py
```

`run_all.py` compares each reported number with the value stated in the report and exits non-zero on
any mismatch. Its current status is recorded in `supplementary/outputs/reproduction_report.md`.

Other offline checks that never spend anything:

```bash
python scripts/run_experiment_e1.py --dry-run --out-dir /tmp/e1-dry   # in-process fake receiver
python scripts/run_monitors.py --dry-run --views M1,M2,M3,M4          # render prompts, send nothing
```

## What needs an API key

Only the **live** entry points do — the sender-receiver interaction
(`scripts/run_phase_a_live.py`), the monitors (`scripts/run_monitors.py`), and the exploratory
follow-up (`scripts/run_experiment_e1.py --confirm-live`). Everything in `supplementary/` and the
whole test suite run without one.

Configure it in an **untracked** `.env` at the repository root:

```bash
# .env  -- NEVER commit this file
APART_API_KEY=<your OpenRouter key>
APART_BASE_URL=https://openrouter.ai/api/v1
```

`.env` is already in `.gitignore`, together with `.env.*` (with `!.env.example` the only exception),
`logs/`, `*.zip`, `results/raw/`, `data/raw/*` and the spend ledgers and guards. **Keys are read from
the environment only and are never written to a result file, a log, or a `repr`.** Live runs
additionally require explicit authorisation flags and are gated by a ledger that refuses any request
it cannot price or reserve.

## Key limitations

The evaluation is **post hoc and pilot-scale**: four auditor views, one game, one prompt set, 40
scored traces per receiver. The target contrast is **not a pure manipulation** — the A- and
B-directed messages differ in payoff magnitude, justification and length, and template identity is
fully aliased with scenario id. The reported M1/M3/M4 AUROC of 1.0 is a **ceiling**: on this corpus
those views are not distinguishable from one another. The M2 result is an **evidence mismatch** — an
action-only view returns 0.5 because every one of its scores is 0, a degenerate value rather than a
fitted one. Intervals describe resampling variability across these 20 scenario labels under one fixed
game; they are not generalisation claims, no cross-model test is performed, and no p-value is
reported for the AUROC difference because no test is approved for it.
