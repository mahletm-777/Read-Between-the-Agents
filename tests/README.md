# `tests/` — test suite

pytest, offline only. **No test calls a provider or needs an API key.** Network-touching code paths
are exercised with injected fakes; several tests additionally install an audit hook that fails if a
socket is opened.

## Run

```bash
python -m pytest tests/                       # whole suite
python -m pytest tests/test_experiment_e1.py  # one module
```

## Layout by area

| Area | Files |
|---|---|
| **Exploratory receiver follow-up (E1)** | `test_experiment_e1.py` (plan invariants, contrasts, eligibility, bootstrap, serialisation), `test_experiment_e1_adapter.py` (conversion/rejection ladder), `test_experiment_e1_runner.py` (journal, interruption, replay, live gating, endpoint authorisation), `test_experiment_e1_integration.py` (production path end-to-end with a **stubbed transport**) |
| **Monitor evaluation** | `test_monitors.py`, `test_ledger_client.py`, `test_settlement_overrun.py`, `test_guarded_generation.py`, `test_e_sheet_labels_matrix.py` |
| **Sender-receiver interaction** | `test_phase_a_live.py`, `test_phase_a_runner.py`, `test_phase_a_parser.py`, `test_phase_a_behavioral_outcomes.py`, `test_phase_a_results.py` |
| **Spend / ledger** | `test_spend_guard.py`, `test_ledger*.py` |
| **Labels / annotation** | `test_e_analysis.py`, `test_generation_probe.py`, calibration tests |
| **Historical Y_H lineage** | `test_analysis.py`, `test_generator.py` |

## Fakes, not mocks of the thing under test

* `monitors/client.py` is the only module that opens a socket. It is exercised with a fake transport
  and with `urllib.request.urlopen` replaced by a function that raises, so an overlooked call path
  cannot reach the network.
* `ModelClient._post_json` is the single provider boundary; the E1 integration tests replace exactly
  that and nothing else.
* Ledgers and spend configs are created in `tmp_path` through the supported `LedgerStore.init` API.

## Known pre-existing failure

`tests/test_spend_guard.py::test_no_placeholder_model_config_is_shipped` fails on a clean checkout.
It asserts that no file under `configs/` contains the string `gpt-5.4-mini`, but
`configs/spend_gpt54mini.json` legitimately does. The test is stale relative to the recorded approval
of the GPT-5.4-mini arm. It predates the exploratory follow-up work and is **not** caused by it.

## Reuse

Tests read fixtures from `tmp_path` and from `runs/` / `results/` where a stored artefact is the point
of the test. Nothing writes to `results/` or `runs/`. Example that is safe to run anywhere:

```bash
python -m pytest tests/test_experiment_e1.py -q
```
