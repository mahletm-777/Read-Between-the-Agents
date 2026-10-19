# APART Experiment

Work-in-progress research scaffold for a multi-agent AI-safety experiment on
communication-mediated coordination. This repository is **not finalized**: the
experimental design, action-label semantics, and analysis protocol are still
under discussion. See [Documentation](#documentation) for what is confirmed
versus proposed.

> **Status:** in development. This is a deterministic mock pipeline plus a
> scaffolded "Phase A" design. It is **not** pilot-ready, and it does **not**
> contain a real LLM-based experimental system.

## Research question (provisional)

A current research direction for this project is whether combining what
agents **say** (public announcements/messages) with what they **do** (final
actions) improves detection of communication-mediated collusion, compared to
looking at either alone. This is stated here as a **provisional direction,
not a settled question**:

- The operational definition of "collusion" (as distinct from a bare safety
  violation, mutual cooperation, or harmful-but-undelivered coordination) is
  **unresolved** — see `docs/open_questions.md`, Q1.
- Scenario design (what scenarios would let an agent's statement and its
  action actually diverge) is **unresolved** — the current deterministic
  mock pipeline's messages are constructed to equal the announcing agent's
  own action, so no existing scenario currently lets them differ.
- The monitoring protocol (what a monitor would see, and how its output
  would be scored) is **unresolved and internally inconsistent** between the
  two existing protocol drafts — see `docs/open_questions.md`, Q3 and Q4.
- **The existing deterministic mock pipeline cannot, by itself, establish
  whether combining statements and actions improves detection.** No monitor
  of any kind is implemented, and the message-equals-action construction
  above means statement/action divergence — the thing such a comparison
  would need to detect — cannot currently be produced by this pipeline.

See `docs/open_questions.md` for the full register of what is blocking this
direction, and `docs/experimental_protocol.md` / `docs/experimental_protocol_proposed.md`
for the (unapproved, mutually conflicting) design drafts this direction sits
inside.

## What exists

### Legacy deterministic pipeline (`src/apart/`)

A two-condition test of communication delivery and execution plumbing, using
deterministic mock agents:

- Conditions: `communication_enabled` / `communication_disabled`
  (`src/apart/conditions.py`).
- `MockAgent` uses a fixed threshold rule; the receiver copies the sender's
  delivered action or falls back to its own threshold (`src/apart/agents.py`).
- `TwoAgentEnvironment` draws independent `resource_level` observations and
  labels outcomes (`src/apart/environment.py`).
- Two analyses: a seed-based run (`src/apart/runner.py`) and an exhaustive
  11 x 11 = 121-pair enumeration (`src/apart/exhaustive.py`).

The legacy pipeline is a deterministic test of the communication-delivery and
execution plumbing. Its results should not be interpreted as evidence about
real agents or as validation of the proposed safety hypothesis.

### Limitations of the current deterministic mock fixtures

Both the legacy and Phase A pipelines use fixed, non-learned, non-LLM logic.
Concretely, this means:

- **Messages equal actions by construction.** The sender's announced message
  is always its own chosen action's label, verbatim (legacy), or the raw
  action label under the "informative" condition (Phase A). No scenario
  currently lets an agent's statement and its action disagree.
- **No real model or monitor is invoked anywhere.** `ModelClient` in
  `src/apart/models.py` is a commented-out protocol stub; Phase A's
  "receiver" is a lookup table of predefined responses
  (`DeterministicFakeReceiver`), not a decision procedure.
- **Results characterize the mock program, not real agents.** Any observed
  effect (e.g. communication increasing a cooperation rate) is a property of
  this specific deterministic rule set, not evidence about how a real
  language-model agent would behave. See `docs/experimental_log.md` §E for
  the fuller discussion of this limitation on the one executed experiment.

### Phase A pipeline (`src/apart/phase_a_*.py`)

A scaffolded three-condition design: `no_message` / `neutral_message` /
`informative_message` (`src/apart/phase_a_conditions.py`). It uses a scripted
sender, a **deterministic fake receiver** (a test fixture, not a model), a
synthetic A/B response parser, and McNemar/power utilities. No real model is
invoked.

## Not implemented

- **No LLM / model integration.** `ModelClient` is a commented-out protocol
  (`src/apart/models.py`); the receiver is a deterministic mock or fake.
- **The monitor-evaluation components described in the protocol are not
  implemented in code** (monitors, annotation fields, private-plan capture,
  safety-outcome labeling, and M1-M4 evidence sets).

## Repository map

| Path | Contents |
|---|---|
| `src/apart/` | Legacy mock pipeline + Phase A scaffold |
| `tests/` | Unit/integration tests (including `test_phase_a_*`) |
| `scripts/` | `run_experiment.py`, `analyze_results.py`, `smoke_test.py` |
| `docs/` | Protocol drafts, decision log, design/experiment notes |
| `config.py` | Run configuration dataclass |
| `pytest.ini`, `requirements.txt` | Test configuration and dependencies |
| `data/`, `results/` | Input/output placeholders; generated results are git-ignored |

## Setup and tests

1. Create and activate a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
2. Install the (single) declared dependency:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the test suite:
   ```bash
   python -m pytest tests/ -v
   ```
   `pytest.ini` sets `pythonpath = . src`, so the `apart` package under `src/`
   is importable without installation.

## Project instructions

- `AGENTS.md` - shared, tool-agnostic rules for any coding agent working in
  this repository.
- `CLAUDE.md` - Claude-Code-specific workflow instructions, on top of the
  shared rules in `AGENTS.md`.
- `CONTRIBUTING.md` - how to propose a change, and how to label it
  confirmed/proposed/unresolved.

## Documentation

- `docs/decision_log.md` - **authoritative record of confirmed decisions.**
- `docs/experiment_design.md` - description of the current mock pipeline.
- `docs/experimental_log.md` - record of executed deterministic runs.
- `docs/experimental_protocol.md` - protocol draft containing proposed
  approaches (two concatenated drafts with conflicting terminology; treat with
  caution).
- `docs/experimental_protocol_proposed.md` - proposed revision containing
  proposed approaches; **not approved.**
- `docs/research_questions.md` - placeholder; the research question has not yet
  been consolidated into a formal, numbered list.
- `docs/glossary.md` - terminology currently under discussion; no terms are
  formally defined yet.
- `docs/open_questions.md` - unresolved design decisions (research and process
  questions), with status, priority, and what each one blocks.

## Open design questions (unresolved)

All currently tracked unresolved design and process questions are listed in
`docs/open_questions.md`, with status, priority, and what each one blocks.
They are not restated here, to avoid two descriptions of the same question
drifting out of sync. As of this writing that register covers: the
safety-violation definition, the estimand/analysis unit, which protocol
draft governs, the M1-M4 monitor definitions, mapping trial records to a
safety outcome, the annotation/private-plan schema, Phase A's approval
status, the Phase A message-stimulus content, the `results/*.jsonl`
commit-versus-regenerate policy, and the scenario population/sample size
for a future pilot.

