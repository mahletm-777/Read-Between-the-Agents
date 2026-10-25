"""E1 production-path integration tests — OFFLINE ONLY.

What these tests are for
------------------------
The E1 live path was wired but never driven end-to-end. These tests execute the
**real** orchestration and accounting chain:

    resolve arm/config/ledger -> live preflight -> aggregate budget -> project lock
    -> run creation + provenance -> trial dispatch -> response parsing
    -> reservation / dispatch marker / settlement -> per-trial journal
    -> terminal status -> output artifacts -> lock release

Only one thing is replaced: the external provider transport. ``ModelClient._post_json``
(the single place ``monitors/client.py`` touches the network) is stubbed, and
``urllib.request.urlopen`` is additionally made fatal so an overlooked call path
cannot reach a socket. A process-wide audit hook records every
``socket.*`` / ``urllib.*`` / ``http.client.*`` event, so network isolation is
asserted directly rather than inferred from "the fake was called".

All fixtures are temporary: configs, E1 ledgers (created through the supported
``LedgerStore.init`` API), output directories and the project lock all live under
``tmp_path``. ``runner.APPROVED_E1_ARMS`` and ``runner.E1_LEDGER_ROOT`` are
patched so the real ``results/spend_e1/`` ledgers are never written to.

Nothing here is evidence about the real provider. A stubbed transport does not
validate provider model identity, pricing or availability.
"""

import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import run_experiment_e1 as runner  # noqa: E402
from apart import experiment_e1 as e1  # noqa: E402

SONNET_ALIAS, SONNET_MODEL, SONNET_PRICE = "sonnet46", "anthropic/claude-sonnet-4.6", (3.0, 15.0)
GPT_ALIAS, GPT_MODEL, GPT_PRICE = "gpt54mini", "openai/gpt-5.4-mini", (0.75, 4.5)
#: RFC 2606 reserved TLD: never resolves, so even a leaked request cannot connect.
TEST_BASE_URL = "https://e1-integration.invalid"
FAKE_KEY = "integration-test-placeholder-not-a-real-key"
REAL_E1_LEDGERS = (ROOT / "results" / "spend_e1" / "sonnet46" / "ledger.json",
                   ROOT / "results" / "spend_e1" / "gpt54mini" / "ledger.json")
EXPECTED_ARTIFACTS = ("plan.json", "plan.sha256", "trials.jsonl", "run_status.json",
                      "conversion.jsonl", "summary.json")

# ------------------------------------------------------------------ audit hook

NETWORK_EVENTS: list[str] = []


def _record_network_event(event: str, args: tuple) -> None:
    if event.startswith(("socket.", "urllib.", "http.client.")):
        NETWORK_EVENTS.append(event)


sys.addaudithook(_record_network_event)


def _forbidden_urlopen(*args, **kwargs):
    raise AssertionError("a real network request was attempted: urlopen was reached")


# --------------------------------------------------------------------- fixtures


def write_config(path: Path, model: str, *, price=(3.0, 15.0),
                 phase_cap_usd: float = 2.0, cap_usd: float = 14.0) -> Path:
    """A temporary spend config in the same shape as configs/spend.json."""
    path.write_text(json.dumps({
        "provider": "openrouter",
        "model": model,
        "allocations": {"pilot_audit": {"cap_usd": cap_usd,
                                        "phase_caps_usd": {"gate": 12.0, "pilot": phase_cap_usd}}},
        "backstop_usd": 37.34,
        "stale_after_s": 600,
        "prices": {f"openrouter/{model}": {"input_per_million": price[0],
                                           "output_per_million": price[1]}},
    }, indent=2) + "\n", encoding="utf-8")
    return path


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """A complete temporary E1 world: two approved arms with empty ledgers.

    Patches every runner constant that would otherwise point into the repository,
    so no real E1 ledger, lock or output directory is touched.
    """
    from monitors.ledger import LedgerStore

    ledger_root = tmp_path / "spend_e1"
    arms = {}
    for alias, model, price in ((SONNET_ALIAS, SONNET_MODEL, SONNET_PRICE),
                                (GPT_ALIAS, GPT_MODEL, GPT_PRICE)):
        config = write_config(tmp_path / f"{alias}_spend.json", model, price=price)
        ledger = ledger_root / alias / "ledger.json"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        LedgerStore(ledger).init(config.read_bytes())
        arms[alias] = runner.E1Arm(alias=alias, provider="openrouter", model=model,
                                   endpoint=TEST_BASE_URL, spend_config=config,
                                   ledger_path=ledger)

    monkeypatch.setattr(runner, "APPROVED_E1_ARMS", arms)
    monkeypatch.setattr(runner, "E1_LEDGER_ROOT", ledger_root)
    monkeypatch.setattr(runner, "E1_GLOBAL_LOCK_PATH", ledger_root / "e1_run.lock")
    monkeypatch.setenv("APART_API_KEY", FAKE_KEY)
    monkeypatch.setenv("APART_BASE_URL", TEST_BASE_URL)
    monkeypatch.setattr(urllib.request, "urlopen", _forbidden_urlopen)
    NETWORK_EVENTS.clear()
    return {"arms": arms, "ledger_root": ledger_root, "tmp_path": tmp_path}


