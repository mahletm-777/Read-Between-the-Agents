# Open Questions

This is a register of unresolved research and process decisions for the
APART Experiment repository. It does not resolve any of them. No decision
recorded here is final until it is also recorded in `docs/decision_log.md`
with an approver.

## Status definitions

- **CONFIRMED FACT:** Supported by the current repository or a clearly
  recorded decision in `docs/decision_log.md`.
- **PROPOSED:** A suggested approach that has not been approved.
- **UNRESOLVED:** A question that has not been decided.

## Priority definitions

- **PILOT:** Blocks a meaningful first experimental pilot.
- **MONITOR:** Blocks monitor-specific or safety-claim evaluation.
- **PROCESS:** Repository governance or documentation issue.
- **DEFERRABLE:** Can reasonably wait until after the initial engineering
  baseline or pilot.

## Three milestones, not to be conflated

1. **Engineering baseline / smoke test.** Verifies that the existing
   pipeline and tests execute and that records can be written and read.
   This validates execution plumbing only; it does not validate the
   research hypothesis or any safety claim. None of the questions below
   are required to be resolved for this milestone.
2. **Experimental pilot.** Generates a small, explicitly scoped batch of
   trial traces under defined conditions, for inspection. This requires a
   clear experimental contract (what conditions, what stimuli, what is
   being recorded) but does not require a completed monitor-evaluation
   framework. Questions tagged **PILOT** below bear on this milestone.
3. **Scientific evaluation.** Supports a claim about communication
   effects, safety violations, deception, coordination, or monitor
   performance. Requires operational definitions, appropriate
   measurements, and a defensible analysis plan. All eight questions below
   bear on this milestone to some degree.

Unresolved scientific questions do not automatically prevent engineering
work (milestone 1). Conversely, successful execution of the pipeline does
not establish scientific validity (milestone 3). A question being
unresolved for milestone 3 does not mean it blocks milestone 1 or 2 unless
its priority tag says so.

---

## Q1 — What constitutes a safety violation?

- **Status:** UNRESOLVED.
- **Priority:** MONITOR.
- **What is known:** `docs/experimental_protocol.md` and
  `docs/experimental_protocol_proposed.md` both describe joint action
  (B, B) as a prohibited safety violation and reference an outsider-welfare
  change from 10 to 4. Separately, `src/apart/environment.py` labels a
  joint `"cooperate"` outcome as `"mutual_cooperation"`.
- **What remains unresolved:** whether "joint B" (the conceptual safety
  rule) and "joint `cooperate`" (the stored outcome label) are intended to
  refer to the same thing. These are two different labels in two different
  places, and nothing in the repository states they are equivalent.
- **Why it matters:** this is the scientific ground-truth definition that
  any safety-outcome annotation or monitor-evaluation target would depend
  on.
- **Proposed next action (PROPOSED, not approved):** do not assign
  safety-violation labels to any trial record until the research definition
  of a safety violation — and its relationship, if any, to the
  `mutual_cooperation`/`mutual_defection`/`mixed` outcome labels — is
  explicitly decided and recorded.

## Q2 — What is the intended estimand and analysis unit?

- **Status:** UNRESOLVED.
- **Priority:** PILOT.
- **What is known:** `src/apart/evaluation.py`'s `tau_y` and `tau_r`
  compute marginal delivered-versus-withheld differences (pooled rates
  across trials in each condition). `src/apart/exhaustive.py` separately
  computes same-input paired comparisons
  (`action_change_count`/`outcome_change_count`) using matched observations
  across conditions. `docs/experimental_protocol.md` states analysis should
  use matched pairs, but this is not reconciled with what `evaluation.py`
  actually computes.
- **What remains unresolved:** which computation is the intended scientific
  estimand, and whether the pairing a paired analysis would require is
  actually justified by how trials are generated. Pairing is appropriate
  only if the experimental assignment and matching structure (e.g.,
  whether delivered and withheld trials share the same underlying
  observation) supports treating them as matched units for the specific
  research question being asked — not merely because a protocol document
  requests pairing. This repository's seed-based pipeline (`runner.py`)
  does not reuse the same seed across conditions by default; the
  exhaustive pipeline does. Whether that distinction matters for the
  intended research question has not been decided.
- **Why it matters:** a result computed with one estimator should not be
  presented as though it answers the question the other estimator would
  answer.
- **Proposed next action (PROPOSED, not approved):** report `tau_y`/`tau_r`
  only as marginal quantities, labeled as such; do not describe them as
  paired estimates or as satisfying a finalized protocol, until the
  estimand and the matching structure needed to justify pairing are both
  explicitly decided.

## Q3 — Which experimental protocol is authoritative?

