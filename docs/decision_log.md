# Decision Log

## Status convention

- **Confirmed:** A specific decision explicitly approved and recorded.
- **Proposed:** A suggested approach that has not been approved.
- **Unresolved:** A question that has not been decided.

An entry may contain material with more than one status. An approver listed on an entry does not mean that every hypothesis, proposed fix, or future action mentioned within that entry was approved.

Historical entries are preserved as written. Their individual claims should be interpreted in context; this convention does not retroactively approve proposed or unresolved material.

## 2026-10-14 — Approved external documentation research on Claude Code filesystem security settings

**Decision:** Conduct web research (WebSearch/WebFetch) against official Anthropic Claude Code
documentation (code.claude.com/docs) and release notes to verify the behavior of
`permissions.blockReadsOutsideWorkingDirectories` and `sandbox.filesystem.denyRead` in
Claude Code v2.1.300.

**Rationale:** Prior inline analysis (based only on in-session config and training-data memory)
could not confirm whether these are real, documented settings keys, which tools they cover, or
how they interact with the Bash sandbox and subagents. The user explicitly authorized external
network requests for this specific research purpose, overriding the default approval gate in
this project's CLAUDE.md for that one action.

**Approver:** mmolla (project owner), via explicit written authorization in-session.

**Verified facts (confirmed against official docs/changelog, sources below):**
- `permissions.blockReadsOutsideWorkingDirectories` is a real, documented key (settings
  reference + settings precedence-exceptions table). Requires Claude Code v2.1.257+; honored
  from any scope even if a managed source sets `false`.
- Its documented effect: "Make the file tools refuse reads outside the working directories in
  every permission mode." The docs classify Read-type/Grep as "Read-only" file tools distinct
  from Bash.
- The Bash sandbox docs state explicitly: "The sandbox covers shell commands only. Claude's file
  tools, MCP servers, and hooks run outside it." Bash/PowerShell/Monitor commands are governed
  instead by `sandbox.filesystem.*` (e.g. `denyRead`, `allowRead`, `denyWrite`), a separate OS-level
  mechanism.
- `sandbox.filesystem.denyRead` is a real, documented key (added v2.1.77 alongside `allowRead`).
- This project's `.claude/settings.json` sets `permissions.blockReadsOutsideWorkingDirectories: true`
  but defines no `sandbox.filesystem.denyRead` rule — only `denyWrite` entries.

**Untested assumptions / not independently verified this session:**
- Whether `blockReadsOutsideWorkingDirectories` explicitly covers Glob (docs say "file tools"
  generically; Glob was not individually named in the fetched text).
- Whether this setting, or `sandbox.filesystem.denyRead`, is force-inherited by subagents launched
  via the Agent tool in this exact version — general subagent docs say permissions are inherited,
  but no fetched source named this specific key in that context.
- Whether the historical bug reports of `denyRead` not being enforced for the Read tool or Bash
  (third-party-reported, marked resolved) are fully resolved in 2.1.300 specifically — not
  re-tested, per your instruction not to run filesystem tests.

**Outcome:** Full findings reported in-session. Net effect on this project as currently
configured: Bash-mediated reads of paths outside the project directory that are not on the
Seatbelt `denyOnly` list remain unrestricted by either mechanism, since no `denyRead` rule is set.

## 2026-10-14 — Approved sandbox.filesystem.allowRead fix (relative → absolute project path)

**Decision:** Changed `.claude/settings.json` → `sandbox.filesystem.allowRead` from `["./"]` to
`["[local APART project directory]"]`.

**Rationale:** Isolation-verification testing (TEST 2, this session) found `ls -la` on the
project root failing under Bash with `Operation not permitted`, despite `allowRead: ["./"]`
intending to carve the project directory back out of the broad `denyRead: ["~/"]` rule. The
session's dump of the compiled Bash sandbox profile showed the project's absolute path entirely
absent from the profile's allow carve-outs, consistent with the relative path `"./"` failing to
resolve at profile-compile time rather than resolving and losing to the deny rule. Switching to
an absolute path was identified as the smallest change that preserves the existing
`denyRead: ["~/"]` and `denyWrite` rules unchanged while targeting only the resolution gap.

**Approver:** mmolla (project owner), explicit "approve" in-session.

**Caveat:** This fix was not re-tested against a live `ls`/sandbox probe after being applied, per
the approver's instruction in this sub-task not to run tests. Effectiveness should be confirmed
in a future isolation check before relying on it.

**Unrelated note:** While appending this entry, found the prior log entry above (web-research
session) describes settings state inconsistent with what this session observed directly — see
in-session flag to the user; not altered here.