def read_ledger(arm) -> dict:
    """Read a temporary E1 ledger through the supported API (read-only)."""
    from monitors.ledger import LedgerStore, Policy, config_sha256
    policy = Policy.from_config(json.loads(arm.spend_config.read_text(encoding="utf-8")))
    return LedgerStore(arm.ledger_path).load(
        expected_config_hash=config_sha256(arm.spend_config.read_bytes()), policy=policy)


def policy_for(arm):
    from monitors.ledger import Policy
    return Policy.from_config(json.loads(arm.spend_config.read_text(encoding="utf-8")))


def install_transport(monkeypatch, *, served_model=None, content=None,
                      error=None, interrupt_at=None):
    """Replace ``ModelClient._post_json`` — the only provider boundary — with a stub.

    Returns the list of stubbed requests, so a test can assert the exact dispatch
    count. ``served_model``/``content``/``error``/``interrupt_at`` select a
    success, malformed, transport-failure or interruption scenario.
    """
    from monitors.client import ModelClient

    requests: list[dict] = []

    def fake_post_json(self, payload):
        index = len(requests)
        requests.append({"model": payload.get("model"),
                         "messages": payload.get("messages"),
                         "temperature": payload.get("temperature"),
                         "max_tokens": payload.get("max_tokens")})
        if interrupt_at is not None and index == interrupt_at:
            raise KeyboardInterrupt
        if error is not None:
            raise error
        body = content if content is not None else json.dumps({"action": "B", "note": "stub"})
        return {"id": f"stub-{index}", "model": served_model or self.model,
                "provider": "openrouter",
                "choices": [{"message": {"content": body}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 20}}

    monkeypatch.setattr(ModelClient, "_post_json", fake_post_json)
    return requests


def run_live(world, *extra, alias=SONNET_ALIAS, out_name=None, base_url=TEST_BASE_URL):
    """Invoke ``main`` in live mode against the temporary world.

    ``--base-url`` is mandatory for a live run, so the helper always supplies one.
    It is placed before ``extra`` so a caller can override it deliberately.
    """
    out_dir = world["tmp_path"] / "runs" / (out_name or f"e1-{alias}")
    argv = ["--out-dir", str(out_dir), "--confirm-live", "--i-have-live-authorization",
            "--model-alias", alias, "--by", "integration-test",
            "--base-url", base_url, *extra]
    return runner.main(argv), out_dir


def read_status(out_dir: Path) -> dict:
    return json.loads((out_dir / e1.STATUS_FILENAME).read_text(encoding="utf-8"))


def read_summary(out_dir: Path) -> dict:
    return json.loads((out_dir / e1.SUMMARY_FILENAME).read_text(encoding="utf-8"))


def trial_records(out_dir: Path) -> list[dict]:
    return e1.read_jsonl(out_dir / e1.TRIALS_FILENAME)


def planned_trial_count() -> int:
    """The frozen plan's trial count, read from the production plan builder."""
    return len(e1.planned_trials(e1.build_plan()))


def assert_status_reconciles_with_records(out_dir: Path, *, dispatched_trials: int) -> None:
    """The terminal status must be exactly derivable from the per-trial records.

    Invariant under test: no trial is silently unaccounted for, and the run status
    is never independent of the trial-level evidence.

    ``dispatched_trials`` is the expected length of ``run_status.json``'s
    ``dispatched_call_ids``. The dispatch gate records exactly one deterministic
    call identifier per *dispatched trial*; a parse retry inside a trial produces a
    second ledger event under the same call id with a ``:p2`` pass suffix, so the
    per-request counts are asserted against the ledger instead (see each test).
    """
    status = read_status(out_dir)
    records = trial_records(out_dir)
    counts = Counter(record["execution_status"] for record in records)

    assert status["n_planned_trials"] == len(records) == planned_trial_count()
    assert status["n_attempted"] == counts[e1.EXEC_LIVE] + counts[e1.EXEC_FAILED]
    assert status["n_failed"] == counts[e1.EXEC_FAILED]
    assert status["n_not_attempted"] == counts[e1.EXEC_NOT_ATTEMPTED]
    assert counts[e1.EXEC_INTERRUPTED] <= 1, "at most one trial can be interrupted"
    # Every planned trial is classified exactly once.
    assert (counts[e1.EXEC_LIVE] + counts[e1.EXEC_FAILED] + counts[e1.EXEC_INTERRUPTED]
            + counts[e1.EXEC_NOT_ATTEMPTED]) == len(records) == planned_trial_count()
    # The dispatch gate's own record agrees with the number of dispatched trials,
    # and no logical call is dispatched twice.
    assert len(status["dispatched_call_ids"]) == dispatched_trials
    assert len(set(status["dispatched_call_ids"])) == dispatched_trials, "no duplicate dispatch"
    assert status["plan_sha256"] == e1.plan_sha256(e1.build_plan())


# ============================================================================
# A. Required successful run, end to end through the production path
# ============================================================================


def test_integration_successful_live_run_end_to_end(world, monkeypatch):
    """Drives steps 1-12 of the production chain with only the transport stubbed."""
    sonnet, gpt = world["arms"][SONNET_ALIAS], world["arms"][GPT_ALIAS]
    transport = install_transport(monkeypatch)          # served model = configured model

    code, out_dir = run_live(world, "--max-spend-usd", "2.00")
    assert code == 0, "the stubbed production run must complete"

    # (11) terminal status and output artifacts
    assert sorted(p.name for p in out_dir.iterdir()) == sorted(EXPECTED_ARTIFACTS)
    status = read_status(out_dir)
    run_id = status["run_id"]
    assert status["status"] == "completed"
    assert status["run_mode"] == e1.EXEC_LIVE
    assert status["n_planned_trials"] == 80
    assert status["n_attempted"] == 80
    assert status["n_failed"] == 0
    assert status["n_not_attempted"] == 0
    assert status["requested_model"] == SONNET_MODEL
    assert len(status["dispatched_call_ids"]) == 80
    assert_status_reconciles_with_records(out_dir, dispatched_trials=80)

    # (6,7,8) every trial was dispatched once through the real execution code, and
    # the stub's provider-shaped reply was parsed and serialised
    assert len(transport) == 80, "exactly one provider request per trial"
    records = trial_records(out_dir)
    assert len(records) == 80
    assert {r["execution_status"] for r in records} == {e1.EXEC_LIVE}
    assert {r["run_mode"] for r in records} == {e1.EXEC_LIVE}
    assert {r["validity"] for r in records} == {"valid"}
    assert {r["parsed_action"] for r in records} == {"B"}
    assert {r["served_model"] for r in records} == {SONNET_MODEL}
    assert {r["attempt"] for r in records} == {1}
    assert len({r["trial_id"] for r in records}) == 80

    # (9) reservations and settlements against the temporary ledger
    ledger = read_ledger(sonnet)
    events = ledger["events"]
    assert len(events) == 80
    assert {event["state"] for event in events} == {"settled"}
    assert all(event.get("dispatched_ts") for event in events), "every dispatch is marked"
    assert all(event.get("ts_resolved") for event in events)
    policy = policy_for(sonnet)
    usage = {"prompt_tokens": 900, "completion_tokens": 20}
    expected_cost = policy.cost_micro(usage, SONNET_MODEL, "openrouter")
    assert expected_cost is not None and expected_cost > 0
    assert {event["settled_micro"] for event in events} == {expected_cost}
    assert all(event["settled_micro"] <= event["reserved_micro"] for event in events)

    # deterministic, unique call identifiers; nothing dispatched twice
    call_ids = [event["call_id"] for event in events]
    assert len(set(call_ids)) == 80
    assert all(call_id.startswith(f"e1:{run_id}:") and call_id.endswith(":r2:p1")
               for call_id in call_ids)
    assert len({(event["call_id"], event["attempt"]) for event in events}) == 80
    assert {event["run_id"] for event in events} == {run_id}

    # (5) run creation and provenance
    header = ledger["runs"][run_id]
    assert header["allocation_id"] == "pilot_audit" and header["phase"] == "pilot"
    assert header["config_sha256"] == hashlib.sha256(
        sonnet.spend_config.read_bytes()).hexdigest()
    assert header["provenance"]["model"] == f"openrouter/{SONNET_MODEL}"
    assert header["provenance"]["base_url"] == TEST_BASE_URL
    assert header["provenance"]["ledger_path"] == str(sonnet.ledger_path)
    assert header["provenance"]["e1_arm"] == SONNET_ALIAS
    assert header["input_hashes"]["plan_sha256"] == e1.plan_sha256(e1.build_plan())

    # (3) aggregate preflight result is recorded in the summary
    summary = read_summary(out_dir)
    assert summary["e1_arm"] == SONNET_ALIAS
    assert summary["aggregate_budget"]["committed_by_arm_micro"] == {
        SONNET_ALIAS: 0, GPT_ALIAS: 0}
    assert summary["aggregate_budget"]["atomic_across_ledgers"] is False
    assert summary["aggregate_budget"]["projected_micro"] <= 2_000_000

    # the other arm's temporary ledger was never touched
    assert read_ledger(gpt)["events"] == []

    # (12) the project lock is released after a normal terminal status
    assert not runner.E1_GLOBAL_LOCK_PATH.exists()
    assert not (out_dir / runner.RUN_LOCK_FILENAME).exists()

    # network isolation across the whole run
    assert NETWORK_EVENTS == [], f"unexpected network audit events: {NETWORK_EVENTS}"


def test_integration_committed_total_reconciles_with_settlements(world, monkeypatch):
    """Accounting invariant: commitments equal the sum of real settled charges."""
    from monitors.ledger import commitments

    install_transport(monkeypatch)
    code, out_dir = run_live(world, "--max-spend-usd", "2.00")
    assert code == 0
    ledger = read_ledger(world["arms"][SONNET_ALIAS])
    totals = commitments(ledger)
    assert totals["total"] == sum(event["settled_micro"] for event in ledger["events"])
    assert totals["allocation"] == {"pilot_audit": totals["total"]}
    assert totals["pair"] == {("pilot_audit", "pilot"): totals["total"]}
    # Settled charges are strictly below the worst-case reservations they replaced.
    reserved_total = sum(event["reserved_micro"] for event in ledger["events"])
    assert 0 < totals["total"] < reserved_total
    # Nothing was invented: no corrections, no missing evidence, no imports.
    assert [a for a in ledger["audit"] if a.get("type") == "correction"] == []
    assert ledger["missing_evidence"] == []
    assert ledger["imports"] == []


def test_integration_outputs_feed_the_frozen_analysis(world, monkeypatch):
    """The frozen analysis runs on the artifacts the live path produced."""
    install_transport(monkeypatch)
    code, out_dir = run_live(world, "--max-spend-usd", "2.00")
    assert code == 0
    analysis = e1.analyze_run_dir(out_dir)
    assert analysis["status"] == e1.STATUS_ESTIMATED
    assert analysis["eligibility"]["n_eligible_scenarios"] == 20
    assert analysis["eligibility"]["accounting_reconciles"] is True
    # Every receiver answered B, so every cell mean is 1.0 and the contrasts are 0.
    assert set(analysis["cell_means"].values()) == {1.0}
    for name in ("T", "J", "I"):
        assert analysis["contrasts"][name]["point_estimate"] == 0.0
        assert analysis["contrasts"][name]["n_boot"] == 2000
        assert analysis["contrasts"][name]["n_boot_invalid"] == 0
    assert analysis["no_pooling"] is True
    assert analysis["not_monitor_evaluation"] is True


# ============================================================================
# A. Transport failure before a valid response
# ============================================================================


def test_integration_transport_failure_is_recorded_once_and_keeps_worst_case(world, monkeypatch):
    """Step A: no duplicate dispatch, worst case retained, terminal status accurate."""
    from monitors.ledger import commitments

    sonnet = world["arms"][SONNET_ALIAS]
    transport = install_transport(
        monkeypatch, error=urllib.error.URLError("stub transport failure"))

    code, out_dir = run_live(world, "--max-spend-usd", "2.00")
    assert code == 0, "a per-trial transport failure does not stop the run (frozen semantics)"

    # Exactly one provider attempt per trial: ModelClient(max_attempts=1), and
    # _call_stage returns immediately on ApiError without a parse retry.
    assert len(transport) == 80, "no unintended duplicate dispatch"

    status = read_status(out_dir)
    assert status["status"] == "completed"
    assert status["n_planned_trials"] == 80
    assert status["n_attempted"] == 80
    assert status["n_failed"] == 80          # every trial failed
    assert status["n_not_attempted"] == 0    # nothing is silently unaccounted
    assert_status_reconciles_with_records(out_dir, dispatched_trials=80)

    records = trial_records(out_dir)
    assert len(records) == 80
    assert {r["execution_status"] for r in records} == {e1.EXEC_FAILED}
    assert {r["error"] for r in records} == {"api_error"}
    assert {r["error_stage"] for r in records} == {"receiver"}
    assert {r["validity"] for r in records} == {None}
    assert {r["parsed_action"] for r in records} == {None}
    assert {r["attempt"] for r in records} == {1}
    assert len({r["trial_id"] for r in records}) == 80

    # Ledger semantics: the reservation closes as `unknown`, and `unknown`
    # contributes its worst case — it is NOT silently treated as zero.
    ledger = read_ledger(sonnet)
    events = ledger["events"]
    assert len(events) == 80
    assert {event["state"] for event in events} == {"unknown"}
    assert {event["unknown_reason"] for event in events} == {"transport_error_URLError"}
    assert all(event.get("dispatched_ts") for event in events), "a real send was attempted"
    totals = commitments(ledger)
    assert totals["total"] == sum(event["reserved_micro"] for event in events)
    assert totals["total"] > 0, "unresolved cost must not be counted as zero"
    assert len({(event["call_id"], event["attempt"]) for event in events}) == 80

    # Nothing is imputed, so the run analyses as not_estimable rather than as data.
    assert e1.analyze_run_dir(out_dir)["status"] == e1.STATUS_NOT_ESTIMABLE
    assert not runner.E1_GLOBAL_LOCK_PATH.exists()
    assert NETWORK_EVENTS == [], NETWORK_EVENTS


# ============================================================================
# B. Unparseable response
# ============================================================================


def test_integration_unparseable_response_follows_the_parse_retry_policy(world, monkeypatch):
    """Step B: exactly one parse retry, accurate counts, run continues, nothing dropped."""
    from agents.parser import RETRY_MESSAGE

    sonnet = world["arms"][SONNET_ALIAS]
    transport = install_transport(monkeypatch, content="this is definitely not json")

    code, out_dir = run_live(world, "--max-spend-usd", "2.00")
    assert code == 0

    # One permitted parse retry per trial => two provider requests per trial.
    assert len(transport) == 160 == 80 * e1.RECEIVER_MAX_ATTEMPTS
    # The retry request carries the assistant turn and the canonical retry prompt.
    assert transport[1]["messages"][-1]["content"] == RETRY_MESSAGE
    assert transport[1]["messages"][-2]["role"] == "assistant"
    assert len(transport[1]["messages"]) == 4     # system, user, assistant, retry-user
    assert len(transport[0]["messages"]) == 2

    status = read_status(out_dir)
    assert status["status"] == "completed"
    assert status["n_attempted"] == 80
    assert status["n_failed"] == 0                # a parse failure is not a transport failure
    assert status["n_not_attempted"] == 0
    assert_status_reconciles_with_records(out_dir, dispatched_trials=80)

    records = trial_records(out_dir)
    assert len(records) == 80, "no unaccounted trial is created"
    assert {r["execution_status"] for r in records} == {e1.EXEC_LIVE}
    assert {r["validity"] for r in records} == {"invalid"}
    assert {r["reason"] for r in records} == {"parse_retries_exhausted"}
    assert {r["attempt"] for r in records} == {2}
    assert {r["parsed_action"] for r in records} == {None}
    assert len({r["trial_id"] for r in records}) == 80

    # Both parse attempts reserved and settled against the temporary ledger.
    ledger = read_ledger(sonnet)
    events = ledger["events"]
    assert len(events) == 160
    assert {event["state"] for event in events} == {"settled"}
    assert {event["call_id"].rsplit(":", 1)[-1] for event in events} == {"p1", "p2"}
    assert len({(event["call_id"], event["attempt"]) for event in events}) == 160

    # Unparseable replies are never imputed, so the run is not estimable.
    analysis = e1.analyze_run_dir(out_dir)
    assert analysis["status"] == e1.STATUS_NOT_ESTIMABLE
    assert analysis["eligibility"]["n_eligible_scenarios"] == 0
    assert NETWORK_EVENTS == [], NETWORK_EVENTS


# ============================================================================
# C. Served-model pricing mismatch
# ============================================================================


def test_integration_served_model_mismatch_stops_dispatch_without_inventing_a_price(world,
                                                                                   monkeypatch):
    """Step C: fail closed, no invented price, remaining trials marked, status accurate."""
    from monitors.ledger import commitments

    sonnet = world["arms"][SONNET_ALIAS]
    transport = install_transport(monkeypatch, served_model="some/undocumented-snapshot-2026-01")

    code, out_dir = run_live(world, "--max-spend-usd", "2.00")
    assert code == 2, "a run-stopping pricing failure is reported as a failed run"

    status = read_status(out_dir)
    assert status["status"] == "failed"
    assert status["stop_reason"] == runner.FATAL_SERVED_MODEL_UNPRICED
    assert status["n_attempted"] == 1
    assert status["n_not_attempted"] == 79
    assert_status_reconciles_with_records(out_dir, dispatched_trials=1)

    assert len(transport) == 1, "no further dispatch after the first unpriced served model"

    records = trial_records(out_dir)
    assert len(records) == 80
    counts = Counter(record["execution_status"] for record in records)
    assert counts[e1.EXEC_FAILED] == 1
    assert counts[e1.EXEC_NOT_ATTEMPTED] == 79
    assert counts[e1.EXEC_LIVE] == 0
    failed = [r for r in records if r["execution_status"] == e1.EXEC_FAILED][0]
    assert failed["error"] == runner.FATAL_SERVED_MODEL_UNPRICED
    assert failed["error_stage"] == "receiver"
    assert failed["served_model"] == "some/undocumented-snapshot-2026-01"
    assert failed["validity"] is None and failed["parsed_action"] is None

    # The offending reply settled as `unknown` at its worst-case reservation — never
    # at an invented or default price — and the run stopped before dispatching more.
    ledger = read_ledger(sonnet)
    events = ledger["events"]
    assert len(events) == 1
    assert events[0]["state"] == "unknown"
    assert events[0]["unknown_reason"] == "usage_missing_or_model_unpriced"
    assert events[0]["settled_micro"] is None, "no price may be invented for an unpriced model"
    assert events[0]["reserved_micro"] > 0
    assert commitments(ledger)["total"] == events[0]["reserved_micro"]

    assert not runner.E1_GLOBAL_LOCK_PATH.exists(), "a failed run must release the lock"
    assert NETWORK_EVENTS == [], NETWORK_EVENTS


# ============================================================================
# D. Interruption after dispatch begins
# ============================================================================


def test_integration_interruption_keeps_records_and_is_not_replayed(world, monkeypatch):
    """Step D: readable journal, no resume/replay, reservation state accurate."""
    from monitors.ledger import (REQUEST_OUTCOME_UNCERTAIN, LedgerStore, Policy,
                                 commitments, config_sha256, sweep_stale)

    sonnet = world["arms"][SONNET_ALIAS]
    transport = install_transport(monkeypatch, interrupt_at=4)   # 5th dispatch is interrupted

    code, out_dir = run_live(world, "--max-spend-usd", "2.00")
    assert code == 2
    status = read_status(out_dir)
    assert status["status"] == "interrupted"
    assert status["stop_reason"] == "keyboard_interrupt"
    assert status["n_attempted"] == 4
    assert status["n_not_attempted"] == 75
    assert len(transport) == 5, "the interrupted dispatch happened exactly once"
    assert_status_reconciles_with_records(out_dir, dispatched_trials=5)

    records = trial_records(out_dir)
    assert len(records) == 80, "journaled records remain readable after an interruption"
    counts = Counter(record["execution_status"] for record in records)
    assert counts[e1.EXEC_LIVE] == 4
    assert counts[e1.EXEC_INTERRUPTED] == 1
    assert counts[e1.EXEC_NOT_ATTEMPTED] == 75
    assert len({record["trial_id"] for record in records}) == 80

    # Ledger state: four settled dispatches plus one still open, with its dispatch
    # marker durable. The open reservation already counts at its worst case.
    ledger = read_ledger(sonnet)
    events = ledger["events"]
    assert len(events) == 5
    states = Counter(event["state"] for event in events)
    assert states["settled"] == 4 and states["reserved"] == 1
    in_flight = [event for event in events if event["state"] == "reserved"][0]
    assert in_flight["dispatched_ts"], "the send was recorded before it was attempted"
    committed = commitments(ledger)["total"]
    assert committed == (sum(e["settled_micro"] for e in events if e["state"] == "settled")
                         + in_flight["reserved_micro"])

    # Recovery follows the EXISTING policy: sweep_stale closes the open reservation
    # as `unknown` (request outcome uncertain) and keeps the worst case. Nothing is
    # released and no call is re-dispatched.
    digest = config_sha256(sonnet.spend_config.read_bytes())
    policy = Policy.from_config(json.loads(sonnet.spend_config.read_text(encoding="utf-8")))
    swept = sweep_stale(LedgerStore(sonnet.ledger_path), digest, policy.stale_after_s,
                        pid_alive=lambda _pid: False)
    assert len(swept) == 1
    recovered_ledger = read_ledger(sonnet)
    recovered = [e for e in recovered_ledger["events"]
                 if e["event_id"] == in_flight["event_id"]][0]
    assert recovered["state"] == "unknown"
    assert recovered["unknown_reason"] == REQUEST_OUTCOME_UNCERTAIN
    assert commitments(recovered_ledger)["total"] == committed, "worst case retained"

    # No resume and no replay: a second run cannot reuse the directory.
    again = runner.main(["--out-dir", str(out_dir), "--confirm-live",
                         "--i-have-live-authorization", "--model-alias", SONNET_ALIAS,
                         "--by", "integration-test"])
    assert again == 2
    assert len(transport) == 5, "an already-attempted call is never replayed"
    assert not runner.E1_GLOBAL_LOCK_PATH.exists()
    assert NETWORK_EVENTS == [], NETWORK_EVENTS


# ============================================================================
# E. Budget exhaustion
# ============================================================================


def test_integration_aggregate_budget_blocks_before_any_dispatch(world, monkeypatch):
    """Step E: the aggregate check rejects before any reservation or dispatch."""
    from monitors.ledger import (LedgerStore, Policy, config_sha256, make_run_header,
                                 open_run, reserve)

    sonnet, gpt = world["arms"][SONNET_ALIAS], world["arms"][GPT_ALIAS]
    transport = install_transport(monkeypatch)

    # (i) an operator-supplied ceiling below this run's own worst case
    code, out_dir = run_live(world, "--max-spend-usd", "0.000001", out_name="tiny-ceiling")
    assert code == 5
    assert not out_dir.exists(), "refused before the output directory is created"
    assert transport == [], "no dispatch after a rejected aggregate check"
    assert read_ledger(sonnet)["events"] == []

    # (ii) a real commitment in the OTHER arm's ledger counts toward the $4 ceiling.
    # Reserve $1.50 of the GPT arm's $2.00 pilot cap, then run Sonnet with a $2.50
    # ceiling: 1.50 committed + 1.176 worst case = 2.676 > 2.50, so it must refuse.
    config_bytes = gpt.spend_config.read_bytes()
    digest = config_sha256(config_bytes)
    policy = Policy.from_config(json.loads(config_bytes.decode("utf-8")))
    store = LedgerStore(gpt.ledger_path)
    open_run(store, policy, make_run_header(
        "e1-precommit", "pilot_audit", "pilot", digest, {"plan_sha256": "x"},
        "2026-01-01T00:00:00Z", provenance={"model": f"openrouter/{GPT_MODEL}"}),
        digest, "tester")
    reserve(store, policy, digest, "e1-precommit", "call-precommit", 0,
            f"openrouter/{GPT_MODEL}", 1_500_000)

    code, out_dir = run_live(world, "--max-spend-usd", "2.50", out_name="shared-ceiling")
    assert code == 5
    assert not out_dir.exists()
    assert transport == [], "no dispatch after a rejected aggregate check"
    assert read_ledger(sonnet)["events"] == [], "the refused run reserved nothing"
    assert len(read_ledger(gpt)["events"]) == 1, "the pre-existing commitment is untouched"
    assert not runner.E1_GLOBAL_LOCK_PATH.exists()
    assert NETWORK_EVENTS == [], NETWORK_EVENTS


# ============================================================================
# F. Lock contention
# ============================================================================


def test_integration_lock_contention_blocks_the_spend_bearing_path(world, monkeypatch):
    """Step F: a held project lock blocks a second run before any spend-bearing work."""
    sonnet = world["arms"][SONNET_ALIAS]
    transport = install_transport(monkeypatch)

    holder = runner.E1ProjectLock(runner.E1_GLOBAL_LOCK_PATH,
                                  {"run_id": "e1-holder", "model_alias": SONNET_ALIAS,
                                   "pid": os.getpid()})
    holder.acquire()
    try:
        code, out_dir = run_live(world, "--max-spend-usd", "2.00")
        assert code == 5
        assert not out_dir.exists(), "blocked before the output directory is created"
        assert transport == [], "no dispatch while another run holds the lock"
        assert read_ledger(sonnet)["events"] == [], "no reservation while the lock is held"
        assert runner.E1_GLOBAL_LOCK_PATH.exists()
    finally:
        holder.release()

    # Released after the holder finished; a subsequent run is admitted again.
    assert not runner.E1_GLOBAL_LOCK_PATH.exists()
    code, out_dir = run_live(world, "--max-spend-usd", "2.00", out_name="after-release")
    assert code == 0
    assert read_status(out_dir)["status"] == "completed"
    assert not runner.E1_GLOBAL_LOCK_PATH.exists()
    assert NETWORK_EVENTS == [], NETWORK_EVENTS


# ============================================================================
# Endpoint binding
# ============================================================================


def test_integration_base_url_mismatch_is_refused_before_dispatch(world, monkeypatch):
    """An endpoint that is not the validated environment endpoint is refused."""
    sonnet = world["arms"][SONNET_ALIAS]
    transport = install_transport(monkeypatch)

    code, out_dir = run_live(world, "--max-spend-usd", "2.00",
                             base_url="https://a-different-endpoint.invalid")
    assert code == 5
    assert not out_dir.exists()
    assert transport == []
    assert read_ledger(sonnet)["events"] == []
    assert not runner.E1_GLOBAL_LOCK_PATH.exists()


def test_integration_unapproved_endpoint_is_refused_before_any_live_side_effect(world,
                                                                               monkeypatch):
    """An endpoint outside the arm's allowlist never reaches the lock, ledger or transport.

    The environment is tampered with (not just the CLI flag), so the arm-bound
    allowlist in the resolver is what refuses — and it does so before the project
    lock is acquired, before the output directory exists, before any reservation,
    and before any dispatch.
    """
    sonnet = world["arms"][SONNET_ALIAS]
    transport = install_transport(monkeypatch)

    acquisitions: list[dict] = []
    real_lock = runner.E1ProjectLock

    class RecordingLock(real_lock):          # type: ignore[misc, valid-type]
        def acquire(self):
            acquisitions.append(dict(self.payload))
            return super().acquire()

    monkeypatch.setattr(runner, "E1ProjectLock", RecordingLock)

    # Most cases are variations of the arm's OWN approved host, so they prove exact
    # matching rather than merely "a different host".
    hostile = [
        "https://e1-integration.invalid.evil.example/api/v1",   # suffix confusion
        "https://192.0.2.7/api/v1",                             # IP literal
        "https://e1-integration.invalid:8443/api/v1",           # unexpected port
        "https://e1-integration.invalid/api/v2",                # alternate path
        "http://e1-integration.invalid/api/v1",                 # wrong scheme
    ]
    for index, url in enumerate(hostile):
        monkeypatch.setenv("APART_BASE_URL", url)
        code, out_dir = run_live(world, "--max-spend-usd", "2.00",
                                 out_name=f"reject-{index}", base_url=url)
        assert code == 5, url
        assert not out_dir.exists(), url
        assert not runner.E1_GLOBAL_LOCK_PATH.exists(), url

    assert acquisitions == [], "the project lock must not be acquired for a rejected endpoint"
    assert transport == [], "no provider request may occur for an unapproved endpoint"
    assert read_ledger(sonnet)["events"] == []
    assert read_ledger(sonnet)["runs"] == {}
    assert NETWORK_EVENTS == [], NETWORK_EVENTS


def test_integration_matching_base_url_is_accepted(world, monkeypatch):
    """Supplying the correct --base-url is not obstructed."""
    transport = install_transport(monkeypatch)
    code, out_dir = run_live(world, "--max-spend-usd", "2.00")
    assert code == 0
    assert len(transport) == 80
    assert read_status(out_dir)["status"] == "completed"
    assert NETWORK_EVENTS == [], NETWORK_EVENTS


# ============================================================================
# A third (unapproved) E1 ledger widens the approved ceiling
# ============================================================================


def test_integration_unapproved_third_ledger_is_refused_before_dispatch(world, monkeypatch):
    """The aggregate gate requires exactly the approved ledger set, so it aborts the run."""
    from monitors.ledger import LedgerStore

    sonnet = world["arms"][SONNET_ALIAS]
    transport = install_transport(monkeypatch)

    rogue_config = write_config(world["tmp_path"] / "rogue_spend.json", "vendor/rogue-model")
    rogue = world["ledger_root"] / "roguearm" / "ledger.json"
    rogue.parent.mkdir(parents=True, exist_ok=True)
    LedgerStore(rogue).init(rogue_config.read_bytes())

    code, out_dir = run_live(world, "--max-spend-usd", "2.00")
    assert code == 5
    assert not out_dir.exists(), "aborted before the output directory is created"
    assert transport == [], "no dispatch while the approved ledger set is inconsistent"
    assert read_ledger(sonnet)["events"] == []
    assert not runner.E1_GLOBAL_LOCK_PATH.exists()
    assert NETWORK_EVENTS == [], NETWORK_EVENTS


# ============================================================================
# Temporary fixtures must not touch the real approved E1 ledgers
# ============================================================================


def test_network_isolation_guards_detect_a_real_attempt(world):
    """Positive control: the isolation mechanism is not vacuous.

    Proves (a) the process audit hook really records socket creation, and (b) the
    ``urlopen`` shim is installed and fatal. Without this control, "no events were
    recorded" would be indistinguishable from "the hook never fires".

    ``monitors/client.py:744`` (``ModelClient._post_json``) is the only
    ``urllib.request.urlopen`` call site reachable from the E1 live path, so the
    three guards together cover every possible provider request.
    """
    import socket

    assert NETWORK_EVENTS == []
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.close()
    assert any(event.startswith("socket.") for event in NETWORK_EVENTS), NETWORK_EVENTS
    with pytest.raises(AssertionError, match="real network request was attempted"):
        urllib.request.urlopen("https://e1-integration.invalid/never-reached")
    NETWORK_EVENTS.clear()


def test_integration_does_not_touch_the_real_e1_ledgers(world, monkeypatch):
    """The approved E1 ledgers are byte-identical before and after a temporary run.

    Reads the two approved ledgers read-only and compares bytes and mtimes. They
    are never opened for writing by these tests.
    """
    assert all(path.exists() for path in REAL_E1_LEDGERS), "both approved ledgers must exist"
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in REAL_E1_LEDGERS}

    install_transport(monkeypatch)
    assert run_live(world, "--max-spend-usd", "2.00")[0] == 0

    after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in REAL_E1_LEDGERS}
    assert after == before, "a temporary integration run modified a real E1 ledger"


