# Agent Instructions

Shared, tool-agnostic rules for any coding agent (Claude Code, Cline, or
otherwise) working in this repository. Tool-specific workflow instructions
live in each tool's own file (e.g. `CLAUDE.md`); this file is the shared
baseline underneath them. See `README.md` for what this repository
contains and how to set it up, and `CONTRIBUTING.md` for the contribution
workflow.

- Work only within this repository. Do not inspect or modify the unrelated
  nested repositories (`Colosseum/`, `LLM-Trust-Breaking/`), or files
  outside this repository, unless explicitly asked.
- Respect explicit scope boundaries given in a task. A request to audit,
  review, or document something is not a request to implement it.
- Preserve the legacy communication-delivery pipeline (`src/apart/environment.py`,
  `agents.py`, `conditions.py`, `runner.py`, `evaluation.py`, `exhaustive.py`,
  `storage.py`) unless a specific change to it is explicitly approved.
- Keep three things separate and do not blur them: an **engineering smoke
  test** (the pipeline runs and records round-trip), an **experimental
  pilot** (a small scoped batch of traces for inspection), and a
  **scientific evaluation** (a claim about communication effects, safety
  violations, deception, coordination, or monitor performance). See
  `docs/open_questions.md` for the full distinction.
- Never represent scaffolding, synthetic/mock data, placeholder stimuli, or
  successful execution as evidence for a scientific hypothesis.
- Do not invent or silently resolve research decisions (estimands, safety
  definitions, protocol authority, monitor definitions, annotation
  schemas, scenario content). If a decision is needed, raise it rather
  than picking one.
- Record confirmed decisions in `docs/decision_log.md` and unresolved
  questions in `docs/open_questions.md`. Do not retroactively reinterpret a
  proposal in either file as an approval.
- Do not change experiment semantics, scientific labels, primary
  estimands, message treatments, or monitor definitions without explicit,
  recorded approval.
- Do not inspect unrelated nested repositories or private local
  configuration (e.g. `.claude/settings.local.json`) unless the task
  requires it and says so.
- Do not access the network, invoke external models, install dependencies,
  or operate on files outside this repository unless explicitly
  authorized for that specific action.
- Do not initialize Git, stage files, commit, push, or configure remotes
  without explicit authorization for that specific action.
- Verify tests by actually running them before claiming they pass. If
  tests were not run, say so explicitly rather than reporting a pass/fail
  status.
- Keep documentation changes focused and factual. Avoid speculative
  frameworks, unnecessary abstractions, or filling empty sections with
  invented content.
