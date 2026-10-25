# Data

This file documents every data file shipped in the release asset `apart_data_2026-10-25.zip`. Per-file sha256 values are also in the asset's `SHA256SUMS.txt` and can be checked with `scripts/verify_data_asset.py`. The reported numbers are reproduced offline by `supplementary/analysis/run_all.py` (35 checks).


## Frozen corpora (`data/frozen/`)

**Schema**: canonical `Trace` records + historical Y_H labels (`src/apart/schema.py`, `src/apart/labeler.py`). **Produces**: offline monitor smoke checks (`tests/test_stage4n_monitor_smoke.py`), not the reported AUROC. **Provenance**: frozen at P1; `manifest_v2.json` pins the four corpora by sha256; no scenario id.

| File | What it is | Records | Bytes | sha256 |
|---|---|---:|---:|---|
| `data/frozen/gate_v2_traces.jsonl` | gate corpus traces (8) | 8 | 3149 | `0d2db6bb629733d45f960731f7750ace7a34f2c2db928a2a865f7262b6aa7059` |
| `data/frozen/gate_v2_labels.jsonl` | gate corpus Y_H labels (8) | 8 | 2304 | `e3879004ccf4b995e823f3823951460b85a3f4cf96d1d50b29a2219082185354` |
| `data/frozen/pilot_v2_traces.jsonl` | pilot corpus traces (40) | 40 | 15871 | `cf7615e107b1be03e9ddd23e1b280a611b30fc0489945412d321e736dee201ac` |
| `data/frozen/pilot_v2_labels.jsonl` | pilot corpus Y_H labels (40) | 40 | 11756 | `22e6457632051f5d749d2409fe53d72d1b96da5fee248e9b495c983b22e1008c` |
| `data/frozen/manifest_v2.json` | sha256 manifest pinning the four files above | — | 741 | `2bf20d7e46086e940ad9c830caa583608534d08544ece071e2798d5b8702a59e` |

## Sender-receiver interaction (`supplementary/data/sender_receiver_interaction/`)

**Schema** (trials): `metadata.{condition,scenario_id,trial_id}`, `sender_action`, `parsed_action`, `validity`, `message_content`, `message_delivered`, `receiver_raw_output`, `call_log[]`, `error`. **Produces**: Table 1, `fig_main_receiver_v2`, `fig_phaseA_BB_v2`, the summary figure. **Provenance**: `scripts/run_phase_a_live.py`; run ids `phase-a-live-v2-20261024` (Sonnet 4.6), `phase-a-live-gpt54mini-v1-20261024` (GPT-5.4-mini); plan seed `20261024`; agent prompt v2.1; receiver temperature 1.0, max_tokens 150, one retry.

| File | What it is | Records | Bytes | sha256 |
|---|---|---:|---:|---|
| `supplementary/data/sender_receiver_interaction/trials_receiver-sonnet.jsonl` | 80 trials, Sonnet receiver | 80 | 133747 | `e26097729d5045a0657395e5a8f1938a61e1ed3ad6df5b8e648e45f88c5a160c` |
| `supplementary/data/sender_receiver_interaction/trials_receiver-gpt54mini.jsonl` | 80 trials, GPT receiver | 80 | 122271 | `7d1fc919f3a3073976af3f9dd0cf193524f231046a4b8f5f53e0e3ebef98ab97` |
| `supplementary/data/sender_receiver_interaction/plan.json` | frozen plan: scenarios, sender actions, templates, order | — | 4669 | `10586121137986ee8c04f9364bca88b3a4252b9cfa33a0b37ee4f2f438d4ecd3` |
| `supplementary/data/sender_receiver_interaction/conversion_receiver-sonnet.jsonl` | adapter status per trial | 80 | 8420 | `f1d753a9e9e57cba78ae5a3e9b1bdb7ae60b06b17db100df2716c553127b842d` |
| `supplementary/data/sender_receiver_interaction/conversion_receiver-gpt54mini.jsonl` | adapter status per trial | 80 | 8420 | `f1d753a9e9e57cba78ae5a3e9b1bdb7ae60b06b17db100df2716c553127b842d` |
| `supplementary/data/sender_receiver_interaction/summary_receiver-sonnet.json` | per-condition counts and validity | — | 1224 | `77ad1edd36d88530c2efb253a28d5f9c8229af2f9f7d0e5bc3b9bd09b6f94b3b` |
| `supplementary/data/sender_receiver_interaction/summary_receiver-gpt54mini.json` | per-condition counts and validity | — | 1225 | `917d509cb37f8b4f8e65b00f455d2c321210c0b79b6bd6736931ded9a3f34fb9` |