# ============================================================================
# Network isolation in a fresh interpreter
# ============================================================================

PROBE_SOURCE = '''
import json, os, sys, urllib.request
from pathlib import Path

root, work = Path(sys.argv[1]), Path(sys.argv[2])
work.mkdir(parents=True, exist_ok=True)
for entry in (str(root), str(root / "src"), str(root / "scripts")):
    sys.path.insert(0, entry)

events = []
def _hook(event, args):
    if event.startswith(("socket.", "urllib.", "http.client.")):
        events.append(event)
sys.addaudithook(_hook)

def _forbidden(*args, **kwargs):
    raise AssertionError("urlopen reached in the isolation probe")
urllib.request.urlopen = _forbidden

import run_experiment_e1 as runner
from monitors.client import ModelClient
from monitors.ledger import LedgerStore

MODEL = "anthropic/claude-sonnet-4.6"

def write_config(path):
    path.write_text(json.dumps({
        "provider": "openrouter", "model": MODEL,
        "allocations": {"pilot_audit": {"cap_usd": 14.0,
                                        "phase_caps_usd": {"gate": 12.0, "pilot": 2.0}}},
        "backstop_usd": 37.34, "stale_after_s": 600,
        "prices": {"openrouter/" + MODEL: {"input_per_million": 3.0,
                                           "output_per_million": 15.0}},
    }))
    return path

ledger_root = work / "spend_e1"
config = write_config(work / "spend.json")
ledger = ledger_root / "sonnet46" / "ledger.json"
ledger.parent.mkdir(parents=True, exist_ok=True)
LedgerStore(ledger).init(config.read_bytes())

runner.APPROVED_E1_ARMS = {"sonnet46": runner.E1Arm(
    alias="sonnet46", provider="openrouter", model=MODEL,
    endpoint="https://e1-integration.invalid",
    spend_config=config, ledger_path=ledger)}
runner.E1_LEDGER_ROOT = ledger_root
runner.E1_GLOBAL_LOCK_PATH = ledger_root / "e1_run.lock"

calls = []
def fake_post_json(self, payload):
    calls.append(1)
    return {"id": "probe", "model": self.model, "provider": "openrouter",
            "choices": [{"message": {"content": json.dumps({"action": "B", "note": ""})}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 20}}
ModelClient._post_json = fake_post_json

os.environ["APART_API_KEY"] = "subprocess-placeholder-not-a-real-key"
os.environ["APART_BASE_URL"] = "https://e1-integration.invalid"

code = runner.main(["--out-dir", str(work / "run"), "--confirm-live",
                    "--i-have-live-authorization", "--model-alias", "sonnet46",
                    "--base-url", "https://e1-integration.invalid",
                    "--max-spend-usd", "2.00", "--by", "isolation-probe"])
assert code == 0, code
assert len(calls) == 80, len(calls)
assert events == [], events
assert not runner.E1_GLOBAL_LOCK_PATH.exists()
print("ISOLATION_OK")
'''


def test_integration_network_isolation_in_a_fresh_interpreter(tmp_path):
    """A separate process runs the whole live path with zero network audit events."""
    probe = tmp_path / "isolation_probe.py"
    probe.write_text(PROBE_SOURCE, encoding="utf-8")
    result = subprocess.run([sys.executable, str(probe), str(ROOT), str(tmp_path / "probe")],
                            capture_output=True, text=True, cwd=str(ROOT))
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "ISOLATION_OK" in result.stdout
