# APART Project: Learning Mode

> Shared, tool-agnostic rules for any coding agent working in this
> repository (scope boundaries, approval requirements, the
> smoke-test/pilot/scientific-evaluation distinction) live in `AGENTS.md`.
> The sections below are Claude-Code-specific workflow instructions on top
> of those shared rules, not a replacement for them.

## My goal
I am learning Python by implementing a multi-agent AI safety
experiment. I want to understand the code, not merely obtain
working implementations.

## Working rules
1. Do not implement an entire component unless I explicitly ask.
2. Do not rewrite my code before reviewing my attempt.
3. Explain the underlying Python concepts in plain language.
4. Prefer hints and targeted fixes over complete solutions.
5. Review only the files and functions relevant to my question.
6. Do not inspect the entire repository unless necessary.
7. Do not refactor unrelated code.
8. Do not modify the experimental design without my approval.
9. Do not make external API calls.
10. Keep changes within this project directory.

## Development Approach: Scaffold First, Learn Through Review

### Primary objective
Build a functional, minimal, end-to-end mock implementation of the
APART multi-agent AI safety experiment.

I want to learn how the implementation works, but I have limited
time for initial development. Generate the initial project skeleton
and necessary boilerplate so I can focus my learning on understanding,
testing, and extending the implementation.

### Phase 1: Initial Implementation
- Inspect the existing project before making changes.
- Implement the agreed project structure and minimum viable mock
  experiment without requiring me to write every file manually.
- Prioritize a simple, runnable end-to-end pipeline.
- Use deterministic mock agents and avoid external API calls.
- Write focused unit tests and a smoke test.
- Run the tests and fix straightforward implementation bugs.
- Use readable, straightforward Python rather than elaborate
  abstractions or unnecessary frameworks.
- Do not implement real LLM integration until explicitly requested.
- Do not invent research hypotheses or silently change the
  experimental design.
- Clearly document assumptions and placeholder logic.

### Phase 2: Guided Learning and Review
When I ask to understand, review, debug, or extend the code,
switch to tutor mode.

In tutor mode:
1. Explain the purpose of the relevant component and its role
   in the overall experiment.
2. Trace the flow of inputs, processing, and outputs.
3. Explain relevant Python concepts in plain language.
4. Identify important implementation choices and their trade-offs.
5. Give me hints and small examples before revealing a complete
   solution when I am trying to solve a problem myself.
6. Ask one question at a time when quizzing my understanding.
7. Distinguish programming bugs from research-design decisions.

Do not automatically rewrite working code when I ask for an
explanation or a review.

### Phase 3: Incremental Development
When extending the implementation:
- Make small, targeted changes.
- Preserve existing working behavior unless a change is required.
- Explain the intended change before making substantial modifications.
- Ask for clarification before making consequential research-design
  decisions.
- Add or update focused tests for changed behavior.
- Avoid refactoring unrelated modules.
- Prefer targeted debugging over broad repository investigations.

### Review Expectations
When reviewing code, focus on:
1. Correctness.
2. Important edge cases.
3. Readability and maintainability.
4. Reproducibility and experimental integrity.

Keep reviews concise and prioritize the most consequential issues.
Do not repeatedly review unchanged code without a specific reason.

### Token Efficiency
- Avoid rereading or summarizing the entire repository when a
  targeted inspection is sufficient.
- Avoid generating lengthy explanations unless I request them.
- Do not duplicate existing documentation or code.
- Prefer focused tests over running the full suite after every
  trivial change.
- Do not use subagents or extensive planning for routine tasks
  unless there is a clear benefit.
- Do not generate elaborate frameworks or infrastructure beyond
  the agreed minimum viable implementation.

### Security and Scope
- Keep all project changes inside the APART Experiment directory.
- Do not modify Claude Code settings or security configurations.
- Do not weaken sandboxing or permission restrictions.
- Do not make external API calls or install dependencies without
  explicit approval.
- Do not transmit project files or delegate work externally.
- Preserve existing security-related project instructions.

## Approval Requirements

Obtain my explicit approval before:

- Sending prompts, files, or research data to external services.
- Making external API calls or network requests, including web
  searches, web fetches, and MCP/connector tools.
- Installing packages or changing the development environment.
- Deleting files or overwriting existing research artifacts.
- Running expensive, large-scale, or confirmatory experiments.
- Changing research hypotheses, estimands, treatment conditions,
  labels, evaluation metrics, or the preregistered protocol.
- Changing data schemas, exclusion criteria, or analysis plans.
- Committing, pushing, or publishing work.
- Expanding filesystem or network access.
- Changing `.claude/settings.json` or `.claude/settings.local.json`.
- Delegating tasks that transmit data to external models.

Before consequential actions, explain the proposed action,
its purpose, relevant risks, and expected cost.

Never interpret silence as approval.

Record each approved research decision in `docs/decision_log.md`
with the date, decision, rationale, and approver.

Subagents are bound by these same requirements. Never delegate
a task to avoid an approval step.
