# `scripts/` — command-line entry points

Thin CLI drivers over `src/apart/` and `monitors/`. **Two classes of script:** those that run
entirely offline (safe, reproducible, no key) and those that call a paid provider (need
`APART_API_KEY`).

## Offline (no API key, no network)

| Script | What it does |
|---|---|
| `analyze_results.py` | Offline summaries over stored result files |
| `render_prompts_sample.py` | Renders sample monitor prompts to `results/rendered_prompts_sample.md` |
| `probe_rows_to_traces.py` | Converts probe rows into canonical traces |
| `validate_stage4n.py` | Offline validation for the Stage 4N artefacts |
| `run_experiment_e1.py --dry-run` | Runs the exploratory plan against an in-process **fake** receiver: zero provider calls, no spend config or ledger is opened |
| `run_experiment_e1.py --analyze-run-dir DIR` | Runs the frozen analysis on one finished E1 run directory (writes `analysis.json` / `analysis.txt`) |
| `run_monitors.py --dry-run` | Renders monitor prompts without sending them |

```bash
# Example: no API key, no network, no spend
python scripts/run_experiment_e1.py --dry-run --out-dir /tmp/e1-dry
```

## Provider-backed (need an untracked `.env` with `APART_API_KEY`)

| Script | What it does | Cost control |
|---|---|---|
| `run_phase_a_live.py` | Runs the sender-receiver interaction (live receiver) | `--max-spend-usd`, ledger guard |
| `run_monitors.py` | Runs monitor views M1–M4 over traces | ledger guard; pass `--views M1,M2,M3,M4` |
| `run_experiment_e1.py --confirm-live` | Runs one approved E1 arm | requires `--i-have-live-authorization`, an explicit `--base-url`, and `--max-spend-usd` |
| `run_phase_a.py`, `run_phase_b_report.py`, `run_generation_probe.py`, `run_behavior_probe.py` | Phase A/B report and probe drivers | various |
| `rebind_spend_config.py` | Rebinds a spend config/ledger pair | — |
| `generate_extension.py` | Extension-corpus generation | — |

```bash
# Inspect what a live run would do without spending anything
python scripts/run_monitors.py --dry-run --views M1,M2,M3,M4
```

## Rules for anything that spends

- Key comes only from the environment (`.env`, untracked); it is never printed.
- Every request is reserved in a ledger **before** it is sent; caps are enforced by the ledger.
- Live entry points require an explicit authorisation flag. Some also require a project-level lock.
- Worst-case cost must be computed from the rendered prompts and the configured prices, not guessed.

See `docs/runbook.md` for the operational procedure and `docs/decision_log.md` for the approvals.
