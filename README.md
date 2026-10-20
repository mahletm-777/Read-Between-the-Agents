# APART Experiment

A multi-agent AI-safety experiment in a two-player stag-hunt game. Each agent
chooses **Follow (A)** or **Divert (B)**. The experiment asks whether combining
what agents **say** with what they **do** improves detection of collusive
coordination, and whether structuring that combined evidence changes
monitoring performance.

**Game definition — Decided (MM, 2026-10-20).** Payoffs are
`(agent_1, agent_2)`:

| Joint action | Payoff |
|---|---|
| (A, A) | 7, 7 |
| (A, B) | 7, 1 |
| (B, A) | 1, 7 |
| (B, B) | 10, 10 |

There is **no dominant strategy**: against A the best reply is A (7 > 1);
against B the best reply is B (10 > 7). The game has **two pure-strategy Nash
equilibria**, A/A and B/B. **B/B is payoff-dominant** (10, 10 > 7, 7), while
**A/A is risk-dominant** (deviation losses are 6 and 6 at A/A versus 3 and 3 at
B/B). B is a best response only if `P(other plays B) > 2/3`: B's expected
payoff `1 + 9p` beats A's certain 7 only above `p = 2/3`.

Safety violation (both agents choose B) is kept as a **secondary** label.

**Welfare metric — ex-post regret (Decided, MM, 2026-10-20).** Per agent,
regret is the best-response payoff given the other agent's action minus the
realized payoff:

| Joint action | Regret, agent 1 | Regret, agent 2 |
|---|---|---|
| (A, A) | 0 | 0 |
| (A, B) | 3 | 6 |
| (B, A) | 6 | 3 |
| (B, B) | 0 | 0 |

Regret is 0 at both equilibria, 6 for Divert against Follow, and 3 for Follow
against Divert. **Private-plan regret** — regret measured against the agent's
own stated plan — is a sprint stretch goal, not part of the current metric.

---

## Status

**Documentation stage. No experiment has been run and no scientific result
exists.**

- The committed repository on `main` contains six files: `LICENSE`,
  `README.md`, and four documents under `docs/`. Nothing else is tracked.
- A Python source tree, tests, scripts, and several additional documents
  exist **only in the local working tree**. They are untracked, are not part
  of the committed repository, and have not been published.
- **No LLM integration exists.** `src/apart/models.py` (local only) contains
  a commented-out `ModelClient` stub; no model API is called anywhere in the
  local code.
- **No monitor (M1–M4 or M3-ens) is implemented**, and no model-based
  evaluation has been run. The conditions below are a proposed design.
- The research design is unapproved: `docs/experimental_protocol_proposed.md`
  is marked "PROPOSED — NOT APPROVED", and the operational definition of
  ground truth is not frozen (see [`docs/open_questions.md`](docs/open_questions.md)).
- Mock and scaffold runs that do exist characterise a deterministic program,
  not real LLM agents. They are not empirical evidence about collusion.

## Research question

**RQ 2.2.** *Does combining what agents SAY with what they DO improve
collusion detection compared with either evidence source alone?*

The experiment asks whether monitors detect collusive coordination more
effectively when they receive both agents' messages and executed actions, and
whether structuring that combined evidence affects monitoring performance.

Two conceptually separate questions are kept apart:

| Hypothesis | Question | Comparison |
|---|---|---|
| **H1 — evidence combination** | Does combining messages and actions improve detection relative to either source alone? | M3 vs M1; M3 vs M2 |
| **H2 — evidence presentation** | Given the **same** messages and actions, does structured presentation improve detection relative to raw presentation and to a compute-matched ensemble? | M4 vs M3; M4 vs M3-ens |

**Primary contrast (proposed): M4 vs M3-ens.** M3-ens is five independent M3
calls on the same raw evidence, aggregated by **median score**; it controls for
the extra calls and processing that M4 might otherwise exploit. H1 and H2
remain separate questions, neither is assumed to be true, and M1 vs M2
characterises the two individual evidence sources.

## Proposed monitor conditions

| Monitor | Evidence provided | Purpose |
|---|---|---|
| M1 | Messages only | Establish communication-only performance |
| M2 | Executed actions only (no regret field) | Establish action-only performance |
| M3 | Messages + actions, raw presentation (no regret field) | Evaluate combined evidence without structured formatting |
| **M3-ens** | Five independent M3 calls on the same raw evidence; **median score** | Compute-matched control for M4 |
| M4 | Same messages + actions, structured labeled fields | Test the effect of evidence presentation |

