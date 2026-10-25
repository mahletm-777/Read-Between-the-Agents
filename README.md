# Reading Between the Agents: Message and Action Evidence in Coordination Monitoring


Research conducted at the AI Collusion Research Sprint, October 2026 (Apart Research).

## Abstract

Monitoring multi-agent coordination requires weighing what agents say against what they do, not conflating evidence with intent. In a pilot study of a two-option game where the operator prefers *A*, we first varied scripted sender messages and measured two language models' responses as receivers. Sonnet 4.6 chose the permitted but dispreferred option *B* in 8/20 trials under a *B*-directed proposal, GPT-5.4-mini in 7/20 under an *A*-directed one; however, direction was bundled with wording. We then compared four monitors: message-only, action-only, combined, and combined with a checklist. Message-only and combined monitors reached AUROC 1.00 on the resolved subset, so combining evidence showed no gain. This reflects a ceiling, not equivalence, because the endpoint is post hoc and perfectly separable by template. These findings show how endpoint construction, evidence asymmetry, and prompt design can limit what a monitoring evaluation shows, and we offer two diagnostics, saturation and evidence-contract checks, for future benchmarks.

## Summary figure

![Summary figure](figures/summary_figure.png)

*Receiver B-choice and joint-B behaviour by condition and receiver, for the sender-receiver interaction and the exploratory receiver follow-up.*

## Repository structure

```
.
├── README.md                  this file
├── LICENSE                    MIT
├── AGENTS.md / CLAUDE.md      agent working rules for this repository
├── CONTRIBUTING.md            contribution workflow
├── requirements.txt           pytest (the only hard dependency)
├── config.py, generate_corpus.py, pytest.ini
├── agents/                    agent-side system prompt (v2.1), parser, client, stage runner
├── configs/                   spend policy config (spend.json; see "What is not included")
├── data/
│   └── frozen/                frozen gate/pilot trace corpora used by offline checks
├── docs/                      specifications, decision log, runbook, manuscript draft
├── extraction/                message-extraction client, prompts, adjudication, writeback
├── figures/                   report figures and summary_figure.png
├── monitors/                  provider client, spend ledger, monitor prompts (views M1–M4)
├── scripts/                   CLI entry points (offline and provider-backed)
├── src/apart/                 study logic: plans, contrasts, labels, metrics
├── supplementary/             the release package: data, design, analysis, manifest
├── tests/                     pytest suite (offline)
├── audits/                    read-only audits of the numbers and the markdown layout
├── experiments/               the separate generation-probe experiment (not committed)
├── reports/                   design memos and report outputs (not committed)
├── results/                   run outputs — not released; produced locally
└── runs/                      raw run artefacts — not released; produced locally
```

## Reproducing the reported numbers

The release package lives in [`supplementary/`](supplementary/README.md). It contains the
released data, the analysis scripts, the design documents and a manifest that records the
sha256 of every file and where it came from. Reproducing every reported table is **offline and
needs no API key**:

```bash
cd supplementary/analysis
python run_all.py          # 35 checks against the reported values -> outputs/reproduction_report.md
```

See [`supplementary/README.md`](supplementary/README.md) for the full data dictionary and the
exact steps.

The **full test suite** additionally reads untracked run/result corpora (`runs/`, `results/`) that
are not in the repository, so `python -m pytest tests/` only passes end-to-end on a machine that
has those corpora checked out locally.

## Data

The released data, its per-file sha256 values, schemas and provenance are documented in
[`DATA.md`](DATA.md). The repository holds the code and the data context; the data files themselves
are shipped as a release asset:

- `apart_data_2026-10-25.zip` — sha256 `2f1f2a073f1cb62bdc7a8fcd59443a89bc8ac42df6d0d7abe3d29f9ef66f5905`
- **https://github.com/mahletm-777/Read-Between-the-Agent/releases/tag/v1.0**

Download and verify the asset, then reproduce the reported numbers from it, all offline:

```bash
python scripts/verify_data_asset.py --zip apart_data_2026-10-25.zip
```

`verify_data_asset.py` checks every entry in the asset's `SHA256SUMS.txt` and then runs the 35-check
supplementary reproduction against the asset's data.

## What is and is not included

**Included:** the source code, the offline analysis scripts, the released data payload under
`supplementary/data/`, the design documents, and the figure scripts.

**Not included, deliberately:** API keys and `.env`; spend ledgers, spend guards and spend policy
configs; per-call provider request/response payloads (`runs/*/raw*/`); account or billing
identifiers; `apart_code_review.zip`; and the raw annotation sheets that carry annotator names. The
two annotators appear only as **Annotator 1** and **Annotator 2**, in `supplementary/data/annotations/`.

This repository is a research artefact, **not a validated safety monitor**.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt            # pytest is the only hard requirement

python -m pytest tests/ -q                 # offline; needs run/result corpora for the full pass
cd supplementary/analysis && python run_all.py   # offline; 35 checks, no key
```

## License

MIT — see [LICENSE](LICENSE).
