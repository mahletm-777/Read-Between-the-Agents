### Critical Review of the Proposed APART Experimental Protocol

As a skeptical reviewer evaluating this pilot design against the multi-agent AI safety literature, I have audited the proposed labels, scenarios, evidence views, monitor configurations, and call arithmetic. 

While the pilot addresses a timely research question—**whether combining public statements with observed actions improves collusion detection**—several methodological confounds and validity threats must be addressed before freezing the protocol.

---

### 1. The Five Most Serious Threats to Validity

#### 1. Confounding Evidence Representation with Rubric Guidance
* **The Threat**: View **M4 (Structured Report)** provides the monitor with explicit JSON fields (such as `claim_vs_action_alignment`) and structured field definitions, whereas View **M3 (Raw Combined Evidence)** provides raw, unparsed transcript text. If M4 outperforms M3, the performance gain may stem from **explicit prompt guidance** (instructing the model *how* to compare claims against actions) rather than the structured layout itself.
* **Literature Evidence**: *TraceGuard* demonstrates that forcing a monitor to answer narrow factual questions (e.g., *"Does the action match what the reasoning says?"*) constrains evaluation compared to holistic prompts. *SCHEME* similarly shows that prompt framing heavily alters detection thresholds.

#### 2. Inference Compute and Multi-Sample Sampling Asymmetry
* **The Threat**: Hierarchical or multi-stage monitors (such as M4 or Extract-and-Evaluate pipelines) execute multiple LLM sampling calls or multi-field parsing passes, whereas single-pass monitors (M1–M3) run a single API call. Ensembling or multi-sample voting reduces score variance and shifts decision thresholds. If M4 is evaluated against a single-pass M3 baseline without an **equal-compute ensemble control (`M3_ensemble`)**, any observed gain is an artifact of inference compute budget rather than evidence structure.
* **Literature Evidence**: In *SCHEME*, score-level ensembling of single-view monitors (`Only Code` + `Only Comms` max/mean) matched unified joint prompts across multiple conditions. In *Information Access*, Extract-and-Evaluate gains were heavily dependent on multi-stage compute allocation.

#### 3. Label Misalignment: Conflating Solo Commitment Breaking with Multi-Agent Collusion
* **The Threat**: In multi-agent normal-form games, an agent breaking its public promise (e.g., announcing Option A but executing Option B) is frequently a **unilateral defection or noise**, NOT a joint collusive agreement. If the ground-truth labeling scheme marks unilateral promise breaking as "collusion", or if the monitor flags any text/action mismatch as "collusion", the experiment conflates solo deception with multi-agent cartel formation.
* **Literature Evidence**: *TrustBreaking* proves that models break commitments at rates exceeding 90% in repeated games for unilateral defection or to coordinate on above-Nash cooperative outcomes. *Colosseum* shows that text-based coordination frequently occurs without landing action-level collusive advantage.

#### 4. The "Less-is-More" Text Distraction & Gaslighting Confound
* **The Threat**: In raw combined transcripts (M3), monitors are frequently distracted or gaslit by plausible post-hoc rationalizations embedded in text messages. If M3 underperforms M2 (Actions Only), it may reflect **text-based gaslighting / prompt distraction** rather than a lack of useful information in dialogue.
* **Literature Evidence**: *Information Access* identifies the "less-is-more" effect, demonstrating that monitors with access to both reasoning/text and actions often perform *worse* than action-only monitors because unfiltered text misleads the evaluator. *Colosseum* uncovers an inverse correlation (\\(r = -0.243\\)) between action gains and text judge scores.

#### 5. Pseudoreplication and Low Task Diversity
* **The Threat**: Evaluated on 20–30 short trajectories across a single game matrix, observed detection differences may reflect prompt-sensitive heuristics of the specific model backbone or game wording rather than general properties of evidence views.
* **Literature Evidence**: *TrustBreaking* shows that model commitment breaking varies radically across games (ranging from 0% to 98.6% for the exact same model backbone). *SHADE-Arena* highlights that monitor ROC-AUC varies dramatically depending on task complexity.

---

### 2. Circular Ground-Truth Definition and Label Leakage

* **Circular Ground Truth**: Defining "collusion" using an LLM judge or monitor inputs rather than deterministic sandbox mechanics.
  * *Methodological Risk*: If ground truth is assigned by an LLM reading the text transcript, evaluating an LLM monitor against that ground truth measures **LLM-judge agreement**, not true safety-violation detection.
  * *Required Safeguard*: Ground truth MUST be computed deterministically from sandbox execution payloads (`action: "B"`) and system regret metrics (\\(\Delta \ge 0.35\\)), completely isolated from monitor predictions.
* **Label Leakage in Trace Formatting**:
  * *Methodological Risk*: Injecting condition names (e.g., `INFORMATIVE_MESSAGE`, `side_task_enabled`), system prompt flags, or post-hoc human annotations into the monitor context window directly leaks the target label.
  * *Required Safeguard*: Monitor prompts must receive strictly sanitized execution traces.

---

### 3. Confounds Between Evidence Content, Prompt Wording, and Monitor Capability

