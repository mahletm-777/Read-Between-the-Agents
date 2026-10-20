# Contributing

This is a small research collaboration. The goal is reviewable, reversible
changes to a work-in-progress experiment - not rapid feature development.

## Before you start

1. Read `README.md`.
2. Read `AGENTS.md` - shared, tool-agnostic rules for any coding agent
   working in this repository; they apply to human contributors too.
3. Read `docs/decision_log.md` - it is the record of confirmed decisions.
4. Read `docs/open_questions.md` - the register of unresolved research and
   process questions, with what each one blocks.
5. Note the distinction between **confirmed**, **proposed**, and **unresolved**
   material: `docs/experimental_protocol_proposed.md` is a proposal, not an
   approved protocol.

## Workflow

1. **Use issues** to discuss research questions, design choices, and bugs
   before opening a pull request.
2. **Work on a branch** (`git checkout -b <topic>`); do not push directly to
   `main`.
3. **Open a focused pull request** with a clear description of what changed and
   why.
4. **Run the test suite** before proposing code changes, where practical:
   ```bash
   python -m pytest tests/ -v
   ```
5. **Get project-owner approval** before changing experimental semantics,
   schemas, conditions, outcome definitions, or introducing external model /
   API calls.

## Labeling changes

When a change touches the experiment, label it in the pull-request description
as one of:

- **Confirmed** - matches an already-recorded decision.
- **Proposed** - a suggestion that has not been approved.
- **Unresolved** - an open question being investigated.

Do not mark proposed or unresolved material as confirmed.