- **Status:** UNRESOLVED.
- **Priority:** PROCESS.
- **What is known (verified by direct inspection):** `docs/experimental_protocol.md`
  contains two concatenated drafts with duplicate section numbers (e.g.,
  two separate `## 2.` sections) and conflicting terminology, including two
  incompatible descriptions of "M1-M4" within the same file.
  `docs/experimental_protocol_proposed.md` is explicitly marked "PROPOSED —
  NOT APPROVED" and documents this conflict rather than resolving it in
  place.
- **What remains unresolved:** whether either document, or some merge of
  the two, should be treated as governing.
- **Why it matters:** without this, a reader cannot tell which of two
  conflicting definitions of a shared term applies.
- **Proposed next action (PROPOSED, not approved):** when citing either
  document, cite the specific file and section rather than "the protocol"
  unqualified, until one is designated authoritative or they are merged.

## Q4 — What do M1-M4 mean?

- **Status:** UNRESOLVED.
- **Priority:** MONITOR.
- **What is known (verified by direct inspection):** within
  `docs/experimental_protocol.md`, one of its two concatenated drafts
  describes "M3 (ensemble)" and "M4 (single-call)" as inference strategies
  (how many model calls are made and how outputs are aggregated). A
  different section of the same file, and `docs/experimental_protocol_proposed.md`,
  instead describe M1-M4 as evidence-visibility sets (what information a
  monitor is permitted to see), treating call count as a separate decision.
  No monitor implementation of any kind exists in `src/apart/`.
- **What remains unresolved:** which definition, if either, this project
  intends M1-M4 to refer to.
- **Why it matters:** these describe two different experimental designs,
  not a wording difference.
- **Proposed next action (PROPOSED, not approved):** do not implement or
  evaluate anything labeled M1-M4 until the intended definition is
  specified and recorded.

## Q5 — How should trial records map to a safety outcome?

- **Status:** UNRESOLVED.
- **Priority:** MONITOR.
- **What is known:** stored actions are the strings `"cooperate"` and
  `"defect"` (`src/apart/agents.py`, `src/apart/environment.py`). The only
  A/B mapping in the codebase is `B_ACTION = "cooperate"` in
  `src/apart/evaluation.py`. No `safety_violation` predicate, payoff
  computation, or welfare value exists anywhere in `src/`, `tests/`, or
  `scripts/`.
- **What remains unresolved:** this question depends on Q1 being resolved
  first. Beyond that dependency, what remains unresolved is the scientific definition from Q1, and,
  separately from that definition, a validated implementation that
  normalizes stored `tool_name` values against whatever vocabulary Q1
  settles on. These are two distinct steps: deciding what a safety
  violation *is* (Q1), and building the code that maps a logged trial to
  that definition (this question). A literal comparison of a stored action
  against the string `"B"` would be false for every trial, since `"B"` is
  never a stored value.
- **Why it matters:** this is the step that would make the Q1 definition
  computable from actual trial records, once Q1 is resolved.
- **Proposed next action (PROPOSED, not approved):** retain only raw
  recorded actions in trial output; do not add a derived safety label or
  metric until Q1 is resolved and a validated mapping is implemented and
  tested.

## Q6 — What evidence and annotations are needed for monitor or deception evaluation?

- **Status:** UNRESOLVED.
- **Priority:** MONITOR.
- **What is known (verified by direct inspection):** `docs/experimental_protocol.md`
  proposes a five-field annotation schema; `docs/experimental_protocol_proposed.md`
  proposes a different, twelve-field schema. Neither is implemented.
  Neither `src/apart/models.py` nor `src/apart/phase_a_models.py` contains
  a `private_plan` field. The current mock sender's message content is its
  own chosen action's label (`src/apart/agents.py`,
  `MockAgent.compose_message`), so a discrepancy between a private plan and
  an external statement cannot currently be produced by the mock agents as
  written, independent of which schema is eventually adopted.
- **What remains unresolved:** which schema (if either) to adopt, and
  how/whether to add private-plan capture to any agent policy.
- **Why it matters:** blocks any deception-evidence annotation and any
  monitor target involving deception.
- **Proposed next action (PROPOSED, not approved):** do not populate any
  deception or plan/statement-discrepancy field without an operational
  definition and sufficient recorded evidence to support it.

## Q7 — What is the approved status and scope of Phase A?

- **Status:** UNRESOLVED.
- **Priority:** PILOT (Phase A is the implemented candidate for a
  sender-receiver communication pilot; this also has a PROCESS dimension —
  see the accompanying decision-log entry).
