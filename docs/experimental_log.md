# Deterministic Two-Agent Exhaustive Baseline — Communication Ablation

**Date:** 2026-10-18
**Status:** Completed — deterministic mock experiment (no LLM, no API calls)
**Code under test:** `src/apart/exhaustive.py`, `src/apart/runner.py`, `src/apart/evaluation.py`
**Tests:** `tests/test_exhaustive.py`
**Scope:** Mock pipeline only. This is not an empirical evaluation of real LLM agents.

---

## A. OBJECTIVE

This experiment exhaustively evaluates the deterministic two-agent environment under the two
communication conditions, `communication_enabled` and `communication_disabled`. Instead of
sampling a handful of seeds, it enumerates **every possible** `(resource_level_a,
resource_level_b)` pair and runs each pair under both conditions using identical observations.

The purpose is twofold:

1. **Pipeline validation:** exercise the full defined observation state space end to end, rather
   than a small sample whose representativeness is unknown.
2. **Policy characterization:** describe exactly how the current deterministic policies behave
   under each condition over all defined inputs.

This is a **deterministic baseline**, not an empirical evaluation of real LLM agents. No model
client exists in the pipeline: the `ModelClient` protocol in `src/apart/models.py` is commented
out, and `config.py`'s `model_name` field is unused (also noted in `docs/experiment_design.md`).
Nothing here should be read as evidence about how a language model would behave.

---

## B. EXPERIMENTAL SETUP

All details below were verified against the source before being recorded.

- **Agents:** `agent_a` (sender) and `agent_b` (receiver), fixed and asymmetric roles
  (`src/apart/runner.py:42-43`).
- **Resource levels:** each agent's `resource_level` ranges over `{0, ..., 10}`, i.e. 11 possible
  values (`src/apart/exhaustive.py:22`, `RESOURCE_LEVELS = range(11)`), matching
  `TwoAgentEnvironment.initial_observations`'s `randint(0, 10)`.
- **Ordered observation pairs:** 11 × 11 = **121** (`enumerate_pairs`,
  `src/apart/exhaustive.py:25-27`).
- **Conditions:** every pair is run under both conditions in `ALL_CONDITIONS` =
  (`communication_enabled`, `communication_disabled`), with the **same observations** injected for
  a given pair across both conditions (`src/apart/exhaustive.py:39-52`).
- **Trial counts:** **242 trials total**, **121 per condition**. The exhaustive path uses no RNG;
  `TrialMetadata.seed` is reused as a deterministic pair index `a * 11 + b`
  (`src/apart/exhaustive.py:41`).

**Action and outcome definitions (verified in source):**

- **Sender policy:** `cooperate` if `resource_level > 5`, otherwise `defect`
  (`src/apart/agents.py:25-30`).
- **Communication enabled:** `delivered_message = message.message_content`, and the receiver
  returns that action verbatim, ignoring its own observation entirely
  (`src/apart/runner.py:54-57`, `src/apart/agents.py:26-27`). The message content is the sender's
  own action label (`compose_message`, `src/apart/agents.py:32-38`).
- **Communication disabled:** `delivered_message = None`, and the receiver applies the same
  resource-threshold policy to its **own** observation (`src/apart/runner.py:54`,
  `src/apart/agents.py:28-30`).
- **Message record:** exactly one `AgentMessage` per trial. It is always recorded; when withheld
  it carries `withheld=True`, `receiver_id=None`, `message_type="withheld"`, while
  `message_content` is preserved (`src/apart/conditions.py:32-50`). This keeps generated versus
  delivered messages distinguishable in the record.
- **Outcome labels:** `mutual_cooperation` if both final actions are `cooperate`,
  `mutual_defection` if both are `defect`, otherwise `mixed`
  (`src/apart/environment.py:25-33`).
- **Estimands:** `tau_y` = P(both cooperate | delivered) − P(both cooperate | withheld);
  `tau_r` = P(receiver cooperates | delivered) − P(receiver cooperates | withheld). Delivery
  status is read from each trial's recorded message, not inferred from the condition label
  (`src/apart/evaluation.py:49-56, 65-99`).

---

## C. OBSERVED EXECUTION RESULTS

The exhaustive experiment was executed directly through the Python module API with
`PYTHONPATH=src`, in memory (see Section G). The user's actual terminal output was:

```
total_trials: 242
total_pairs: 121

mutual_cooperation_rate_by_condition:
  communication_enabled: 0.45454545454545453
  communication_disabled: 0.2066115702479339

tau_y: 0.24793388429752064
tau_r: 0.0

action_change_count: 60
outcome_change_count: 60
```

### Reported test-suite result

- Reported result of the full suite: **54 passed in 0.09s** (command in Section G).
- The first run in that verification pass had **1 failure and 53 passes**: one test attempted to put
  an `AgentAction` dataclass instance directly into a `set`, and `AgentAction` is an unfrozen
  dataclass with no value-based hashing (`TypeError: unhashable type: 'AgentAction'`).
- The test was corrected to compare `t.actions["agent_a"].tool_name` instead.
- The correction changed **a test assertion only**; it did not change the experimental logic.
- **No production code was changed** during the verification pass.
- `results/raw_trials.jsonl` was reported unchanged: **6 lines**, MD5
  `7b537a4bf6c4667eb3153334898c8b53`.

### Provenance of these results

- The numbers in the code block above are the **user's directly observed terminal output**, reported
  for this entry. They were not re-executed during this documentation task.
- The **existing decision log** (`docs/decision_log.md`, 2026-10-18 entries) separately records
  hand-derived *expected* values computed from the agent rules before implementation, plus the
  test-run history and the `results/raw_trials.jsonl` check. The observed output agrees with those
  hand-derived values exactly.
