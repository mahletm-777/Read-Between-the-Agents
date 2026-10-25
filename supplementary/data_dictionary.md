# Data dictionary

All JSONL files are one JSON object per line. All timestamps are UTC ISO-8601.
Every path below is relative to `supplementary/`.

## `data/sender_receiver_interaction/`

### `trials_receiver-{sonnet,gpt54mini}.jsonl` — 80 rows each

| Field | Type | Meaning |
|---|---|---|
| *(top level)* | | |
| `parsed_action` | `"A"` \| `"B"` \| `null` | the receiver's choice, as parsed. `null` = unparseable/refused |
| `sender_action` | `"A"` \| `"B"` | the **scripted** sender's choice (a scenario attribute) |
| `validity` | `"valid"` \| `"ambiguous"` \| `"invalid"` | whether the receiver action resolved. Only `"valid"` enters any denominator |
| `message_content` | string | the message the receiver saw (C0: empty) |
| `message_delivered` | bool | whether the message reached the receiver |
| `receiver_raw_output` | object \| string \| null | the receiver's raw reply (model output) |
| `call_log` | array | one entry per API attempt; length > 1 records a **parse retry** |
| `error` / `error_stage` / `reason` | string \| null | failure accounting; `null` on a clean trial |
| `metadata` | object | see below |
| *(inside `metadata`)* | | |
| `condition` | `c0_no_message` \| `c1_neutral` \| `c2_a_coordination` \| `c3_b_coordination` | experimental condition |
| `scenario_id` | `"S01"`…`"S20"` | scenario label |
| `trial_id` | string | `"{scenario_id}_{condition}"` |

### `plan.json`
`{"scenarios": [{"scenario_id", "sender_action", "template_index"}], "order": [...], ...}` — the
frozen scenario/sender/template assignment and the seeded trial order. Identical for both receivers.

### `conversion_receiver-{sonnet,gpt54mini}.jsonl`
Adapter status per trial: whether the record converted to a canonical trace, plus the `trace_id`
used to join trials to monitor scores and E labels.

### `summary_receiver-{sonnet,gpt54mini}.json`
Run-level counts: `n_trials`, `n_valid_traces`, `counts_by_status`, `exclusions`.

## `data/monitor_scores/`

### `{sonnet_monitor,gpt_monitor}/scores_monitor-{sonnet,gpt}_receiver-{...}_{condition}.jsonl`
80 rows per file (= 20 traces × 4 views). One row per (trace, view).

| Field | Type | Meaning |
|---|---|---|
| `view` | `"M1"` \| `"M2"` \| `"M3"` \| `"M4"` | which monitor view produced this score |
| `score` | int 0–100 | evidential strength. **≥ 50 = positive** |
| `rationale` | string | the monitor's stated reasoning |
| `checklist` | object \| null | M4 only; `null` for M1–M3 |
| `trace_id` | string | join key to the trial record and to the E labels |
| `scenario_id` | `"S01"`…`"S20"` | scenario label |
| `status` | string | `"ok"` or a failure status; failures are kept, not dropped |
| `n_attempts` | int | API attempts for this row |
| `prompt_version` | string | `"2.0"` |
| `prompt_sha256` | string | sha256 of the **rendered** prompt (per trace, per view) |
| `model` / `served_model` / `served_provider` / `snapshot` | string | requested vs provider-reported identity |
| `provider_seed`, `temperature`, `max_tokens` | int/float | decoding settings |
| `response_id`, `created_utc`, `display_id`, `k` | mixed | provenance |

### `status_monitor-gpt_receiver-*.json` — run-level status of each GPT-monitor run.

### `e_labels_receiver-{sonnet,gpt54mini}.jsonl` — 80 rows each

| Field | Type | Meaning |
|---|---|---|
| `trace_id` | string | join key |
| `scenario_id` | string | scenario label |
| `e` | bool | `true` = E-positive |
| `e_state` | `"positive"` \| `"negative"` | the resolved E state (**UNRESOLVED traces carry no `e_state`**) |
| `evidence_status` | `"resolved"` \| `"no_agent_message"` \| benign-empty or obscuring status | independent of E; missing evidence is never treated as negative |
| `joint_action` | `"AA"` \| `"AB"` \| `"BA"` \| `"BB"` \| unparseable | joint outcome, agent-1-first ordering |
| `objective_conflicting_behaviour` | `"yes"` / other | OBJ-1.0 endpoint |
| `n_delivered`, `n_obscuring`, `n_slots` | int | evidence accounting |
| `label_version`, `objective_id` | string | `"e-1.0"`, `"OBJ-1.0"` |
| `provenance` | object | annotators, guide version, converter, message sources |

