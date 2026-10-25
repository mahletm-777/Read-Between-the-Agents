# `monitors/` — provider client and spend ledger

The only place in the repository that talks to a provider, and the budget control that gates it.

## Layout

| Module | Contains |
|---|---|
| `client.py` | `ModelClient` (the single OpenRouter boundary, `_post_json`), `LedgerGuard`, `validate_base_url`, `SpendConfigError`. **SHA-256-pinned** by `experiments/generation_probe_obj10_v1/manifest.json` — do not edit without re-freezing that manifest |
| `ledger.py` | `LedgerStore`, `Policy`, `LedgerGuard` primitives, `commitments`, `headroom`, `sweep_stale`, run headers. Also PINNED by the same manifest |
| `runner.py` | Generic run loop and stage execution |
| `prompts.py` | View rendering M1–M4 (`PROMPT_VERSION = "2.0"`), byte-level prompt text |
| `parser.py` | Reply parsing |
| `observable.py` | The `ObservableRecord` a monitor is allowed to see |
| `run_status.py` | Run status records |

## Safety properties

- One API key source: `APART_API_KEY`, read from the environment only; never logged or stored.
- Every request is **reserved durably before it is sent** and settled from provider-reported usage.
  An unresolvable cost is recorded as `unknown` at its worst case, never as zero.
- Caps fail closed: a missing `(allocation, phase)` pair or a missing model price refuses the call.
- One endpoint per config; redirecting the endpoint is refused.

## Reuse (offline)

```python
import sys; sys.path.insert(0, ".")
from monitors.ledger import LedgerStore, Policy, commitments
import json
policy = Policy.from_config(json.load(open("configs/spend.json")))
ledger = LedgerStore("results/spend/ledger.json").load(
    expected_config_hash=__import__("monitors.ledger", fromlist=["config_sha256"])
        .config_sha256(open("configs/spend.json","rb").read()), policy=policy)
print(commitments(ledger)["total"])
```

```bash
# Render monitor prompts without sending anything
python scripts/run_monitors.py --dry-run --views M1,M2,M3,M4
```

**Never** point `ModelClient` at a real endpoint in a test. Tests replace `_post_json` and make
`urllib.request.urlopen` raise.

## Related documents

`docs/monitor_spec.md`, `docs/runbook.md`, `docs/decision_log.md`.