- `docs/experiment_design.md` describes the exhaustive experiment's design and scope; it does not
  contain executed output.
- `scripts/run_experiment.py` and `scripts/analyze_results.py` were **not** executed as part of this
  verification.

---

## D. DERIVED FINDINGS

Exact fractions behind the decimals in Section C:

1. **Mutual cooperation, communication enabled:**
   `55/121` ≈ 0.4545, or **45.45%**.
2. **Mutual cooperation, communication disabled:**
   `25/121` ≈ 0.2066, or **20.66%**.
3. **Difference in mutual-cooperation rates (tau_Y):**
   `55/121 − 25/121 = 30/121` ≈ **0.2479**.

   This is an absolute difference of approximately **24.8 percentage points**, not a 24.8% relative
   increase. (For contrast, the *relative* increase would be `30/25 = 120%`, about 2.2× the
   withheld-condition rate.)
4. **Difference in receiver-cooperation rates (tau_R):**
   `55/121 − 55/121 = 0`, exactly.
5. **Receiver action changes across matched input pairs:** **60 of 121**.
6. **Joint-outcome changes across matched input pairs:** **60 of 121**.

**Why the action-change count is 60.** Of the 121 ordered pairs, `5 × 5 = 25` have both resource
levels above 5, and `6 × 6 = 36` have both at or below 5. In those 61 pairs the enabled and
disabled policies agree. In the remaining `121 − 61 = 60` pairs the threshold statuses differ, so the
enabled receiver (copying the sender) and the disabled receiver (own threshold) take different
actions.

**Why the outcome-change count is also 60.** The sender's action is identical in both conditions. If
the receiver's action changes, the pair `(sender action, receiver action)` changes between agreement
and disagreement, which necessarily changes the outcome label to or from `mixed`. If the receiver's
action does not change, the outcome cannot change. The two counts therefore coincide exactly.

**Why the receiver's aggregate cooperation rate can remain unchanged while its action changes for 60
pairs.** Under `communication_enabled` the receiver's cooperation rate is
`P(resource_level_a > 5) = 55/121`, because it copies the sender, whose action is determined by the
sender's own level. Under `communication_disabled` it is `P(resource_level_b > 5) = 55/121`, from
its own level under the same range and threshold. The two marginal probabilities are equal, so
`tau_R = 0` exactly — even though the receiver's *conditional* action changes for 60 of 121 matched
pairs.

---

## E. INTERPRETATION AND LIMITATIONS

### Findings established within this deterministic model

- Communication increases mutual cooperation under the specified policies: 45.45% (55/121) with
  communication enabled versus 20.66% (25/121) with it disabled; `tau_Y = 30/121` ≈ 0.2479.
- The receiver's aggregate cooperation rate is unchanged: `tau_R = 0` exactly.
- The receiver changes its action for 60 of 121 matched observation pairs; the joint outcome changes
  for the same 60 pairs.
- Aggregate cooperation rates alone do not capture all conditional behavioral changes: an exactly
  zero `tau_R` coexists with 60 individual action changes.

### Limitations

- Receiver copying is explicitly programmed (`src/apart/agents.py:26-27`), not learned or emergent.
- Both agents use the same resource-level range and the same threshold, which structurally forces
  `tau_R = 0` (see Section D).
- Results characterize the current deterministic model only.
- The experiment does not establish emergent strategic coordination, deception, collusion, or
  misalignment.
- No statistical-significance claim should be made for this exhaustive deterministic contrast.
  Enumerating a finite deterministic space involves no sampling uncertainty.
- The design is single-round, two-agent, with fixed roles; `config.py`'s `max_turns` and
  `num_agents` remain unused (documented in `docs/experiment_design.md`).

### On interpreting tau_R = 0

`tau_R = 0` does **not** mean communication has no behavioral effect. It means the aggregate
receiver-cooperation rate is identical between the two conditions. The action-change count
(60 of 121) is the direct evidence that the receiver's behavior differs conditionally; the aggregate
rate simply does not detect it.

### Findings versus hypotheses

The bullets above are findings about a deterministic program. Any claim that this programmed
copying resembles, predicts, or models strategic coordination in real agents is a **hypothesis**, not
something this experiment establishes.

---

## F. OPEN QUESTIONS FOR THE NEXT EXPERIMENT

The following are unresolved research questions, not conclusions:

- How can mechanical copying be distinguished from strategic coordination?
- Which conditional behavioral measurements add useful information beyond aggregate cooperation
  rates?
- Could mutual information between sender behavior and receiver behavior help characterize
  dependence?
- What controls would distinguish communication effects from changes in prompts, incentives,
  observations, or policies?
- What observable criteria would operationally define collusion in a future experiment?

---

## G. REPRODUCIBILITY

Reported method for running the exhaustive experiment (run from the project root):

```bash
cd "<project-root>"
source .venv/bin/activate

PYTHONPATH=src python - <<'PY'
from apart.exhaustive import run_exhaustive_trials, summarize_exhaustive

trials = run_exhaustive_trials()
summary = summarize_exhaustive(trials)

print(f"Total trials: {len(trials)}")
print(summary)
PY
```

This runs the exhaustive experiment **in memory** and prints the summary; it does **not** explicitly
save the trials to `results/raw_trials.jsonl`. `PYTHONPATH=src` is required because the package lives
under `src/` (`pytest.ini` sets `pythonpath = . src`).

Test command:

```bash
python -m pytest tests/ -v
```

The reported result (`54 passed in 0.09s`) is **historical verification** from the user's execution.
Tests were **not** rerun during this documentation task.

---

**Related records:** `docs/decision_log.md` (2026-10-18 entries) holds the decision record,
pre-implementation hand derivations, and the test-run history; `docs/experiment_design.md` holds the
design description. This file is the experimental record of the executed run.

