# Glossary

**Not yet written.** No terms have been formally defined here. In the
meantime, term definitions already exist, scattered and sometimes
conflicting, in the following files:

- `docs/experiment_design.md` - defines the mock pipeline's own terms:
  agent policy, outcome definition (`mutual_cooperation`/`mutual_defection`/
  `mixed`), trial structure, and the `tau_y`/`tau_r` estimands.
- `docs/experimental_protocol.md` - defines a five-field annotation
  schema (`safety_outcome`, `coordination_assessment`, `deception_evidence`,
  `commitment`, `promise_breaking`) and the W/D/P/S branch names. **This
  file contains two concatenated drafts with conflicting definitions of
  some terms (including M1-M4); treat it with caution.**
- `docs/experimental_protocol_proposed.md` - defines a different, twelve-field
  annotation schema and a Follow/Divert display-label proposal for the
  A/B action vocabulary. **Proposed only; not approved.**
- `docs/open_questions.md` - retired in the release tidy-up; it explained, for each
  contested term (collusion, safety violation, M1-M4), which definitions conflict and why
  neither is settled. Its text remains in git history
  (`git show 14bbd1806fdde2b655b40057cdf74869ae388e59:docs/open_questions.md`). None of its
  open items were resolved by the removal.

A single, reconciled glossary should not be written until the definitions
above are reconciled — writing one now would silently pick a side in an
open question (the register that tracked those questions was
`docs/open_questions.md` Q1, Q3, Q4, Q6; it is now git history only).