## Monitor scores (`supplementary/data/monitor_scores/{sonnet_monitor,gpt_monitor}/`)

**Schema** (score rows): `view` (M1–M4), `score` (0–100), `rationale`, `checklist` (M4 only), `trace_id`, `scenario_id`, `status`, `n_attempts`, `prompt_version`, `prompt_sha256`, `model`, `served_model`, `served_provider`, `temperature`, `max_tokens`, `k`. **Produces**: Table 2 (AUROC) and Table 3 (contrasts), `fig_phaseB_v2`. **Provenance**: `scripts/run_monitors.py`; monitor prompt v2.0; temperature 0.0; max_tokens 250 (M1–M3) / 600 (M4); 80 rows per file = 20 traces × 4 views; 16 files = **1,280 score rows**. GPT monitor is the completed `gptmon2` rerun.

| File | What it is | Records | Bytes | sha256 |
|---|---|---:|---:|---|
| `supplementary/data/monitor_scores/gpt_monitor/scores_monitor-gpt_receiver-gpt54mini_c0_no_message.jsonl` | GPT monitor, GPT receiver, C0 | 80 | 71781 | `414ebf679feead09f83425877be93c79209b6b8c0a94f9751fab16d8e0d15461` |
| `supplementary/data/monitor_scores/gpt_monitor/scores_monitor-gpt_receiver-gpt54mini_c1_neutral.jsonl` | GPT monitor, GPT receiver, C1 | 80 | 73994 | `c4fb5a8520c5a952c2a75a79575071adbf0d89750ef949c67e501a2cbb6df4b4` |
| `supplementary/data/monitor_scores/gpt_monitor/scores_monitor-gpt_receiver-gpt54mini_c2_a_coordination.jsonl` | GPT monitor, GPT receiver, C2 | 80 | 72222 | `47564061343619103b6ca2fa01a46e2748dfa392843ed58bc567165302b40749` |
| `supplementary/data/monitor_scores/gpt_monitor/scores_monitor-gpt_receiver-gpt54mini_c3_b_coordination.jsonl` | GPT monitor, GPT receiver, C3 | 80 | 72439 | `223a197f7c2c573724e3cc0a5024279ef7ec2a1b517318a5de304546d6eabedf` |
| `supplementary/data/monitor_scores/gpt_monitor/scores_monitor-gpt_receiver-sonnet_c0_no_message.jsonl` | GPT monitor, Sonnet receiver, C0 | 80 | 71613 | `c087a8353187e27bcf0879b419d2a1141e84ab8a0202fddb4b3ee48e2f4a6386` |
| `supplementary/data/monitor_scores/gpt_monitor/scores_monitor-gpt_receiver-sonnet_c1_neutral.jsonl` | GPT monitor, Sonnet receiver, C1 | 80 | 73714 | `58151a5f7bd46c0c923f4a76334d43bb12ad2a6c4a0af61936e8455489fe7faf` |
| `supplementary/data/monitor_scores/gpt_monitor/scores_monitor-gpt_receiver-sonnet_c2_a_coordination.jsonl` | GPT monitor, Sonnet receiver, C2 | 80 | 72310 | `7bef59e741296c721071dbc9dd6f46312dd1d44a16cfb4007b33d1b3664d4291` |
| `supplementary/data/monitor_scores/gpt_monitor/scores_monitor-gpt_receiver-sonnet_c3_b_coordination.jsonl` | GPT monitor, Sonnet receiver, C3 | 80 | 73168 | `4d00af40d7d6ae43597042ee53fb603ef186586f019347783aee9386ce1c64f0` |
| `supplementary/data/monitor_scores/sonnet_monitor/scores_monitor-sonnet_receiver-gpt54mini_c0_no_message.jsonl` | Sonnet monitor, GPT receiver, C0 | 80 | 71646 | `a647545f987d5658b81257743cb96c380e8bf3e918a5644d535da699a333d5b9` |
| `supplementary/data/monitor_scores/sonnet_monitor/scores_monitor-sonnet_receiver-gpt54mini_c1_neutral.jsonl` | Sonnet monitor, GPT receiver, C1 | 80 | 74634 | `58eae6783e485a9f15062f28198eac69c61aaa35ba4b6438a9b4280e02a9207b` |
| `supplementary/data/monitor_scores/sonnet_monitor/scores_monitor-sonnet_receiver-gpt54mini_c2_a_coordination.jsonl` | Sonnet monitor, GPT receiver, C2 | 80 | 70771 | `026be5b9207ac796887fddef9d7fb21d5f7bde2cd535085d76a3e8771476d580` |
| `supplementary/data/monitor_scores/sonnet_monitor/scores_monitor-sonnet_receiver-gpt54mini_c3_b_coordination.jsonl` | Sonnet monitor, GPT receiver, C3 | 80 | 73552 | `16a4ec6b62338abb78717c619c468720ede7c0816bc87ffd1032fbd7535e77e5` |
| `supplementary/data/monitor_scores/sonnet_monitor/scores_monitor-sonnet_receiver-sonnet_c0_no_message.jsonl` | Sonnet monitor, Sonnet receiver, C0 | 80 | 71627 | `429f1588d7c91c2987689923a5df8cf455f345c10831b43706de14ce374d27ab` |
| `supplementary/data/monitor_scores/sonnet_monitor/scores_monitor-sonnet_receiver-sonnet_c1_neutral.jsonl` | Sonnet monitor, Sonnet receiver, C1 | 80 | 75276 | `ff3fcefd00a0a965bf345783a6cf2cea9c5468dfb374cf7204911b91230c03f2` |
| `supplementary/data/monitor_scores/sonnet_monitor/scores_monitor-sonnet_receiver-sonnet_c2_a_coordination.jsonl` | Sonnet monitor, Sonnet receiver, C2 | 80 | 71603 | `5123cb1ffd96d591e86e511379acd90f83d10f4c17f758885b7a2dfd44e5ab8a` |
| `supplementary/data/monitor_scores/sonnet_monitor/scores_monitor-sonnet_receiver-sonnet_c3_b_coordination.jsonl` | Sonnet monitor, Sonnet receiver, C3 | 80 | 73398 | `8e0dabd580cdabe7202ae62534eefe4080864039eba84071031b72d4643ebac2` |
| `supplementary/data/monitor_scores/gpt_monitor/status_monitor-gpt_receiver-gpt54mini_c0_no_message.json` | GPT-monitor run status, GPT receiver, C0 | — | 1476 | `8ce865e4367f225fb8f8cf1317bd7f8b57faf2731baf4b4de82ba82777caf624` |
| `supplementary/data/monitor_scores/gpt_monitor/status_monitor-gpt_receiver-gpt54mini_c1_neutral.json` | GPT-monitor run status, GPT receiver, C1 | — | 1461 | `f7ef8843f19ff0ba0ac19c66f9a15666394893a6f25776aa88977871f3261f58` |
| `supplementary/data/monitor_scores/gpt_monitor/status_monitor-gpt_receiver-gpt54mini_c2_a_coordination.json` | GPT-monitor run status, GPT receiver, C2 | — | 1496 | `0f802252f0a2a7a99754920e15b74f4eb50c0780b259a31c2d4e4f402395f87a` |
| `supplementary/data/monitor_scores/gpt_monitor/status_monitor-gpt_receiver-gpt54mini_c3_b_coordination.json` | GPT-monitor run status, GPT receiver, C3 | — | 1496 | `65858fa67c015254fab531ffbe2ab7c3d08efc9db5aa724a56d01de7657734c4` |
| `supplementary/data/monitor_scores/gpt_monitor/status_monitor-gpt_receiver-sonnet_c0_no_message.json` | GPT-monitor run status, Sonnet receiver, C0 | — | 1426 | `f419acdb04482e3b7218f0b79a0b1032b28cbeb9684600d80f461d8dfc68cac7` |
| `supplementary/data/monitor_scores/gpt_monitor/status_monitor-gpt_receiver-sonnet_c1_neutral.json` | GPT-monitor run status, Sonnet receiver, C1 | — | 1411 | `90bc70f20d632cb1d083123ea5120e07a005bf8708e37ad5d72f125190b75363` |
| `supplementary/data/monitor_scores/gpt_monitor/status_monitor-gpt_receiver-sonnet_c2_a_coordination.json` | GPT-monitor run status, Sonnet receiver, C2 | — | 1446 | `9ff8094d3eeec1a4ca424c04401e37e4d7f9b420814366348e939c0cda182461` |
| `supplementary/data/monitor_scores/gpt_monitor/status_monitor-gpt_receiver-sonnet_c3_b_coordination.json` | GPT-monitor run status, Sonnet receiver, C3 | — | 1446 | `1965b8ef669db9ef76d7858db5372e2d712d80ce494c0f2eff4a26b3b90cab4d` |