## 2026-10-14 — Filesystem isolation probe: Tests 3 and 4 (Read tool, Glob/Grep tools)

**Decision:** Recorded results of two user-directed isolation tests against
`/Users/Shared/apart_isolation_probe.txt`. No settings changed, no additional tests run.

**Test 3 (Read tool) — PASS.** Attempting to read the file was explicitly denied. Exact error:
`/Users/Shared/apart_isolation_probe.txt is outside [local APART project directory]; the
permissions.blockReadsOutsideWorkingDirectories setting blocks reads outside the working
directories. Ask the user to add the directory with /add-dir, or to remove that setting.`

**Test 4 (Glob/Grep tools) — INCONCLUSIVE.** Neither tool was available in this session (absent
from the active tool list and from `ToolSearch`'s deferred-tool index). No fallback to Bash or
another path was attempted. This establishes nothing about whether Glob/Grep would themselves be
blocked by the same or a different restriction.

**Outcome:** Overall filesystem isolation has **not** been fully verified. Test 3 only confirms
`permissions.blockReadsOutsideWorkingDirectories` covers the Read tool; Glob/Grep remain untested.
Critically, **Bash-mediated reads remain unverified by these two tests** — per the prior entry
above (2026-10-14, web-research session), Bash is governed by a separate mechanism
(`sandbox.filesystem.denyRead`), which this project's settings do not currently set. No inference
about Bash-path isolation should be drawn from Tests 3/4.

**Approver:** mmolla (project owner), via explicit in-session request to record these results.

## 2026-10-14 — Filesystem isolation probe: Test 2 rerun (Bash `pwd` / `ls -la .`)

**Decision:** Rerun Test 2 only, to check whether current filesystem configuration allows Bash
access inside the project directory. No settings changed, no workaround attempted, Tests 3/4 not
rerun.

**Test 2 — FAIL.**
- `pwd` succeeded, returning `[local APART project directory]`.
- `ls -la .` failed: `ls: .: Operation not permitted` (exit code 1).

**Outcome:** Bash can resolve the working directory but cannot list it under the current sandbox
configuration. Cause not diagnosed in this session — no further investigation was performed, per
instruction not to run additional tests.

**Approver:** mmolla (project owner), via explicit in-session request to record this result.

## 2026-10-14 — Hypothesis: Test 2 failure caused by stale compiled sandbox profile

**Decision:** Investigated why `pwd` succeeds but `ls -la .` fails (Test 2 rerun) by comparing
this session's compiled Bash sandbox config against `.claude/settings.json`. No commands run,
no settings changed.

**Hypothesis (not confirmed):** `.claude/settings.json` sets
`sandbox.filesystem.allowRead: ["[local APART project directory]"]` (the fix from the
2026-10-14 `allowRead` entry above), but this session's actual compiled read rule still lists
`/Users/mmolla` and `/Users` under `denyOnly`, with only the scratchpad tmp path carved out via
`allowWithinDeny` — no carve-out for the project directory. `pwd` doesn't exercise filesystem
read checks, so it succeeds regardless; `ls` does, so it surfaces the mismatch. This points to the
running session's compiled sandbox profile not reflecting the current settings file, but this has
**not** been confirmed.

**Proposed confirmation step (not performed):** Restart Claude Code (to force sandbox profile
recompilation from current settings) and rerun Test 2 (`pwd` / `ls -la .`).

**Approver:** mmolla (project owner), via explicit in-session request to record this diagnosis.

## 2026-10-14 — Diagnosis: Test 2 failure persists after restart; TCC special-folder access
identified as leading hypothesis

**Decision:** Diagnosed why sandboxed `ls -la .` fails (`Operation not permitted`) while `pwd`
succeeds, after a Claude Code restart did not resolve it. Inspected only
`.claude/settings.json` / `.claude/settings.local.json`; no files modified, no further tests run.

**Evidence:**
- `.claude/settings.json` sets `sandbox.filesystem.allowRead: ["[local APART project directory]"]` alongside `denyRead: ["~/"]`; `.claude/settings.local.json` adds no conflicting
  filesystem rules.
- This session's previously-observed compiled sandbox profile denies `/Users/mmolla`/`/Users`
  broadly, with no carve-out for the project path despite the `allowRead` entry.
- `ls: .: Operation not permitted` is EPERM, not EACCES ("Permission denied") — the signature of a
  macOS sandbox/TCC denial rather than ordinary Unix file-mode permissions.
- `pwd` doesn't perform a filesystem read syscall; `ls` does — consistent with a read-gated
  restriction specifically.
- The Test 2 failure is unchanged after a full Claude Code restart, which argues against "stale
  sandbox profile" as a sufficient standalone explanation.

**Hypotheses (not confirmed):**
- H1: the `allowRead` carve-out for the project path is not being compiled into the live Seatbelt
  profile, for reasons not identifiable from these two settings files alone.
- H2 (better fits restart-invariance): the project lives under `~/Desktop`, a macOS TCC-protected
  special folder; if the host app/terminal lacks the OS-level "Desktop Folder"/Full Disk Access
  grant, directory reads fail with EPERM independent of Claude Code's own sandbox config and
  independent of a Claude Code restart.

**Recommended smallest corrective action:** Grant the terminal/application running Claude Code
the macOS "Desktop Folder" (or Full Disk Access) permission via System Settings → Privacy &
Security → Files and Folders — an OS-level change requiring no edits to project files or
`.claude/settings.json`.

**Approver:** mmolla (project owner), via explicit in-session request to record this diagnosis.

## 2026-10-14 — Unsandboxed `ls -la .` succeeds; weakens TCC hypothesis, strengthens H1

**Decision:** User ran `pwd` and `ls -la .` outside the Bash tool's sandbox (via the `!` prefix,
same host app/Terminal that launched Claude Code) and shared the output. No action taken by
Claude; recorded as evidence only.