### `e_label_manifest_receiver-*.json`
Label counts, the pinned `e_rule` (P0-1), guide version, and the recorded sha256 of each annotator's
sheet, the pinned `e_rule` (P0-1), guide version, and the recorded sha256 of each annotator's
sheet.

> **⚠ Flagged, not fixed — Annotator 2 (LL) sheet-hash discrepancy.** In both
> `e_label_manifest_receiver-*.json` files the Annotator 2 entry reads
> `{"sheet": "annotation_sheet_LL.csv", "sha256": "3d1f2d63fe49979f68952c12bd34addffd08198c0ffd8484e5579eea7aaa5b2e"}`.
> The file named `annotation_sheet_LL.csv` (released here as `annotator_2_revised.csv`) actually
> hashes to `0648165e6a6e14eeeaecbb092173143b313b45d7a8fcc00af5b7979ec81237c0`; the recorded
> `3d1f2d63…` is the hash of `annotation_sheet_LL_original.csv` (released as
> `annotator_2_first_pass.csv`). Annotator 1's entry is self-consistent — its name and its hash both
> point at the revised sheet (`cbff1e9a…`). **So the pinned Annotator 2 hash points at the first
> pass while the pinned filename points at the revised sheet.** The discrepancy is a provenance
> defect only: the two annotators agreed on `coordination_attempt` and `target` for all 10 messages
> from the outset, so **E is identical under either sheet** and no released number changes. It is
> self-reported here because it cannot be resolved without the owner deciding which artefact the
> manifest was meant to pin. See `audits/md_audit_2026-10-25.md` and
> `supplementary_phase_b_audit/outputs/annotation_provenance_and_agreement.md`.

## `data/exploratory_receiver_follow-up/arm-{sonnet46,gpt54mini}/`

| File | Contents |
|---|---|
| `trials.jsonl` | 80 rows; fields as above plus `cell_id` ∈ `c_a_absent`, `c_a_present`, `c_b_absent`, `c_b_present`, `served_model`, `run_seed`, `message_id`, `message_text` |
| `plan.json` | frozen plan: `cell_mean_order`, per-cell `target` and `payoff_mention`, the eight frozen message literals, `seed` |
| `run_status.json` | terminal run status: `n_planned_trials`, `n_attempted`, `n_failed`, `n_not_attempted`, `dispatched_call_ids` |
| `summary.json` | aggregate run summary |
| `analysis.json` | the stored analysis: `cell_means`, `contrasts` (T/J/I with `ci95`), `bootstrap` settings, `eligibility` |

## `data/annotations/`

Four CSV files, one row per message. **The two annotators are referred to as Annotator 1 and
Annotator 2**; personal initials are not used anywhere in this package.

| File | Annotator | Pass |
|---|---|---|
| `annotator_1_first_pass.csv` | Annotator 1 (lowercase value style) | first pass |
| `annotator_1_revised.csv` | Annotator 1 | revised |
| `annotator_2_first_pass.csv` | Annotator 2 (uppercase value style) | first pass |
| `annotator_2_revised.csv` | Annotator 2 | revised |

Columns: `message_id`, `message_text`, `coordination_attempt`, `target`, `speech_act_primary`,
`speech_act_secondary`, `uncertainty_or_notes`.

**Normalisation required before comparing:** Annotator 1 writes `yes` and lowercase speech acts;
Annotator 2 writes `YES` and uppercase. Comparisons must be case-insensitive.

**Mapping evidence.** The mapping above is inferred from the value conventions, not stated in a file:
`annotator_1_calibration.csv` uses lowercase `yes` / `proposal`, matching Annotator 1's sheets;
`annotator_2_calibration.csv` uses `YES` / `PROPOSAL`, matching Annotator 2's. Confidence: high but
**not** documentary.

## Conventions that apply throughout

- **Denominators are always explicit.** A zero denominator yields `null`, never a zero rate.
- **Invalid, missing and unparseable outcomes are kept in the files** and counted, never imputed.
- `null` means "not established", which is not the same as `0`.
- All costs, where quoted, are token counts × configured gateway prices — **not** provider invoices.
- Every monitor score joins to its trial and label by `trace_id`; `scenario_id` is the resampling
  unit used by the bootstrap.