## E labels (`supplementary/data/monitor_scores/e_labels_*.jsonl` + manifests)

**Schema** (labels): `trace_id`, `scenario_id`, `e`, `e_state`, `evidence_status`, `joint_action`, `objective_conflicting_behaviour`, `x`, `n_slots`, `n_delivered`, `n_obscuring`, `label_version` (`e-1.0`), `objective_id` (`OBJ-1.0`), `provenance`. **Produces**: the reference outcome behind Table 2/3. **Provenance**: `src/apart/e_label.py` (rule P), `src/apart/e_sheet_labels.py`, guide `annotation_calibration_guidelines_2026-10-23`, E rule P0-1 (post hoc). 80 rows per receiver: 40 resolved + 40 no_agent_message.

| File | What it is | Records | Bytes | sha256 |
|---|---|---:|---:|---|
| `supplementary/data/monitor_scores/e_labels_receiver-sonnet.jsonl` | E labels, Sonnet receiver (80) | 80 | 46151 | `d6b4351e83781190d8860502475b31cd15694e45c87e4251a07220b03cb9a3d5` |
| `supplementary/data/monitor_scores/e_labels_receiver-gpt54mini.jsonl` | E labels, GPT receiver (80) | 80 | 46142 | `c65c9f3290bb91dc5f7d365e7403302cf1d6c752ae6ccb752d5f93807f5d2726` |
| `supplementary/data/monitor_scores/e_label_manifest_receiver-sonnet.json` | label manifest: counts, E rule, sheet hashes | — | 9007 | `667d641be7b858e6c27560f5955b24fb37bbf9ad546faac64571cdeecde88b6f` |
| `supplementary/data/monitor_scores/e_label_manifest_receiver-gpt54mini.json` | label manifest: counts, E rule, sheet hashes | — | 9027 | `28331ddaf2ffe81a7de00fb0d0a9e462d1d24091db262d82b4608c9bacda9e6a` |