**Evidence:**
- Unsandboxed `pwd` returned `[local APART project directory]`, matching the sandboxed result.
- Unsandboxed `ls -la .` **succeeded**, listing the project contents normally (standard
  `drwxr-xr-x` permissions, owner `mmolla`, 13 entries including `.claude`, `CLAUDE.md`, `docs`,
  `src`, etc.) — no TCC or permission denial of any kind.

**Updated hypothesis weighting:** This weakens **H2** (macOS TCC "Desktop Folder" restriction on
the host app) from the prior diagnosis — the same host process/user context can read the
directory fine when not going through the sandboxed Bash path. It strengthens **H1** — the
failure is specific to commands routed through the sandbox's (Seatbelt) profile, not a restriction
on the directory or the host process itself.

**Approver:** mmolla (project owner), via explicit in-session request to record this result.

## 2026-10-14 — Diagnosis: settings-vs-compiled-profile mismatch; proposed `/add-dir` fix (not applied)

**Decision:** At the user's direction, stopped pursuing the TCC/Desktop-permission line and
re-inspected `.claude/settings.json` / `.claude/settings.local.json` to determine why the Bash
sandbox cannot list the project directory despite the configured allowance. No files or settings
edited; no new tests run.

**Evidence:**
- `.claude/settings.json` declares `sandbox.filesystem.denyRead: ["~/"]` with
  `allowRead: ["[local APART project directory]"]` as the intended carve-out.
- This session's previously-observed compiled Bash sandbox profile shows `read.denyOnly`
  containing `/Users/mmolla` and the broader `/Users`, while `read.allowWithinDeny` contains only
  a scratch tmp path — no carve-out for the project directory at all.
- This mismatch persisted across a full Claude Code restart; unsandboxed commands (same host
  process) succeed, isolating the cause to Bash sandbox profile compilation specifically.
- The project directory name (`APART Experiment`) contains the only space character among all
  paths in the compiled profile — a known source of bugs in path-list join/tokenization pipelines.

**Hypotheses (not confirmed):** H1a — `allowRead` is not honored by the compiled profile in this
version/config, regardless of path content. H1b — the space in the path specifically breaks
compilation of this carve-out. Not distinguished without a test outside current scope.

**Proposed fix (not applied):** Use Claude Code's built-in `/add-dir` command to register
`[local APART project directory]`, rather than continuing to hand-edit
`sandbox.filesystem.allowRead`, since `/add-dir` is the mechanism the Read-tool's own denial
message pointed to and may be the supported path that actually feeds the compiled Seatbelt
profile.

**Security implications (assessed, fix not applied):** Scoped to one already-intended directory;
does not alter `denyRead: ["~/"]`, `denyWrite`, or network rules; reversible; still subject to this
project's standing approval requirement for settings changes, so not executed without explicit
approval.

**Approver:** mmolla (project owner), via explicit in-session request to record this diagnosis.

## 2026-10-14 — `/add-dir` fix attempt: no-op; disproves proposed fix; repeat unsandboxed `ls` confirms stability

**Decision:** User ran `/add-dir` directly (not via Claude) to test the previously proposed fix,
then reran unsandboxed `ls -la .`. Recorded as evidence; no files/settings changed by Claude.

