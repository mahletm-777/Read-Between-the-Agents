# `configs/` — spend policy and model bindings

Small JSON files that bind a **model**, a set of **caps** and a **price list**. A ledger binds to the
SHA-256 of exactly one config file, so editing a config invalidates every ledger bound to it.

| File | Binds | Caps (USD) | Prices (per 1M tokens) |
|---|---|---|---|
| `spend.json` | `openrouter/anthropic/claude-sonnet-4.6` | allocation `pilot_audit` 14.00; phases `gate` 12.00, `pilot` 2.00; backstop 37.34 | Sonnet: 3.00 in / 15.00 out |
| `spend_gpt54mini.json` | `openrouter/openai/gpt-5.4-mini` | identical to the above | Sonnet as above, plus GPT-5.4-mini: 0.75 in / 4.50 out |

`spend.json` is the canonical Phase A/B config. `spend_gpt54mini.json` is the GPT-5.4-mini receiver
arm's config.

## How to read a cap

Effective headroom for a run is the **minimum** of its phase cap, its allocation cap and
`backstop_usd` — **caps are never summed**. `prices` is a flat map keyed
`"{provider}/{model}"`; a served model with no entry fails closed (the call cannot be priced, so the
run stops rather than settling at an invented price).

```json
"prices": { "openrouter/anthropic/claude-sonnet-4.6": { "input_per_million": 3.00,
                                                         "output_per_million": 15.00 } }
```

## Rules

- **Never** edit a config to make a preflight pass. A config change invalidates the config hash
  recorded in every existing ledger run header, and those runs then fail closed.
- Caps are application-level controls, **not** provider-side billing guarantees.
- Rebind a config/ledger pair only via `scripts/rebind_spend_config.py`, deliberately.

## Related

`docs/decision_log.md` records the approvals for every cap and price;
`docs/runbook.md` covers the operational procedure.