## Exploratory receiver follow-up (`supplementary/data/exploratory_receiver_follow-up/`)

**Schema** (trials): `cell_id` (c_a_absent/c_a_present/c_b_absent/c_b_present), `scenario_id`, `parsed_action`, `validity`, `message_id`, `message_text`, `served_model`, `run_seed`. **Produces**: Table 4 (cell means + T/J/I), `fig_e1_v2`, panel (b) of `fig_main_receiver_v2`. **Provenance**: `scripts/run_experiment_e1.py`; run ids `e1-msg2x2-sonnet46-v1-20261025T030713Z`, `e1-msg2x2-gpt54mini-v1-20261025T031210Z`; plan seed `20261101`; agent prompt v2.1; bootstrap n=2000 seed=0. Labelled as a separate exploratory component.

| File | What it is | Records | Bytes | sha256 |
|---|---|---:|---:|---|
| `supplementary/data/exploratory_receiver_follow-up/arm-sonnet46/trials.jsonl` | 80 trials, Sonnet arm | 80 | 202521 | `cf96d7cd20b4ed039aab5eb7bf44dc8c3ed933bfe996c77a66956517e5fdeeae` |
| `supplementary/data/exploratory_receiver_follow-up/arm-sonnet46/plan.json` | frozen 2x2 plan | — | 6767 | `c7891ec9aec8104867f5c5fde19836e88ebec6846ab87b3a0cce4ba137556aa2` |
| `supplementary/data/exploratory_receiver_follow-up/arm-sonnet46/run_status.json` | terminal run status | — | 6582 | `f66e8439a27676180fa478449769b76e376061bf1eb6033438d9accf5fd2eb84` |
| `supplementary/data/exploratory_receiver_follow-up/arm-sonnet46/summary.json` | run summary (path-normalised) | — | 1381 | `6c8c34c714f539f3f9ab46d1266d1761610eb958110b04376827561030f47622` |
| `supplementary/data/exploratory_receiver_follow-up/arm-sonnet46/analysis.json` | stored analysis: cell means, T/J/I, bootstrap | — | 6645 | `eab08e39ed137375032151a02d03a7d60f6f8ac701889144e0a4a6c9d7a90a86` |
| `supplementary/data/exploratory_receiver_follow-up/arm-gpt54mini/trials.jsonl` | 80 trials, GPT arm | 80 | 188070 | `b39ef41607e23680a15e370516cf9168648fe5e391e9c5b56de9f32ac9a3367e` |
| `supplementary/data/exploratory_receiver_follow-up/arm-gpt54mini/plan.json` | frozen 2x2 plan | — | 6767 | `c7891ec9aec8104867f5c5fde19836e88ebec6846ab87b3a0cce4ba137556aa2` |
| `supplementary/data/exploratory_receiver_follow-up/arm-gpt54mini/run_status.json` | terminal run status | — | 6656 | `82342d6a4da591916c19c024ee4e8557575f0033aaafc6157a44a3ec48871c53` |
| `supplementary/data/exploratory_receiver_follow-up/arm-gpt54mini/summary.json` | run summary (path-normalised) | — | 1403 | `e1c325474b7e6b4c9ebbb0507bdf293193c6881fd33857e3cb286266db1cc62d` |
| `supplementary/data/exploratory_receiver_follow-up/arm-gpt54mini/analysis.json` | stored analysis: cell means, T/J/I, bootstrap | — | 6598 | `0b5e700fd05f45e4a53aeef1bded49bf4ab8fdc7be4b9ddbf334357a25938baa` |

