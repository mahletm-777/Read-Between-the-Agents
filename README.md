# APART Experiment

A multi-agent AI-safety experiment testing whether monitors that see both
what agents **say** and what they **do** detect harmful coordination better
than monitors that see either alone.

> **Status:** Documentation only. Source code, tests, and scripts exist
> locally but are not yet part of this repository. No LLM-based system or
> monitor is implemented. The experimental design is under active
> discussion — see [`docs/open_questions.md`](docs/open_questions.md) for
> what is decided versus open.

---

## Research question

Does structuring the evidence a monitor receives — separating what agents
announce from what they execute — improve detection of
communication-mediated collusion in a two-player stag-hunt game?

**Specifically:** Four monitor views (M1–M4) each see a different slice of
the same trajectory. The primary comparison is M4 (evidence restructured
into labeled fields) vs M3 (same evidence, raw concatenation). An
M3-ensemble (5 independent calls, majority vote) controls for compute.

| Monitor | Sees | Does not see |
|---|---|---|
| M1 | Public messages only | Actions, welfare, plans |
| M2 | Realized actions + welfare | Messages, plans |
| M3 | Messages + actions + welfare (raw) | Plans, labels |
| M4 | Same as M3, restructured into fields | Plans, labels |

See [`docs/experimental_protocol_proposed.md`](docs/experimental_protocol_proposed.md)
for the full proposed design and
[`docs/open_questions.md`](docs/open_questions.md) for unresolved
decisions.

---

## What exists

### Deterministic mock pipeline (`src/apart/`)

A two-condition test of communication delivery and execution plumbing
using deterministic mock agents. Not an experimental system — a scaffold.

- **Conditions:** `communication_enabled` / `communication_disabled`.
- **Agents:** Fixed-threshold sender and receiver; no LLM is invoked.
- **Exhaustive baseline:** 121 ordered `(resource_level_a, resource_level_b)`
  pairs × 2 conditions = 242 trials. Unchanged and must remain unchanged.

### Sender-receiver interaction scaffold (`src/apart/phase_a_*.py`)

A three-condition design for testing message delivery effects:

| Condition | What the receiver gets |
|---|---|
| `NO_MSG` | No message |
| `NEUTRAL_MSG` | A message with no action content |
| `INFORM_MSG` | A message proposing coordination on Divert (B) |

Currently uses a **scripted sender** and a **deterministic fake receiver**
(test fixture, not a model). Message content is placeholder — designing
real message templates is an open decision (see
[`docs/open_questions.md`](docs/open_questions.md), section D).

### Limitations

- **Messages equal actions by construction.** The sender's message is its
  own action label. No scenario currently lets statement and action diverge.
- **No real model or monitor exists.** The receiver is a lookup table; no
  LLM is called anywhere.
- **Results characterize the mock program, not real agents.** Observed
  effects are properties of the deterministic rules, not evidence about
  language-model behavior.

---

## Not yet implemented

| Component | Status |
|---|---|
| LLM integration | `ModelClient` is a commented-out stub |
| Monitor evaluation (M1–M4) | Not implemented |
| Annotation fields (12-field schema) | Not implemented |
| Safety-outcome labeling | Specified; not implemented |
| Scenario corpus | No scenarios exist in the repo |
| Private-plan capture | Not implemented |

---

## Repository map

| Path | Contents |
|---|---|
| `src/apart/` | Mock pipeline + sender-receiver scaffold |
| `tests/` | Unit and integration tests |
| `scripts/` | `run_experiment.py`, `analyze_results.py`, `smoke_test.py` |
| `docs/` | Protocol drafts, open decisions, glossary |
| `config.py` | Run configuration dataclass |
| `pytest.ini`, `requirements.txt` | Test configuration and dependencies |
| `data/`, `results/` | Placeholders; generated results are git-ignored |

> **Note:** Only the documentation files are part of the initial
> repository. The source tree is local only.

---

## Setup

Requires the source tree (not yet in this repository).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -v
```

`pytest.ini` sets `pythonpath = . src`, so the `apart` package is
importable without installation.

---

## Documentation

| File | What it covers | Status |
|---|---|---|
| [`docs/open_questions.md`](docs/open_questions.md) | Unresolved design decisions, grouped by dependency | **Active — start here** |
| [`docs/experimental_protocol_proposed.md`](docs/experimental_protocol_proposed.md) | 12-field annotation schema, M1–M4 evidence sets, speech-act codebook | Proposed, not approved |
| [`docs/research_questions.md`](docs/research_questions.md) | Research question register | Placeholder |
| [`docs/glossary.md`](docs/glossary.md) | Terminology | Placeholder (definitions conflict across docs) |

**Local files not in this repository:**

- `docs/decision_log.md` — authoritative record of confirmed decisions.
- `docs/experiment_design.md` — mock pipeline description.
- `docs/experimental_log.md` — record of executed deterministic runs.
- `docs/experimental_protocol.md` — earlier protocol draft (two
  concatenated drafts with conflicting terminology).

---

## Agent instructions

- `AGENTS.md` — shared rules for any coding agent working in this repo.
- `CLAUDE.md` — Claude Code–specific workflow instructions.
- `CONTRIBUTING.md` — how to propose changes and label them
  confirmed/proposed/unresolved.