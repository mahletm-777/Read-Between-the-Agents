# Experiment Design Notes (Mock Pipeline)

This describes the current mock pipeline only. It does not validate any
research hypothesis about multi-agent collusion or communication.

## Placeholders and simplifying assumptions

- **Agent policy** (`agents.py`): a fixed threshold rule (cooperate if
  resource_level > 5, else defect). This is a placeholder for a future
  policy driven by a real or mocked language model, not a claim about how
  a real agent would behave.
- **Outcome definition** (`environment.py`): a trial's outcome is one of
  `mutual_cooperation`, `mutual_defection`, or `mixed`, based only on the
  two agents' single-step actions. This is a minimal stand-in outcome
  space, not a validated measure of collusion.
- **Trial structure** (`environment.py`): one round of actions per trial,
  each agent getting its own independent observation (drawn from the
  seeded RNG, not shared). There is no multi-turn interaction yet, even
  though `config.py` defines `max_turns` for future use.
- **Roles are fixed and asymmetric** (`runner.py`): `agent_a` is the
  sender (its action and announced message always come from its own
  observation via the threshold rule, independent of the delivery
  condition); `agent_b` is the receiver (copies the sender's action if
  delivered, else falls back to the same threshold rule on its own
  observation). This is a deterministic positive control for the
  communication-delivery condition, not a claim about symmetric agents.
- **Communication condition** (`conditions.py`): controls only whether the
  sender's announced action is delivered vs. withheld, not its content.
  Message volume or correlated actions observed here should not be
  interpreted as evidence of collusion.
- **Estimands** (`evaluation.py`): `tau_y`/`tau_r` compute the delivered-
  vs-withheld difference in P(both choose B) / P(receiver chooses B),
  where B=cooperate (fixed mapping). See
  `docs/decision_log.md` (2026-10-17 entry) for why per-agent observations
  were necessary for these to be non-trivial.
- **Model interface**: no real model client exists yet. `config.py`'s
  `model_name` field is reserved for when a real provider is added.

## Exhaustive state-space validation (separate experiment)

`src/apart/exhaustive.py` runs a **second, separate** validation experiment
alongside the seed-based pipeline above. Instead of a handful of seeds, it
enumerates all 121 possible `(resource_level_a, resource_level_b)` pairs
(11 x 11, since each resource level is in `{0,...,10}`) and runs every pair
under both conditions, with identical observations for a given pair across
conditions.

- This characterizes the **deterministic mock agent's exact behavior over
  its full observation space** -- it is strictly more informative than the
  3-seed sample, which could not establish how representative its result
  was of the underlying rule. It is still **not** evidence about real LLM
  agents, for the same reason the seed-based positive control isn't: both
  are deterministic mock policies, not models of how a real agent would
  behave.
- The exhaustive tau_R = 0 (exactly) is a structural property of this
  specific design -- sender and receiver draw resource_level from the same
  range with the same threshold, so their marginal cooperation
  probabilities are mathematically equal under copying vs. under
  independent thresholding. This differs from the 3-seed result (tau_R =
  -1/3), which was a sample-dependent artifact of which three pairs seeds
  0/1/2 happened to produce -- see `docs/decision_log.md` (2026-10-18
  entry) for the full derivation and the care taken not to overstate this
  as a claim about statistical noise (there is no sampling uncertainty in
  an exhaustive enumeration of a finite space).
- The seed-based `run_trial`/`run_trials` API, `results/raw_trials.jsonl`,
  and all previously-reported seed-based values are unchanged by this.

## Known unused config fields

- `num_agents` and `max_turns` are defined in `ExperimentConfig` but not
  yet consumed by the pipeline (the environment is hardcoded to two agents
  and one round). Left in place pending a design decision on multi-agent /
  multi-turn support.