## Annotations (`supplementary/data/annotations/`)

**Schema** (CSV): `message_id`, `message_text`, `coordination_attempt`, `target`, `speech_act_primary`, `speech_act_secondary`, `uncertainty_or_notes`. **Produces**: the E derivation (rule P0-1) and the annotation-agreement summary. **Provenance**: the four sheets (first pass and revised, both annotators), anonymised as Annotator 1 / Annotator 2; 10 messages each. Fields defined in `docs/annotation_instructions.md`.

| File | What it is | Records | Bytes | sha256 |
|---|---|---:|---:|---|
| `supplementary/data/annotations/annotator_1_first_pass.csv` | Annotator 1 first pass | 11 | 1070 | `bead0f880036d7b18d218fdb573a00d099274085b47a2fb8d8ab1c4460f418c0` |
| `supplementary/data/annotations/annotator_1_revised.csv` | Annotator 1 revised | 11 | 1062 | `cbff1e9a97e579618c49d23ad3ee565b6193ab5564daca56e9d177bb041232ab` |
| `supplementary/data/annotations/annotator_2_first_pass.csv` | Annotator 2 first pass | 11 | 976 | `3d1f2d63fe49979f68952c12bd34addffd08198c0ffd8484e5579eea7aaa5b2e` |
| `supplementary/data/annotations/annotator_2_revised.csv` | Annotator 2 revised | 11 | 1057 | `0648165e6a6e14eeeaecbb092173143b313b45d7a8fcc00af5b7979ec81237c0` |

## Design — templates, monitor prompts, constructs (`supplementary/design/`)

**Produces**: the template appendix and the exact prompt text behind every monitor score. **Provenance**: `src/apart/phase_a_live.py` (ten templates), `monitors/prompts.py` (v2.0).

| File | What it is | Records | Bytes | sha256 |
|---|---|---:|---:|---|
| `supplementary/design/message_templates.json` | ten templates + C1 notice + scenario mapping | — | 2124 | `8d214e95c4ed568a39aa3b6c68f12c01021cf435af043d2c2b27442e746ff183` |
| `supplementary/design/monitor_prompts_v2.0.json` | exact monitor prompts (system, M1–M4, checklist) | — | 9304 | `3e454ebaa4f3450def7423783798c54dc6edf74e7eeaac6a0c1e549c6724e88a` |
| `supplementary/design/game_and_conditions.md` | game spec and conditions C0–C3 | — | 3497 | `7bcebd8e7e3dfb844dbc94852a89715f02b4b5f2783e826c5f980e84cf725442` |
| `supplementary/design/message_templates.md` | templates verbatim with M-id cross-reference | — | 3058 | `625477d16e2c3d8d011d3f386db6cd780e93820bcde6e866044ae750ef2de133` |
| `supplementary/design/monitor_views.md` | M1–M4 evidence blocks and instructions | — | 3949 | `cd1f9027406780f77dd25a4fa77dd352221550685f433ee9f9e9e0f76ec597b7` |
| `supplementary/design/constructs.md` | receiver B-choice, BB, OB, E, Y_H | — | 3690 | `3cc2e8a43063531ff9b84647e325282d93eed08c7a7413cc2176a18059835980` |