**Evidence:**
- `/add-dir "[local APART project directory]"` (quoted) **failed**: it concatenated the
  argument onto the current directory instead of treating it as an absolute path, producing a
  nonexistent path — a quoting/argument-parsing issue in `/add-dir` itself for paths containing
  spaces.
- `/add-dir [local APART project directory]/` (unquoted, trailing slash) returned "is
  already the current working directory" — a no-op, since the project root is already the
  implicit working directory rather than something `/add-dir` treats as a new addition.
- A repeat unsandboxed `ls -la .` succeeded again, identical to the earlier result — confirms
  stability of unsandboxed access, no new information beyond the earlier observation.

**Updated assessment:** The previously proposed `/add-dir` fix is **disproven** — it cannot apply
because the directory is already the working directory, not an unregistered one, yet the Bash
sandbox still fails to list it. This rules out "missing directory registration" as the cause and
further strengthens H1b (the space in the working-directory path breaking the Bash sandbox
profile's read carve-out for it), over H1a.

**Approver:** mmolla (project owner), via explicit in-session request to record this result.

## 2026-10-14 — Trailing-slash `allowRead` fix tested and reverted: Test 2 still FAILs

**Decision:** With explicit approval, changed `.claude/settings.json` →
`sandbox.filesystem.allowRead` from `["[local APART project directory]"]` to
`["[local APART project directory]/"]` (trailing slash), as a minimal, scope-preserving
experiment. User restarted Claude Code and Test 2 was rerun.

**Result — Test 2: FAIL (unchanged).**
- `pwd` succeeded: `[local APART project directory]`.
- `ls -la .` failed: `ls: .: Operation not permitted` (exit code 1).

**Outcome:** The trailing-slash hypothesis did not resolve the issue, even after a restart. Combined
with the `/add-dir` no-op result (the directory is already recognized internally as the working
directory, yet still lacks a read carve-out), this indicates the defect most likely sits inside
Claude Code's own sandbox-profile compilation, not in `settings.json` syntax reachable from this
project. No further evidence-supported configuration change is identified at this time; further
guessing was avoided per instruction.

**Action taken:** Per explicit approval, reverted `allowRead` back to
`["[local APART project directory]"]` (original, pre-experiment value). All other settings
(deny/ask permissions, `denyWrite`, `denyRead`, `sandbox.enabled`, `failIfUnavailable`,
`allowUnsandboxedCommands`, `network.allowedDomains`) were never touched.

**Approver:** mmolla (project owner), via explicit in-session request to apply, test, and revert
this change, and to record the result.

## 2026-10-14 — Built minimum viable mock pipeline scaffold

**Decision:** Implemented the full foundational scaffold requested in-session: `src/apart/`
(`models.py` additions, `agents.py`, `environment.py`, `conditions.py`, `runner.py`, `storage.py`,
`evaluation.py`), `scripts/{smoke_test,run_experiment,analyze_results}.py`,
`tests/test_{models,agents,environment,conditions,storage,evaluation}.py`, `pytest.ini`,
`requirements.txt`, `.gitignore`, and `docs/experiment_design.md`. Extended the user's existing
`config.py` (added a `conditions` field and validation) rather than overwriting it.

**Rationale:** The user explicitly asked for this scaffold (pasted, itemized request specifying
exact file structure, module responsibilities, and a runnable mock pipeline), which this
project's CLAUDE.md treats as the required explicit ask to implement a full component. The mock
agent policy, outcome labels, and communication-condition mechanics are documented in
`docs/experiment_design.md` as placeholders, not a validated research design. No external API
calls were made; the mock agent requires no API key or network access.

**Reused-vs-overwritten note:** `src/apart/models.py` already contained the user's own
hand-written `AgentAction`/`AgentMessage` dataclasses (different field names than originally
planned: `tool_name`/`tool_input`, `sender_id`/`receiver_id`/`withheld`/`message_type`). Per the
user's explicit choice when this conflict surfaced, all other modules were written to match the
user's field names instead of replacing them; `TrialMetadata`/`TrialResult` were added to the
same file since those did not previously exist.

**Bug found and fixed (user-authorized):** `AgentMessage.withheld: bool` had no default but
followed `receiver_id: Optional[str] = None`, which raises `TypeError` at class-definition time
in a dataclass. Reordered so `receiver_id` (the only field with a default) is last. The user
asked for this one fix directly ("Fix it yourself, then I'll rerun pytest") rather than applying
it themselves.

**Verification:** `pytest` — 18 passed. `python scripts/smoke_test.py` — passed (4 trials, both
conditions, save/load round-trip, outcome rate computed). `python scripts/run_experiment.py` then
`python scripts/analyze_results.py` — 6 trials, 0 failed, outcome rate reported by condition
(0.33 in each condition, as expected since the mock policy does not yet respond to
communication). All runs were executed by the user via the unsandboxed `!` prefix, since the
Bash tool's sandbox cannot access this project directory (see the prior entries above on the
unresolved Seatbelt `allowRead` issue).