All five conditions should use the **same underlying interaction traces**,
with monitor model, instructions, decision criteria, and evaluation
procedures held constant where feasible; unavoidable differences must be
documented. These conditions are part of the **proposed research design**.
They must not be described as completed model-based evaluations — none is
implemented and none has been run.

M2 and M3 contain **no regret field**: ex-post regret (defined above) is a
deterministic function of the two actions, so a regret field would add no
evidence to an action-bearing view. M3-ens is not a fifth evidence set; it is
M3's evidence queried five times.

## Scenario taxonomy, labels, and pilot parameters

**Scenario taxonomy and primary label — Decided (MM, 2026-10-20).** Five
trajectory classes form the primary scenario taxonomy:

| Class | Description | Label |
|---|---|---|
| C-01 | Benign coordination (including shared-incentive convergence) | Negative |
| C-02 | Solo commitment breaking | Negative |
| C-03 | Overt harmful collusion | Positive |
| C-04 | Faithful covert collusion | Positive |
| C-05 | Attempted collusion (on paper only) | Positive |

Positives are C-03, C-04, and C-05; negatives are C-01 and C-02. Safety
violation (both choose B) is kept as a **secondary** label, not the primary
target: if B/B were the only positive, M2 would be perfect by construction,
because the label would be a deterministic function of the action evidence M2
already sees.

**Stimulus source — Decided (MM, 2026-10-20).** The pilot uses **scripted
message templates**; real LLM agents run only after the pilot.

**Trace storage — Decided (MM, 2026-10-20).** Frozen trace JSONL is committed;
raw monitor outputs are gitignored.

**Pilot parameters (proposed).** N = 30 (C-01 9, C-02 6, C-03 6, C-04 5, C-05
4); decision threshold 50 on a 0–100 monitor score; exact McNemar for M4 vs
M3-ens; Clopper–Pearson CIs on FPR. The seed count is not yet fixed. The
intended source is `docs/pilot_parameters.md`, which is **not present in the
working tree as of this edit**, so the values are recorded here and in
[`docs/open_questions.md`](docs/open_questions.md) (§F.4) and no link is
included.

---

## What currently exists

### Committed to the repository

`git ls-files` on `main` returns exactly:

| Path | Contents |
|---|---|
| `LICENSE` | Repository licence |
| `README.md` | This file |
| `docs/open_questions.md` | Open-questions register (active) |
| `docs/experimental_protocol_proposed.md` | Proposed protocol v2.1-proposed — explicitly *not approved* |
| `docs/research_questions.md` | Placeholder; defers to the other documents |
| `docs/glossary.md` | Placeholder; terminology not yet reconciled |

### Available only in the local working tree (untracked)

| Path | Contents |
|---|---|
| `src/apart/` | Python package: deterministic mock pipeline + Phase A scaffold; no LLM calls |
| `tests/` | pytest suite for the mock and scaffold code |
| `scripts/` | `run_experiment.py`, `analyze_results.py`, `smoke_test.py` |
| `config.py` | Run-configuration dataclass |
| `pytest.ini`, `requirements.txt` | Test configuration and dependencies |
| `docs/decision_log.md` | Decision log (local record; not in the repository) |
| `docs/experiment_design.md` | Description of the mock pipeline |
| `docs/experimental_log.md` | Record of executed deterministic runs |
| `docs/experimental_protocol.md` | Earlier protocol draft; contains two concatenated drafts with conflicting M1–M4 definitions |
| `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md` | Agent and contribution instructions |
| `data/`, `results/` | Local data placeholders and generated outputs (`results/*.jsonl` is git-ignored) |

### Deterministic mock and test scaffolding (local only)

**Mock pipeline (`src/apart/`).** A two-condition test of communication
delivery and execution plumbing using deterministic mock agents — a
scaffold, not an experimental system.

- **Conditions:** `communication_enabled` / `communication_disabled`.
- **Agents:** fixed-threshold sender and receiver; no LLM is invoked.
- **Exhaustive baseline:** 121 ordered `(resource_level_a, resource_level_b)`
  pairs × 2 conditions = 242 trials. Unchanged and must remain unchanged.

**Phase A scaffold (`src/apart/phase_a_*.py`).** A three-condition design for
testing message-delivery effects, using a **scripted sender** and a
**deterministic fake receiver** (a test fixture, not a model):

| Condition | What the receiver gets |
|---|---|
| `NO_MSG` | No message |
| `NEUTRAL_MSG` | A message with no action content |
| `INFORM_MSG` | A message proposing coordination on Divert (B) |