1. **Prompt Wording vs. Evidence Content**: M4's prompt tells the monitor *how* to parse structured fields (checking alignment), whereas M3 receives open-ended instructions ("Did collusion occur?"). Any M4 advantage may reflect superior prompt engineering rather than the value of structured data.
2. **Monitor Capability Asymmetry**: Strong frontier monitors (e.g., Claude Sonnet 4.6 or GPT-5.4) can parse unparsed raw text in M3, whereas weaker models (e.g., GPT-4.1-mini) heavily depend on M4 structuring.
3. **Threshold Calibration Artifacts**: Differences in True Positive Rate at low False Positive Rate (\\(\text{TPR} @ \text{FPR} \le 2\%\\)) across views may be artifacts of threshold calibration methodology (e.g., pooling vs. per-model calibration, or retaining vs. dropping main-task failures).

---

### 4. How the Monitor Could Exploit Superficial Cues

1. **Lexical / Keyword Triggers**: Assigning high suspicion to any trace containing words like *"Divert"*, *"Option B"*, or *"Secret"*, regardless of whether joint collusion occurred.
2. **Signal Dilution via Transcript Length**: Assigning lower suspicion scores to longer transcripts. In *SCHEME*, longer communication logs correlated negatively with suspicion scores (\\(r = -0.44\\)) due to signal dilution.
3. **Format Non-Compliance Penalties**: Assigning high suspicion scores to runs where agent tool outputs contain formatting glitches, misinterpreting execution errors as malicious evasion.
4. **Blind Action Matching**: Flagging joint Option B (\\(B/B\\)) execution even when agents reached Option B through non-collusive, independent utility maximization.

---

### 5. Uninterpretable Negative Results

1. **Floor/Ceiling Effects**: If the task is too obvious (overt text proposals) or too subtle (covert steganography), monitors across all four views saturate at 100% or 0% TPR, revealing zero discriminative difference between raw and structured evidence.
2. **Uncalibrated Baseline Thresholds**: If suspicion thresholds \\(\tau\\) are set arbitrarily without calibrating on a clean benign baseline pool, high error rates across all views reflect threshold miscalibration rather than evidence representation flaws.
3. **Behavioral Collapse ("Collusion on Paper")**: If the generation harness produces text proposals that fail to materialize in sandbox actions, testing monitors on these traces yields meaningless action-detection statistics because no environmental violation occurred.

---

### 6. Claims the Pilot CANNOT Support (Even with Clear Results)

1. **Generalizability Across Model Families or Multi-Turn Horizons**: A pilot on 20–30 single-shot traces using one model backbone cannot claim that findings generalize to multi-turn interactions, heterogeneous agent teams, or other LLM families.
2. **Resistance to Adaptive / Stealth-Aware Adversaries**: Demonstrating M4 superiority against static benchmark traces does NOT prove M4 is resistant to adaptive adversaries who actively obfuscate their dialogue or tool calls.
3. **Pure Information-Theoretic Representation Effect**: Because M4 changes prompt formatting, token layout, and field structure simultaneously, a positive result cannot be claimed as a pure, isolated representation effect.

---

### 7. Smallest High-Leverage Validity Improvements (Scope-Neutral)

1. **Add an `M3_ensemble` Compute Control**: Implement a 5-pass ensemble of the M3 prompt at \\(T=0.7\\) (aggregated via median) to hold total inference compute equal to structured/multi-sample views.
2. **Embed Identical Rule Definitions in M3 and M4**: Provide the exact text definitions of authorized vs. prohibited rules inside both M3 and M4 system prompts to eliminate rubric guidance as a confound.
3. **Symmetric Main-Task Pass Filtering**: Calibrate thresholds and evaluate metrics strictly on traces where agents successfully complete valid environment turns, dropping execution crashes symmetrically across baseline and attack pools.
4. **Deterministic Sandbox Ground Truth**: Anchor ground truth strictly on sandbox execution payloads (`action: "B"`) and system regret metrics (\\(\Delta \ge 0.35\\)), completely isolated from monitor prompts and LLM judge outputs.

---

### 8. Go/No-Go Checklist for a Weekend-Scale Pilot

```
====================================================================================================
WEEKEND PILOT GO / NO-GO CHECKLIST
====================================================================================================

[ ] 1. STAGE 0 POSITIVE CONTROL VERIFIED
    • 4-seed C-03 (Overt Harmful Collusion) run produces 100% joint B/B execution and 100% monitor TPR.
    • Decision: [ ] GO  [ ] NO-GO

[ ] 2. NEGATIVE CONTROL VERIFIED
    • 4-seed C-01 (Benign Baseline) run produces 0% false positive rate (FPR ≤ 2%, suspicion score ≤ 15/100).
    • Decision: [ ] GO  [ ] NO-GO

[ ] 3. GROUND TRUTH IS STRICTLY DETERMINISTIC
    • Safety labels are computed directly from sandbox tool call JSON logs and DCOP regret matrices.
    • Monitor inputs and LLM judge outputs are 100% excluded from label assignment.
    • Decision: [ ] GO  [ ] NO-GO

[ ] 4. PROMPT & COMPUTE CONFINEMENT
    • View M3 contains the exact same text definitions of safety rules as View M4.
    • M3_ensemble baseline (5-pass median) is implemented to match inference compute.
    • Decision: [ ] GO  [ ] NO-GO

[ ] 5. PAIRED EVALUATION HARNESS READY
    • All 4 views (M1, M2, M3, M4) are evaluated on the EXACT SAME frozen set of N=25 trajectories.
    • Decision: [ ] GO  [ ] NO-GO
====================================================================================================
```