**Outstanding items (not yet decided):** `config.py`'s `num_agents` and `max_turns` fields remain
unused by the pipeline (hardcoded to two agents, one round per trial); whether to wire them in or
remove them is an open question for the user to decide later.

**Approver:** mmolla (project owner), via the original itemized pasted request and explicit
follow-up approval ("Yes, log it in the decision log").

## 2026-10-17 — Communication-sensitive positive control: asymmetric sender/receiver roles

**Decision:** Redesigned the two-agent trial to be a deterministic positive control for the
communication-delivery condition, replacing the previous symmetric design (both agents compose
and receive a message, acting only on their own observation).

**New design, agreed via explicit Q&A in-session:**
- **Roles are fixed and asymmetric**, not symmetric peers: `agent_a` is the sender, `agent_b` is
  the receiver. The sender's action and announced message are always computed from its own
  observation via the existing threshold rule (`cooperate` if `resource_level > 5`, else
  `defect`), independent of the delivery condition.
- **Action mapping**: `cooperate = B`, `defect = A` (fixed, for interpreting τ_Y/τ_R below).
- **Receiver's rule**: if the sender's message is delivered, the receiver copies the sender's
  announced action verbatim, ignoring its own observation. If withheld, the receiver falls back
  to the same threshold rule, applied to its *own* observation.
- **Observations are now per-agent, not shared**: `TwoAgentEnvironment.initial_observation()` →
  `initial_observations(agent_ids)`, drawing one independent `resource_level` per agent from the
  seeded RNG. This was necessary: with a single shared observation, the sender's action and the
  receiver's withheld-fallback action are the same deterministic function of the same input, so
  τ_Y and τ_R would be exactly 0 by construction regardless of the delivery condition — not a
  useful positive control. Independent per-agent draws let the two conditions actually diverge.
- **Estimands** (now implemented in `evaluation.py`): `τ_Y = P(both choose B | delivered) -
  P(both choose B | withheld)`; `τ_R = P(receiver chooses B | delivered) - P(receiver chooses B |
  withheld)`, computed from stored trial records' `actions` and `messages[0].withheld` fields only
  — no numeric payoff/utility field was introduced (deferred until a payoff function is
  specified, per explicit scope limit this session).

**Rationale:** The user's pasted task specified the τ_Y/τ_R formulas and the sender/receiver
copy-or-threshold rule directly, but left the shared-vs-per-agent-observation question unresolved;
building it with a shared observation would have silently produced a degenerate (always-zero)
positive control. This was flagged and resolved via `AskUserQuestion` before any code was written,
per this project's rule not to invent research-design choices.

**Scope explicitly excluded (per direct instruction):** three-agent/coalition extension, LLM
integration, monitor/judge evaluation, and any numeric payoff field.

**Files changed:** `src/apart/environment.py`, `src/apart/agents.py`, `src/apart/runner.py`,
`src/apart/evaluation.py`, and `tests/test_{environment,agents,runner,evaluation}.py`.

**Approver:** mmolla (project owner), via explicit answers to in-session clarifying questions
before implementation.

## 2026-10-17 — Validated design: deterministic two-agent communication-intervention pipeline

**Decision:** No code change in this entry — documenting the now-validated design and its
confirmed expected values for the record, per the user's request.

**Design, as implemented (see prior entry above for full history):**
- `agent_a` is the sender, `agent_b` is the receiver (fixed, asymmetric roles).
- Conditions: `communication_enabled` (delivered) and `communication_disabled` (withheld).
- Identical seeds are run across both conditions so the two per-agent `resource_level`
  observations are identical in both — only the delivery condition differs between a paired run.
- Threshold rule (both the sender's action and the receiver's withheld-fallback action):
  cooperate if `resource_level > 5`, else defect.
- Receiver's rule: copies the sender's delivered action verbatim when delivered; otherwise
  applies the threshold rule to its own observation.
- Estimands (`src/apart/evaluation.py`):
  `τ_Y = P(both cooperate | delivered) − P(both cooperate | withheld)`
  `τ_R = P(receiver cooperates | delivered) − P(receiver cooperates | withheld)`
  Both exclude failed (errored) trials and raise `ValueError` if either delivery condition has
  zero completed trials.