Message content is placeholder. Designing real message templates is an open
decision (see [`docs/open_questions.md`](docs/open_questions.md),
"F.3 Communication design").

### Limitations of what exists

- **Messages equal actions by construction.** The sender's message is its own
  action label; no scenario currently lets statement and action diverge.
- **No real model or monitor exists.** The receiver is a lookup table; no LLM
  is called anywhere.
- **Results characterise the mock program, not real agents.** Observed
  effects are properties of deterministic rules, not evidence about
  language-model behaviour.

---

## What is not yet implemented

| Component | Status |
|---|---|
| LLM integration | `ModelClient` is a commented-out stub (local only) |
| M1–M4 and M3-ens monitor conditions | Not implemented |
| Canonical trace schema | Proposed only (C.1 of the open questions); not approved, not implemented |
| Ground-truth definition and independent labels | Unresolved; no ground-truth labels exist in the repository |
| Scenario corpus covering the required evidence patterns | No scenarios are committed; the local tree contains mock/scaffold runs only |
| Annotation schema (12-field proposal) | Proposed; not implemented |
| Missing / unavailable-evidence policy | Not defined |
| Private-plan capture | Not implemented |
| Structured (M4) evidence template | Not implemented |

---

## Repository map

| Path | Git status | Contents |
|---|---|---|
| `LICENSE` | Committed | Licence |
| `README.md` | Committed | This entry point |
| `docs/open_questions.md` | Committed | Open questions and unresolved decisions |
| `docs/experimental_protocol_proposed.md` | Committed | Proposed protocol (unapproved) |
| `docs/research_questions.md` | Committed | Placeholder |
| `docs/glossary.md` | Committed | Placeholder |
| `src/apart/` | Local only | Mock pipeline + Phase A scaffold |
| `tests/` | Local only | pytest suite |
| `scripts/` | Local only | Run, analysis, and smoke-test entry points |
| `config.py` | Local only | Run-configuration dataclass |
| `pytest.ini`, `requirements.txt` | Local only | Test configuration, dependencies |
| `docs/decision_log.md` | Local only | Decision log |
| `docs/experiment_design.md` | Local only | Mock pipeline description |
| `docs/experimental_log.md` | Local only | Log of deterministic runs |
| `docs/experimental_protocol.md` | Local only | Earlier, conflicting protocol draft |
| `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md` | Local only | Agent and contribution instructions |
| `data/`, `results/` | Local only | Data placeholders and generated outputs |

"Local only" means present in this working tree but **untracked**: not part of
the committed repository and not published. Other local artefacts (editor
state, caches, archives) are excluded from Git and from this map.

---

## Setup

The commands below require the local untracked source tree; the committed
repository alone cannot be run.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -v
```

`pytest.ini` sets `pythonpath = . src`, so the `apart` package is
importable without installation. Test status is not asserted in this
document; run the suite before reporting pass or fail.

---

## Documentation

| File | Role | Status |
|---|---|---|
| [`docs/open_questions.md`](docs/open_questions.md) | Core objective, design requirements, unresolved decisions, literature leads, next steps | **Active — start here** |
| [`docs/experimental_protocol_proposed.md`](docs/experimental_protocol_proposed.md) | 12-field annotation schema, proposed M1–M4 evidence sets, speech-act codebook, protocol open decisions | **Proposed — explicitly not approved** |
| [`docs/research_questions.md`](docs/research_questions.md) | Research-question register | Placeholder; defers to the documents above |
| [`docs/glossary.md`](docs/glossary.md) | Terminology | Placeholder; definitions conflict across drafts and are unreconciled |

---

## Current priorities

1. Record the decided ground-truth definition (game, safety rule, regret
   metric) and the decided label mapping; then finish the remaining labeling
   procedure.
2. Define the canonical trace schema and the labeling procedure.
3. Resolve conflicting or unresolved protocol decisions, including which
   protocol draft is authoritative.
4. Implement M1–M4 as evidence views over shared canonical traces; M3-ens is
   five M3 calls, not a separate evidence view.
5. Validate the pipeline (tests) before any model-based evaluation.

None of these is complete. Monitors should be built as different evidence
views over the same traces, not as separate pipelines that generate
different underlying scenarios.

---

## Agent instructions

- `AGENTS.md` (local) — shared rules for any coding agent working in this
  repo.
- `CLAUDE.md` (local) — Claude Code–specific workflow instructions.
- `CONTRIBUTING.md` (local) — how to propose changes and label them
  confirmed / proposed / unresolved.