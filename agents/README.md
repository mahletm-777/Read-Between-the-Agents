# `agents/` — agent-side client, prompts and stage runner

The **agent** side of the game (distinct from `monitors/`, which is the auditor side).

| Module | Contains |
|---|---|
| `client.py` | `FakeAgentClient` (deterministic, offline) and the response record type |
| `prompts.py` | Agent system prompts and the frozen message templates, incl. the C1 neutral notice |
| `parser.py` | Action parsing and the canonical `RETRY_MESSAGE` |
| `runner.py` | Stage execution (`_call_stage`), trial records, run loop used by the E1 driver |

## Reuse (offline, no key)

```python
import sys; sys.path.insert(0, ".")
from agents.client import FakeAgentClient
client = FakeAgentClient(responder=lambda messages, temperature, seed, index:
                         '{"action": "B", "note": "fake"}', model="fake")
```

The fake client is what `python scripts/run_experiment_e1.py --dry-run` uses, so the whole pipeline
can be exercised with **zero provider calls and zero spend**.

## Notes

- `agents/prompts.py` is one of the files SHA-256-pinned by
  `experiments/generation_probe_obj10_v1/manifest.json`.
- Live runs go through `monitors/client.py`, not this folder; the guard lives on the monitor side.