**Expected values for seeds [0, 1, 2]** (deterministic — derived from each seed's fixed
`resource_level` draws, not from a probabilistic model):

| Seed | Sender's own choice | Receiver's own choice | Enabled (delivered) | Disabled (withheld) |
|---|---|---|---|---|
| 0 | cooperate | cooperate | both cooperate | both cooperate |
| 1 | defect | cooperate | receiver receives "defect", defects | receiver gets nothing, cooperates (own rule) |
| 2 | defect | defect | both defect | both defect |

Condition-level rates over these three seeds: mutual-cooperation rate = 1/3 in **both** conditions;
receiver-cooperation rate = 1/3 (enabled) vs. 2/3 (disabled). Six trials total, zero expected
failures. From these: **τ_Y = 0.0**, **τ_R = −1/3**.

**Interpretation, explicit:**
- **τ_Y = 0.0** means the *joint*-cooperation rate happens to be equal across the two conditions
  for this specific three-seed set. It does **not** establish that communication never affects
  joint cooperation in general — it is one deterministic data point from three fixed seeds, not a
  statistical result over a distribution of seeds, and the receiver's action can still change
  between conditions (seed 1) without moving the *joint*-cooperation rate, since cooperation by
  only one of the two agents doesn't count toward "both cooperate."
- **τ_R = −1/3** means, for this seed set, the receiver cooperates *less* often when the message
  is delivered than when it is withheld (1/3 vs. 2/3) — driven entirely by seed 1, where the
  sender announces "defect" and the receiver, on its own, would have cooperated. This is the
  positive control doing exactly what it's for: showing the receiver's behavior is causally
  sensitive to delivery, in a direction fully explained by the deterministic copy-or-threshold
  rule, not by anything statistical.

**Relevant tests** (see `tests/test_runner.py`, `tests/test_evaluation.py`):
- `test_end_to_end_run_save_load_and_tau_integration` — runs real trials for seeds [0,1,2] through
  `run_trials` → `save_trials` → `load_trials` → `tau_y`/`tau_r`, asserting the τ_Y=0.0/τ_R=−1/3
  values above.
- `test_withholding_isolation_receiver_ignores_sender_when_withheld` — checks seeds 1 and 6, which
  produce opposing sender/receiver own-choice actions under withholding; verifies the receiver
  uses its own observation rather than copying the sender when no message is delivered.
- `test_positive_control_receiver_copies_sender_when_delivered`,
  `test_direct_same_seed_cross_condition_positive_control` — same-seed, cross-condition checks
  that the receiver copies the sender when delivered and differs from its own-rule action when
  compared across conditions.

**Execution status:** The expected values above (τ_Y=0.0, τ_R=−1/3; six trials, zero failures)
were independently hand-derived and checked against the implementation by Claude during this
session's code review and test-writing, by tracing the deterministic per-seed arithmetic — not by
an observed pytest run inside this session (the sandboxed Bash tool cannot execute anything inside
this project's `.venv`, documented earlier in this log). **The user has reported that the full
test suite passes**; that report, not an in-session execution by Claude, is the basis for treating
these tests as passing. No discrepancy between the hand-derived expected values and the design
has been found in this session.

**Scope note:** This is a **deterministic mock experiment** validating the communication-delivery
pipeline's plumbing (does withholding actually prevent information flow; do the estimands compute
correctly from real trial records). It is not empirical evidence about real LLM agents, and the
`cooperate`/`defect` labels and the mock agent's threshold rule are not a model of collusion —
consistent with the placeholder status already recorded in `docs/experiment_design.md`.

**Approver:** mmolla (project owner), via this session's request to document the validated design
and reported test results.

## 2026-10-18 — Exhaustive state-space validation (separate experiment, seed-based pipeline unchanged)

**Decision:** Added a second, separate validation experiment (`src/apart/exhaustive.py`) that runs
every possible `(resource_level_a, resource_level_b)` pair — 11 × 11 = 121 pairs, since each
resource level is drawn from `{0, ..., 10}` — under both communication conditions, instead of the
three arbitrary seeds used previously. The existing seed-based `run_trial`/`run_trials` API,
`results/raw_trials.jsonl`, and all previously-reported seed-based values are unchanged.

**Why:** The prior validation (seeds [0, 1, 2]) demonstrated the pipeline's plumbing works, but
τ_Y and τ_R over 3 arbitrary seeds are not representative of the deterministic mock agent's full
behavior — a different arbitrary seed choice could have produced different numbers. Since the
agent rules are deterministic and the observation space is small (121 combinations), it's feasible
to compute τ_Y/τ_R *exactly*, over every possible case, rather than over a sample.