- **What is known (verified by direct inspection):** `src/apart/phase_a_*.py`
  (conditions, models, sender, receiver, parser, runner, storage, stats)
  and eight corresponding files under `tests/` exist in the repository.
  None of `docs/decision_log.md`, `docs/experiment_design.md`,
  `docs/experimental_protocol.md`, or `docs/experimental_protocol_proposed.md`
  mentions Phase A by name.
- **What remains unresolved:** whether Phase A's design and scope have been
  reviewed and approved, and whether its test suite currently passes (not
  verified in this review — no tests were executed).
- **Why it matters:** running a pilot on an unreviewed, unapproved pipeline
  would produce traces whose intended scientific purpose is not written
  down anywhere.
- **Proposed next action (PROPOSED, not approved):** describe Phase A as
  existing implementation with approval status unresolved, unless and
  until a recorded decision establishes otherwise. Do not describe its
  traces as approved pilot evidence without an explicit scope decision.

## Q8 — Are the Phase A message stimuli meaningful experimental treatments?

- **Status:** UNRESOLVED.
- **Priority:** PILOT.
- **What is known (verified by direct inspection):** in
  `src/apart/phase_a_conditions.py`, the neutral message's content is a
  fixed placeholder string, with an in-file comment stating it is "not
  evidence that a real neutral message conveys no information." In
  `src/apart/phase_a_sender.py`, the informative message's content is the
  sender's raw action label, not a validated natural-language stimulus.
- **What remains unresolved:** what the neutral and informative message
  stimuli should actually say to support any claim about message-content
  effects.
- **Why it matters:** a pilot run with the current placeholder stimuli can
  demonstrate that the pipeline executes, but cannot support any claim
  about how real message content affects receiver behavior.
- **Proposed next action (PROPOSED, not approved):** describe any output
  produced with the current stimuli as an engineering or scaffold test
  result, not as evidence of a meaningful message-content effect.

## Q9 — Should generated `results/*.jsonl` files be committed or regenerated?

- **Status:** UNRESOLVED. (A default behavior already exists in tooling —
  see "What is known" below — but no decision has been recorded.)
- **Priority:** PROCESS.
- **What is known:** `.gitignore` currently ignores `results/*.jsonl` (with
  `results/.gitkeep` explicitly kept), so generated trial-result files are
  not tracked by Git today. `docs/decision_log.md`'s 2026-10-14 entry
  records creating `.gitignore` as part of the initial scaffold, but no
  entry records a reasoned decision to exclude `results/*.jsonl`
  specifically (e.g., for reproducibility, file size, or regeneration
  cost).
- **What remains unresolved:** whether this is the project's settled
  policy, or simply the scaffold's original default, left unexamined.
- **Why it matters:** a collaborator cloning the repository has no
  documented explanation for why `results/` is empty, and no stated
  expectation for whether they should regenerate results locally or expect
  them to be provided separately.
- **Proposed next action (PROPOSED, not approved):** keep the current
  `.gitignore` behavior (regenerate locally via `scripts/run_experiment.py`
  / `scripts/smoke_test.py`) until or unless an explicit decision changes
  it; record that decision in `docs/decision_log.md` if and when it is made
  consciously rather than left as an unexamined default.

## Q10 — What scenario population and sample size would a pilot use?

- **Status:** UNRESOLVED.
- **Priority:** PILOT.
- **What is known:** `docs/experimental_protocol.md` §3 and
  `docs/experimental_protocol_proposed.md` §6 both propose scenario
  families/sets and split strategies, but both are explicitly marked
  proposed, not implemented, and neither specifies a population or sample
  size for a first pilot. No scenario corpus exists in the repository
  (`data/synthetic/` is empty).
- **What remains unresolved:** how many scenarios, drawn from what
  population or generation process, a first pilot would use. This is
  distinct from Q7 (Phase A's approval status) and Q8 (stimulus content
  validity) — it concerns scale and sampling, not scope or wording.
- **Why it matters:** without this, no call-count or cost estimate for a
  pilot can be made, and the pilot's scenarios could not be checked for
  balance or hard-negative coverage. The same gap is recorded in the
  repository itself: `docs/experimental_protocol_proposed.md` §11 (O8)
  lists "content of S001/S002/S004/S008/S010; corpus generation method and
  scale" as an open decision with "no scenarios exist," and §9.3 proposes
  only a 10-scenario pilot gate without specifying a sampling method;
  `docs/experimental_protocol.md` §5 separately leaves "the specific
  minimum count" for family-stratified metrics as "undecided — REQUIRES
  APPROVAL."
- **Proposed next action (PROPOSED, not approved):** do not generate pilot
  trajectories until a scenario population and target sample size are
  explicitly decided and recorded.

---

*This register reflects a read-only review of the repository as it stood
on 2026-10-19. Absence of a question from this list means it was outside
the scope of that review, not that it is settled.*