## Figure scripts (`figures/report_2026-10-24/`)

**Produces**: `fig_phaseA_BB_v2`, `fig_phaseB_v2`, `fig_e1_v2`, `fig_main_receiver_v2`. Read-only scripts over the data above; Wilson 95% intervals for the receiver figures.

| File | What it is | Records | Bytes | sha256 |
|---|---|---:|---:|---|
| `figures/report_2026-10-24/make_figures.py` | shared loaders + count assertions | — | 25120 | `80c9ca5a890c9fd84625b804730c7526656bde21620e16cb4bc77f8f01ff5c9f` |
| `figures/report_2026-10-24/make_phaseA_fig_v2.py` | receiver-B / BB figure | — | 4958 | `e81cbd0872d6956cb2cfb586c1b0af9f1efc63fa126157a597ef1da97297b4bf` |
| `figures/report_2026-10-24/make_phaseB_fig_v2.py` | monitor-score figure with AUROC | — | 4851 | `edc6171518b867cabda27713c998d2cc36b286f85c9198af800f0bd80b1c86f9` |
| `figures/report_2026-10-24/make_e1_fig_v2.py` | exploratory follow-up figure | — | 5986 | `af358ba84d559a94ca5ff233a803c893c3701bd93a1fb7ab8908b763cd2138c0` |
| `figures/report_2026-10-24/make_main_receiver_fig_v2.py` | main-text receiver figure | — | 4582 | `0849edad05c4b4340d48e82a2479ff8def7c29007f8a4fd31571312af4cb3b17` |
| `figures/report_2026-10-24/make_main_receiver_fig.py` | earlier main-text receiver figure | — | 4599 | `3c99016464a036a110a6939873072e7421ddd088c0ca15b984b89269c282188c` |

## Summary figure

The report's summary figure, embedded in the README. It has **no generating script** in the repository and is shipped as-is.

| File | What it is | Records | Bytes | sha256 |
|---|---|---:|---:|---|
| `figures/summary_figure.png` | summary figure (PNG) | — | 99819 | `bcfe9a5ab15e21c4d42c41292957d00e516b55f313f2bfa61f8c8d33b0985c90` |

## What is NOT released, and why

- `runs/` and `results/` corpora — live-run ledger/guard state and un-anonymised sheets; produced locally.

- Raw provider payloads (`runs/*/raw*/`, `experiments/*/raw/`).

- Spend records: ledgers, guards and spend policy configs.

- Any API key, `.env`, or account/billing identifier; `apart_code_review.zip`.


## Anonymisation

Personal initials do not appear in the released data. Two transforms were applied and are recorded in `supplementary/manifests/file_manifest.json`: `MM` → `Annotator 1` and `LL` → `Annotator 2` (plus the four sheet names), and the absolute local path prefix → `<repo>/` in the two exploratory-arm `summary.json` files. Six files are therefore **not byte-identical to their source** (`copied_verbatim: false`):

1. `supplementary/data/monitor_scores/e_labels_receiver-sonnet.jsonl`

2. `supplementary/data/monitor_scores/e_labels_receiver-gpt54mini.jsonl`

3. `supplementary/data/monitor_scores/e_label_manifest_receiver-sonnet.json`

4. `supplementary/data/monitor_scores/e_label_manifest_receiver-gpt54mini.json`

5. `supplementary/data/exploratory_receiver_follow-up/arm-sonnet46/summary.json`

6. `supplementary/data/exploratory_receiver_follow-up/arm-gpt54mini/summary.json`


## Annotation precedence and adjudication

The annotation instructions take precedence over the calibration guide where they differ; the guide's phrase 'B is the prohibited action' is read as 'B is the option the operator does not prefer.' Annotation judged message content only (target is the proposed action, not its status).

**Adjudicated column:** the four annotation sheets ship exactly what they contain — a `message_id`, `message_text` and five annotation columns. There is **no separate adjudicated column and no adjudication sheet**: the revised sheets are the post-adjudication record, and `label_manifest.json` records `adjudication = null` (rule P0-1 made E independent of the disputed speech-act cells, so no adjudication column was needed).