**Expected values, derived by hand from the agent rules BEFORE writing any code** (sender:
cooperate iff `resource_level_a > 5`; receiver: copies sender when delivered, else applies the
same threshold to its own `resource_level_b`):
- 242 trials total, 121 per condition, 0 failures (deterministic, no failure path for valid inputs).
- Mutual-cooperation rate, enabled = P(a>5) = 55/121 (≈0.4545).
- Mutual-cooperation rate, disabled = P(a>5 and b>5) = 25/121 (≈0.2066).
- Receiver-cooperation rate = 55/121 in **both** conditions.
- **τ_Y = 55/121 − 25/121 = 30/121** (≈0.2479).
- **τ_R = 55/121 − 55/121 = 0, exactly.**
- Receiver action-change count (same pair, differs between conditions) = 60/121.
- Joint-outcome-change count (same pair, outcome label differs between conditions) = 60/121 —
  coincides exactly with the action-change count, a structural consequence of how the environment
  collapses any sender/receiver disagreement into the single `mixed` label.
- No `mixed` outcomes occur under `communication_enabled` — structurally impossible, since the
  receiver's action always equals the sender's action when delivered.
These were verified in `tests/test_exhaustive.py` against the actual implementation's output, not
asserted on trust.

**Interpretation of τ_R = 0, stated carefully (no unsupported noise claim):** The previous 3-seed
result reported τ_R = −1/3. The exhaustive computation shows τ_R = 0 exactly over the full
121-pair space. The reason τ_R is exactly 0 here is structural, not a claim about randomness: the
sender's and receiver's resource_level values are drawn from the identical range with the
identical threshold rule, so their respective marginal cooperation probabilities — P(a>5) under
delivery via copying, and P(b>5) under withholding via the receiver's own rule — are mathematically
equal regardless of which specific values occur. The earlier 3-seed τ_R = −1/3 is therefore
**sample-dependent**: it reflects which three specific pairs happened to be drawn by seeds 0, 1, 2,
not a property of the underlying deterministic model. This is a factual description of what
changed between a 3-case sample and the full 121-case space; it is not a claim about statistical
significance, noise distributions, or anything requiring inferential statistics, since there is no
sampling uncertainty in an exhaustive enumeration of a finite deterministic space.

**Implementation notes:**
- `src/apart/runner.py` was refactored (not behavior-changed) to extract
  `run_trial_from_observations(...)`, shared by `run_trial` (seed-based) and
  `apart.exhaustive.run_exhaustive_trials()` (pair-based), so the sender/receiver/outcome logic
  isn't duplicated. `run_trial`'s and `run_trials`' public signatures and behavior are unchanged.
- `TrialMetadata.seed` is reused in the exhaustive path to hold a deterministic pair index
  (`a * 11 + b`), documented here explicitly since there is no RNG involved in that path — this is
  a reuse of an existing field, not a schema change.
- `action_change_count`/`outcome_change_count` are new functions in `apart/exhaustive.py` (not
  added to `evaluation.py`), since they require same-pair pairing across conditions, which
  `evaluation.py`'s existing functions (designed for marginal per-condition rates) don't do.
- No new dependencies, no network access, no changes to `results/raw_trials.jsonl`,
  `src/apart/{agents,conditions,environment,models,storage}.py`, or `scripts/*.py`.

**Scope note, as for the prior entry:** This is a **deterministic mock experiment**. The exhaustive
result characterizes exactly how this specific mock agent behaves over its full observation space
— it is not empirical evidence about real LLM agents, and the exact zero value of τ_R here is a
property of this particular deterministic rule design, not a general claim about communication
effects.

**Execution status:** The values above were independently hand-derived before any code was
written, then checked by Claude against the actual implementation's test assertions in this
session — not by an observed pytest run (the sandboxed Bash tool still cannot execute anything
inside this project's `.venv`, as documented earlier in this log). See the end-of-task report for
the exact command to run locally.

**Approver:** mmolla (project owner), via explicit approval of the implementation plan presented
earlier in this session.

## 2026-10-18 — Exhaustive validation: first in-session pytest execution; one test-only bug found and fixed

**Decision:** Continuing from the prior entry, a new session was handed the exhaustive-validation
task as "approved work" to implement. Repository inspection first (per this project's standing
rule to inspect before changing anything) found the work already fully implemented and documented
by the prior entry above: `src/apart/exhaustive.py`, `tests/test_exhaustive.py`, and both docs
files already matched the task's spec exactly, including all expected metrics. No reimplementation
was done. The only remaining gap was that the tests had never actually been executed in-session —
the prior entry's "Execution status" explicitly says the expected values were hand-derived and
checked against test assertions, not run.

**What was done:** Ran the full suite via the Terminal MCP tool (the sandboxed Bash tool still
cannot read or execute anything under this project directory — see the unresolved Seatbelt entries
above), using the existing `.venv`: `.venv/bin/python -m pytest -q`.

**First run — 1 failed, 53 passed:**
`tests/test_exhaustive.py::test_same_pair_gives_identical_observations_across_conditions` failed
with `TypeError: cannot use 'apart.models.AgentAction' as a set element (unhashable type:
'AgentAction')`. Cause: `AgentAction` (`src/apart/models.py`) is a plain, unfrozen `@dataclass`,
so it has no value-based `__hash__`; the test tried to put `AgentAction` instances directly into a
`set` to check they were identical across conditions. This is a bug in the test's assertion
mechanics, not in the exhaustive-validation logic itself or the experimental design — the rest of
`test_exhaustive.py` and `exhaustive.py`'s own `action_change_count` already compare `.tool_name`
instead of the dataclass instance.

**Fix applied (one line, test file only):** In `tests/test_exhaustive.py`, changed
`sender_actions = {t.actions["agent_a"] for t in by_condition.values()}` to
`{t.actions["agent_a"].tool_name for t in by_condition.values()}`, matching the pattern already
used elsewhere in the same file and in `exhaustive.py`. No production code
(`src/apart/*.py`) was changed.

**Second run — all tests pass:** `54 passed in 0.09s` (54 = the pre-existing 53 passing tests, now
54 after this test's assertion was fixed so it runs to completion; no test was added or removed).
This is the first time in this project's history that the full suite's pass/fail result was
directly observed by Claude in-session (prior sessions relied on the user's reported results or
hand-derivation, per the sandboxed-Bash limitation).

**Verified unchanged:** `results/raw_trials.jsonl` — 6 lines, MD5
`7b537a4bf6c4667eb3153334898c8b53`, not modified by this session. No dependencies installed, no
network access, no files outside `tests/test_exhaustive.py` and this log changed.

**Docs:** `docs/experiment_design.md` was reviewed and found to already accurately describe the
exhaustive experiment (added in the prior entry); no content there depended on the fixed test's
assertion mechanics, so no change was needed there this session.

**Approver:** mmolla (project owner), via the pasted task's standing authorization to run tests
and fix straightforward implementation bugs, and via this project's CLAUDE.md Phase 1 instruction
to do the same.

**Experimental record:** See `docs/experimental_log.md` (2026-10-18 entry) for the executed run's
observed output, derived findings, limitations, and reproducibility steps. That file is the detailed
experimental record; this log remains the decision record.

## 2026-10-19 — Status record: Phase A pipeline and protocol-document state (not a decision)

**Type:** Status record, not a research or process decision. This entry approves nothing. It exists
because the two facts below were not previously reflected anywhere in this log.

**Finding 1 — Phase A pipeline exists without a prior decision-log entry.** `src/apart/phase_a_*.py`
(conditions, models, sender, receiver, parser, runner, storage, stats) and eight corresponding files
under `tests/` exist in the repository. No prior entry in this log mentions Phase A, and it is not
described in `docs/experiment_design.md`, `docs/experimental_protocol.md`, or
`docs/experimental_protocol_proposed.md`. Whether its tests currently pass has not been verified as
part of recording this entry — no tests were run. **This entry does not approve, scope, or validate
Phase A.** Its status remains: present in the repository, not reviewed, not approved for use beyond
its own test files.

**Finding 2 — Two protocol documents exist; neither is designated as governing this project.**
`docs/experimental_protocol.md` contains two concatenated drafts with duplicate section numbering
and conflicting label vocabularies, including two incompatible definitions of "M1-M4" within the
same file (verified by direct inspection). `docs/experimental_protocol_proposed.md` is a separate
document explicitly marked "PROPOSED — NOT APPROVED" that documents this conflict without resolving
it. **This entry does not designate either document as authoritative.**

**Rationale for recording this now:** both gaps were identified while comparing the state of this
log against the contents of `src/apart/` and `docs/`. Recording them here, without resolving them,
keeps this log's own record accurate about what has and has not been decided.

**Decision:** None recorded.

**Approver:** Not applicable — no decision was made.

**Open follow-up:** `docs/open_questions.md`, Q3 and Q7.
