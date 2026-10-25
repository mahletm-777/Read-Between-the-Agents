"""E1 runner: persistence, interruption, replay, dry-run isolation, live gating.

Offline only. Every test uses a temporary directory and a fake client; nothing
here can reach a provider or touch the spend ledger.
"""

import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import run_experiment_e1 as runner  # noqa: E402
from agents.client import FakeAgentClient  # noqa: E402
from apart import experiment_e1 as e1  # noqa: E402
from monitors.client import ApiError  # noqa: E402

#: Endpoint pinned on every temporary test arm and supplied by ``approve_env``.
#: It is deliberately NOT the real approved endpoint, so no test can accidentally
#: authorise a call to a real provider.
TEST_ENDPOINT = "https://example.invalid"


def answer(action="A"):
    def responder(messages, temperature, seed, index):
        return json.dumps({"action": action, "note": "fake"})
    return responder


def counting_run(plan, out_dir, run_id="e1-run", responder=None, **kwargs):
    client = FakeAgentClient(responder=responder or answer(), model="fake-e1-receiver")
    outcome = runner.run_plan(plan, client, run_id, e1.EXEC_DRY_RUN_FAKE, out_dir, **kwargs)
    return client, outcome


# ------------------------------------------------------------------ plan / CLI


def test_t07_plan_trial_ids_are_unique_and_derive_from_scenario_and_cell():
    plan = e1.build_plan()
    ids = [e1.trial_id_for(scenario_id, cell_id) for scenario_id, cell_id in e1.planned_trials(plan)]
    assert len(ids) == 80 == len(set(ids))
    call_ids = {e1.call_id_for("e1-x", scenario_id, cell_id)
                for scenario_id, cell_id in e1.planned_trials(plan)}
    assert len(call_ids) == 80


def test_t07b_existing_output_directory_is_refused(tmp_path):
    first = tmp_path / "run"
    assert runner.main(["--out-dir", str(first), "--dry-run"]) == 0
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(first.iterdir())}
    assert runner.main(["--out-dir", str(first), "--dry-run"]) == 2
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(first.iterdir())}
    assert before == after


def test_t23_no_output_overwrite_on_re_analysis(tmp_path):
    out = tmp_path / "run"
    assert runner.main(["--out-dir", str(out), "--dry-run"]) == 0
    assert runner.main(["--analyze-run-dir", str(out)]) == 0
    analysis = json.loads((out / e1.ANALYSIS_JSON_FILENAME).read_text())
    assert analysis["status"] == e1.STATUS_ESTIMATED
    # A second analysis must not silently replace the first artefact.
    assert runner.main(["--analyze-run-dir", str(out)]) == 4


def test_a_bad_mode_combination_is_refused(tmp_path):
    assert runner.main([]) == 2
    assert runner.main(["--dry-run", "--confirm-live", "--out-dir", str(tmp_path / "x")]) == 2
    assert runner.main(["--dry-run"]) == 2       # no --out-dir


def test_a_replacement_run_records_supersession_without_touching_the_old_run(tmp_path):
    original = tmp_path / "run-1"
    replacement = tmp_path / "run-2"
    assert runner.main(["--out-dir", str(original), "--dry-run",
                        "--run-id", "e1-original"]) == 0
    frozen = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(original.iterdir())}
    assert runner.main(["--out-dir", str(replacement), "--dry-run",
                        "--run-id", "e1-replacement",
                        "--supersedes", "e1-original",
                        "--supersession-reason", "machine sandbox blocked the first run"]) == 0
    status = json.loads((replacement / e1.STATUS_FILENAME).read_text())
    assert status["supersedes_run_id"] == "e1-original"
    assert status["supersession_reason"]
    assert status["run_id"] == "e1-replacement"
    # The superseded directory is byte-identical: never rewritten.
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(original.iterdir())} == frozen


# ------------------------------------------------------- persistence (T20/T21)


def test_t20_each_completed_result_is_on_disk_before_the_next_call(tmp_path):
    plan = e1.build_plan()
    out = tmp_path / "run"
    out.mkdir()
    trials = out / e1.TRIALS_FILENAME
    observed = []

    def responder(messages, temperature, seed, index):
        text = trials.read_text(encoding="utf-8") if trials.exists() else ""
        observed.append(len([line for line in text.splitlines() if line.strip()]))
        return json.dumps({"action": "A", "note": "fake"})

    client, outcome = counting_run(plan, out, responder=responder)
    assert outcome.status == "completed"
    assert outcome.n_attempted == 80 and outcome.n_failed == 0
    # At call i exactly i results were already durable.
    assert observed == list(range(80))
    assert len(client.calls) == 80
    assert len(e1.read_jsonl(trials)) == 80


def test_t20b_run_status_is_written_during_the_run_and_records_dispatches(tmp_path):
    plan = e1.build_plan()
    out = tmp_path / "run"
    out.mkdir()
    _client, outcome = counting_run(plan, out, run_id="e1-status")
    status = json.loads((out / e1.STATUS_FILENAME).read_text())
    assert status["schema"] == "apart-e1-run-status/1"
    assert status["run_id"] == "e1-status"
    assert status["status"] == "completed"
    assert status["n_planned_trials"] == 80
    assert status["n_attempted"] == 80
    assert status["n_not_attempted"] == 0
    assert len(status["dispatched_call_ids"]) == 80
    assert status["run_mode"] == e1.EXEC_DRY_RUN_FAKE
    assert outcome.exit_code == 0


def test_t21_keyboard_interrupt_keeps_completed_trials_and_records_the_rest(tmp_path):
    plan = e1.build_plan()
    out = tmp_path / "run"
    out.mkdir()
    stop_after = 5

    def responder(messages, temperature, seed, index):
        if index == stop_after:
            raise KeyboardInterrupt
        return json.dumps({"action": "B", "note": "fake"})

    client, outcome = counting_run(plan, out, run_id="e1-interrupted", responder=responder)
    assert outcome.status == "interrupted"
    assert outcome.stop_reason == "keyboard_interrupt"
    assert outcome.n_attempted == stop_after
    assert outcome.n_not_attempted == 80 - stop_after - 1
    assert outcome.exit_code == 2
    assert len(client.calls) == stop_after + 1

    records = e1.read_jsonl(out / e1.TRIALS_FILENAME)
    assert len(records) == 80, "every planned trial must be accounted for"
    statuses = Counter(record["execution_status"] for record in records)
    assert statuses[e1.EXEC_DRY_RUN_FAKE] == stop_after
    assert statuses[e1.EXEC_INTERRUPTED] == 1
    assert statuses[e1.EXEC_NOT_ATTEMPTED] == 80 - stop_after - 1

    # The interrupted trial carries its call id, so it is identifiable.
    interrupted = [r for r in records if r["execution_status"] == e1.EXEC_INTERRUPTED]
    assert len(interrupted) == 1 and interrupted[0]["call_id"]

    status = json.loads((out / e1.STATUS_FILENAME).read_text())
    assert status["status"] == "interrupted"
    assert status["stop_reason"] == "keyboard_interrupt"
    assert status["n_not_attempted"] == 80 - stop_after - 1

    # No imputation: an interrupted run analyses as not_estimable, not as a number.
    analysis = e1.analyze(records, plan, n_boot=10)
    assert analysis["eligibility"]["n_planned_scenarios"] == 20
    assert analysis["eligibility"]["accounting_reconciles"] is True
    if analysis["status"] == e1.STATUS_NOT_ESTIMABLE:
        for name in ("T", "J", "I"):
            assert analysis["contrasts"][name]["point_estimate"] is None


def test_t21b_an_arbitrary_failure_retains_records_and_marks_the_run_failed(tmp_path):
    plan = e1.build_plan()
    out = tmp_path / "run"
    out.mkdir()
    stop_after = 3

    def responder(messages, temperature, seed, index):
        if index == stop_after:
            raise RuntimeError("simulated serialization failure")
        return json.dumps({"action": "A", "note": "fake"})

    _client, outcome = counting_run(plan, out, run_id="e1-failed", responder=responder)
    assert outcome.status == "failed"
    assert outcome.stop_reason == "RuntimeError"
    assert "simulated serialization failure" in outcome.error
    assert outcome.exit_code == 2
    records = e1.read_jsonl(out / e1.TRIALS_FILENAME)
    assert len(records) == 80
    statuses = Counter(record["execution_status"] for record in records)
    assert statuses[e1.EXEC_DRY_RUN_FAKE] == stop_after
    assert statuses[e1.EXEC_INTERRUPTED] == 1
    # Nothing is imputed and no already-durable result is lost.
    assert statuses[e1.EXEC_NOT_ATTEMPTED] == 80 - stop_after - 1


def test_t22_a_transport_failure_is_recorded_once_and_never_re_dispatched(tmp_path):
    plan = e1.build_plan()
    out = tmp_path / "run"
    out.mkdir()
    calls = []

    def responder(messages, temperature, seed, index):
        calls.append(index)
        if index == 2:
            raise ApiError("simulated transport failure")
        return json.dumps({"action": "A", "note": "fake"})

    client, outcome = counting_run(plan, out, run_id="e1-transport", responder=responder)
    assert outcome.status == "completed"          # a per-trial failure does not stop the run
    assert outcome.n_failed == 1
    assert len(client.calls) == 80                # exactly one call per trial; no replay
    assert calls.count(2) == 1
    records = e1.read_jsonl(out / e1.TRIALS_FILENAME)
    failed = [r for r in records if r["execution_status"] == e1.EXEC_FAILED]
    assert len(failed) == 1
    assert failed[0]["error"] == "api_error" and failed[0]["error_stage"] == "receiver"
    assert failed[0]["validity"] is None and failed[0]["parsed_action"] is None


def test_t22b_the_dispatch_gate_refuses_a_repeated_call_id_and_attempt(tmp_path):
    gate = runner.E1DispatchGate()
    gate.check("call-a", 1)
    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        gate.check("call-a", 1)
    assert "re-dispatch" in str(excinfo.value)
    gate.check("call-a", 2)          # a parse retry is a different reservation
    gate.check("call-b", 1)
    assert len(gate) == 3


def test_t22c_call_ids_are_deterministic_so_a_restart_collides():
    plan = e1.build_plan()
    scenario_id, cell_id = e1.planned_trials(plan)[0]
    first = e1.call_id_for("e1-run-x", scenario_id, cell_id)
    assert first == e1.call_id_for("e1-run-x", scenario_id, cell_id)
    # A different run must not share a reservation key, so a replacement run
    # cannot silently re-use a settled call.
    assert first != e1.call_id_for("e1-run-y", scenario_id, cell_id)
    # The logical call id is stable across trials and carries no random element.
    ids = {e1.call_id_for("e1-run-x", sid, cell) for sid, cell in e1.planned_trials(plan)}
    assert len(ids) == 80
    assert all(call_id.startswith("e1:e1-run-x:") for call_id in ids)


# --------------------------------------------------- dry-run isolation (T24)


def test_t24_dry_run_runs_offline_and_reads_no_spend_config_or_ledger(tmp_path):
    """A subprocess proves the property: audit hooks record file opens and sockets.

    Note: ``monitors.client`` (and transitively ``monitors.ledger``) *are*
    imported, because ``agents.runner`` imports ``monitors.client`` for
    ``ApiError``. What is asserted here is the property that matters -- no spend
    config or ledger file is opened, no ``LedgerStore`` is constructed, and no
    socket is created.
    """
    out = tmp_path / "run"
    script = f'''
import sys
sys.path.insert(0, {str(ROOT)!r})
sys.path.insert(0, {str(ROOT / "src")!r})
sys.path.insert(0, {str(ROOT / "scripts")!r})

opened = []
network = []
def hook(event, args):
    if event == "open" and args and isinstance(args[0], str):
        opened.append(args[0])
    if event.startswith(("socket.", "urllib.", "http.")):
        network.append(event)
sys.addaudithook(hook)

import monitors.ledger as ledger
def _forbidden(*args, **kwargs):
    raise AssertionError("a dry run must not construct a LedgerStore")
ledger.LedgerStore.__init__ = _forbidden

import run_experiment_e1 as runner
code = runner.main(["--out-dir", {str(out)!r}, "--dry-run", "--run-id", "e1-t24"])
assert code == 0, code

offenders = [p for p in opened
             if "results/spend" in p or p.endswith("spend.json")
             or p.endswith("spend_gpt54mini.json") or p.endswith("ledger.json")]
assert not offenders, offenders
assert not network, network
assert "apart.phase_a_live" not in sys.modules
print("DRYRUN_OK")
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, cwd=str(ROOT))
    assert result.returncode == 0, result.stderr
    assert "DRYRUN_OK" in result.stdout

    records = e1.read_jsonl(out / e1.TRIALS_FILENAME)
    assert len(records) == 80
    assert {r["run_mode"] for r in records} == {e1.EXEC_DRY_RUN_FAKE}
    status = json.loads((out / e1.STATUS_FILENAME).read_text())
    assert status["requested_model"] == "fake-e1-receiver"


def test_t24b_a_run_writes_only_inside_its_own_output_directory(tmp_path):
    out = tmp_path / "run"
    assert runner.main(["--out-dir", str(out), "--dry-run"]) == 0
    assert sorted(p.name for p in out.iterdir()) == sorted([
        "plan.json", "plan.sha256", "run_status.json", "summary.json",
        "trials.jsonl", "conversion.jsonl"])
    assert not (tmp_path / "plan.json").exists()
    assert not (tmp_path / "trials.jsonl").exists()
    # The execution lease is released when the run finishes.
    assert not (out / "run.lock").exists()


def test_t16b_the_summary_states_scope_and_pooling_limits(tmp_path):
    out = tmp_path / "run"
    assert runner.main(["--out-dir", str(out), "--dry-run", "--model-alias", "smoke"]) == 0
    summary = json.loads((out / e1.SUMMARY_FILENAME).read_text())
    assert summary["experiment"] == "e1"
    assert summary["plan_version"] == e1.PLAN_VERSION
    assert summary["plan_sha256"] == e1.plan_sha256(e1.build_plan())
    assert summary["not_monitor_evaluation"] is True
    assert summary["no_pooling"] is True
    assert summary["status"] == "completed"
    assert summary["n_valid_traces"] == 80
    assert summary["counts_by_status"]["valid"] == 80
    # No cross-model aggregate of any kind is emitted.
    assert "pooled" not in json.dumps(summary).lower()


# --------------------------------------------------------- live gating (T25)


def live_strategy(**overrides):
    base = dict(model_alias="sonnet46",
                spend_config=Path("/nonexistent/spend.json"),
                ledger_path=Path("/nonexistent/ledger.json"),
                allocation="pilot_audit", phase="pilot",
                base_url="https://example.invalid", approved=True,
                max_spend_micro=4_000_000)
    base.update(overrides)
    return runner.LiveStrategy(**base)


@pytest.fixture(autouse=True)
def _isolate_project_lock(tmp_path, monkeypatch):
    """Redirect the project E1 run lock so no test touches results/spend_e1/."""
    monkeypatch.setattr(runner, "E1_GLOBAL_LOCK_PATH", tmp_path / "e1_run.lock")
    yield


def resolvers(price=("p", "m", (1, 2)), headroom=1_000_000, worst=500_000):
    return dict(resolve_price=lambda: price if price else None,
                headroom_micro=lambda: headroom, worst_case_micro=lambda: worst)


def test_t25_live_preflight_passes_only_when_every_precondition_is_met():
    assert runner.preflight_live(live_strategy(), **resolvers())["approved"] is True


@pytest.mark.parametrize("overrides,fragment", [
    ({"approved": False}, "live_execution_not_approved"),
    ({"allocation": ""}, "live_allocation_phase_unresolved"),
    ({"phase": ""}, "live_allocation_phase_unresolved"),
    ({"base_url": ""}, "live_base_url_missing"),
])
def test_t25b_live_preflight_blocks_unapproved_or_unresolved_inputs(overrides, fragment):
    from dataclasses import replace

    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.preflight_live(replace(live_strategy(), **overrides), **resolvers())
    assert fragment in str(excinfo.value)


def test_t25c_live_preflight_blocks_an_unpriced_model_or_insufficient_headroom():
    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.preflight_live(live_strategy(), **resolvers(price=None))
    assert "live_model_price_unresolved" in str(excinfo.value)

    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.preflight_live(live_strategy(), **resolvers(headroom=100, worst=200))
    assert "live_budget_insufficient" in str(excinfo.value)

    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.preflight_live(live_strategy(), **resolvers(worst=0))
    assert "live_worst_case_reservation_not_positive" in str(excinfo.value)


def test_t25d_live_preflight_enforces_an_operator_supplied_ceiling():
    from dataclasses import replace

    tight = replace(live_strategy(), max_spend_micro=100)
    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.preflight_live(tight, **resolvers(headroom=10 ** 9, worst=500))
    assert "live_spend_ceiling_exceeded" in str(excinfo.value)


def test_t25e_live_pathway_is_wired_and_refuses_unauthorised_or_unknown_arms(tmp_path):
    """The live path is production-wired; the refusals are what keep it fail-closed."""
    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.live_strategy_resolvers(live_strategy(approved=False))
    assert "live_execution_not_approved" in str(excinfo.value)

    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.live_strategy_resolvers(live_strategy(model_alias="not-an-approved-arm"))
    assert "unknown_e1_model_alias" in str(excinfo.value)

    # The CLI cannot reach a dispatch for an unapproved alias either.
    assert runner.main(["--out-dir", str(tmp_path / "live"), "--confirm-live",
                        "--i-have-live-authorization", "--allocation", "pilot_audit",
                        "--phase", "pilot", "--base-url", "https://example.invalid"]) == 5
    assert not (tmp_path / "live").exists()


def test_t25f_confirm_live_without_authorization_is_blocked_before_any_request(tmp_path,
                                                                             monkeypatch):
    """--confirm-live without --i-have-live-authorization is refused.

    The environment and arm are fully valid here, so the only missing precondition
    is the authorisation assertion itself.
    """
    approve_env(monkeypatch)
    make_arm(tmp_path, "sonnet46", SONNET_MODEL, monkeypatch)
    assert runner.main(["--out-dir", str(tmp_path / "live2"), "--confirm-live",
                        "--model-alias", "sonnet46",
                        "--base-url", TEST_ENDPOINT]) == 5
    assert not (tmp_path / "live2" / e1.TRIALS_FILENAME).exists()


def test_t25g_confirm_live_requires_an_explicit_base_url(tmp_path, monkeypatch):
    """--base-url is mandatory for a live run.

    This is a CLI-usage refusal, so the endpoint can never be silently taken from
    the environment without an agreement check.
    """
    approve_env(monkeypatch)
    make_arm(tmp_path, "sonnet46", SONNET_MODEL, monkeypatch)
    assert runner.main(["--out-dir", str(tmp_path / "live3"), "--confirm-live",
                        "--i-have-live-authorization", "--model-alias", "sonnet46"]) == 2
    assert not (tmp_path / "live3").exists()
    # The dry run is unaffected: it never builds a live strategy.
    assert runner.main(["--dry-run", "--out-dir", str(tmp_path / "dry3")]) == 0
    assert (tmp_path / "dry3" / e1.TRIALS_FILENAME).exists()


# ------------------------------------------- endpoint authorisation (E1)

#: Endpoint pinned on arms used by the endpoint-authorisation tests. It deliberately
#: carries a path so path-exactness is actually exercised.
PINNED_ENDPOINT = "https://pinned.invalid/api/v1"

#: Well-formed but unauthorised endpoints. Every entry is a variation of
#: :data:`PINNED_ENDPOINT`, so these prove exact matching rather than merely
#: "a different host".
UNAPPROVED_ENDPOINTS = [
    "https://openrouter.ai/api/v1",              # a different, real host
    "https://192.0.2.7/api/v1",                  # IP literal
    "https://pinned.invalid.evil.example/api/v1",   # suffix confusion
    "https://evil.example/api/v1",               # prefix confusion
    "https://pinned.invalid:8443/api/v1",        # unexpected port
    "https://pinned.invalid/api/v2",             # alternate path
    "https://pinned.invalid/api",                # truncated path
    "https://pinned.invalid/api/v1/admin",       # deeper path
    "https://pinned.invalid/other/v1",           # different path
]

#: Endpoints rejected one layer earlier, by URL validation / normalisation syntax
#: rather than by the allowlist comparison.
MALFORMED_ENDPOINTS = [
    "http://pinned.invalid/api/v1",              # wrong scheme
    "https://pinned.invalid/api/v1?x=1",         # query string
    "https://user:pw@pinned.invalid/api/v1",     # embedded credentials
    "https://pinned.invalid/api/v1#frag",        # fragment
]


@pytest.mark.parametrize("supplied", UNAPPROVED_ENDPOINTS + MALFORMED_ENDPOINTS)
def test_endpoint_authorisation_rejects_unapproved_endpoints(tmp_path, monkeypatch, supplied):
    """Every unapproved endpoint is refused before the ledger or a client exists."""
    arm = make_arm(tmp_path, "sonnet46", SONNET_MODEL, monkeypatch, endpoint=PINNED_ENDPOINT)
    approve_env(monkeypatch, base_url=supplied)
    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.live_strategy_resolvers(strategy_for(arm))
    assert "endpoint_not_approved" in str(excinfo.value)
    # Nothing was opened and nothing was reserved.
    assert read_ledger(arm)["events"] == []
    assert read_ledger(arm)["runs"] == {}


@pytest.mark.parametrize("equivalent", [
    PINNED_ENDPOINT,
    PINNED_ENDPOINT + "/",                       # trailing slash
    "https://pinned.invalid:443/api/v1",         # explicit default port
    "https://PINNED.INVALID/api/v1",             # host case
])
def test_endpoint_authorisation_accepts_exact_and_equivalent_forms(tmp_path, monkeypatch,
                                                                  equivalent):
    """The exact endpoint and its explicitly-intended equivalents are accepted."""
    arm = make_arm(tmp_path, "sonnet46", SONNET_MODEL, monkeypatch, endpoint=PINNED_ENDPOINT)
    approve_env(monkeypatch, base_url=equivalent)
    context = runner.live_strategy_resolvers(strategy_for(arm))
    assert context.arm.alias == "sonnet46"
    assert context.base_url == equivalent.rstrip("/")


def test_endpoint_authorisation_is_bound_to_the_arm_not_the_environment(tmp_path, monkeypatch):
    """A valid environment value is still refused if it is not THIS arm's endpoint."""
    arm = make_arm(tmp_path, "sonnet46", SONNET_MODEL, monkeypatch,
                   endpoint="https://arm-specific.invalid/api/v1")
    approve_env(monkeypatch, base_url="https://example.invalid/api/v1")   # well-formed
    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.live_strategy_resolvers(strategy_for(arm))
    assert "endpoint_not_approved" in str(excinfo.value)


def test_runner_arms_pin_the_approved_endpoint():
    """Both production arms are pinned to the one approved endpoint."""
    assert runner.E1_APPROVED_ENDPOINT == "https://openrouter.ai/api/v1"
    assert {arm.endpoint for arm in runner.APPROVED_E1_ARMS.values()} == {
        runner.E1_APPROVED_ENDPOINT}


def test_client_boundary_refuses_an_endpoint_outside_the_arm_allowlist(tmp_path, monkeypatch):
    """Defence in depth: the E1 client factory refuses independently of the resolver.

    ``build_e1_client`` is the only place E1 constructs a live client, so a caller
    that skips ``live_strategy_resolvers`` still cannot reach the transport with an
    unapproved endpoint.
    """
    from monitors.client import SpendConfigError

    monkeypatch.setenv("APART_API_KEY", "unit-test-placeholder-not-a-real-key")
    arm = runner.E1Arm(alias="sonnet46", provider="openrouter", model=SONNET_MODEL,
                       endpoint=PINNED_ENDPOINT,
                       spend_config=tmp_path / "c.json", ledger_path=tmp_path / "l.json")
    for hostile in UNAPPROVED_ENDPOINTS:
        with pytest.raises(e1.E1LiveBlocked) as excinfo:
            runner.build_e1_client(guard=None, base_url=hostile, arm=arm)
        assert "endpoint_not_approved" in str(excinfo.value), hostile
    for malformed in MALFORMED_ENDPOINTS:
        with pytest.raises((e1.E1LiveBlocked, SpendConfigError)):
            runner.build_e1_client(guard=None, base_url=malformed, arm=arm)


def test_client_boundary_accepts_the_approved_endpoint(tmp_path, monkeypatch):
    """The arm's approved endpoint passes the client-construction boundary."""
    monkeypatch.setenv("APART_API_KEY", "unit-test-placeholder-not-a-real-key")
    arm = runner.E1Arm(alias="sonnet46", provider="openrouter", model=SONNET_MODEL,
                       endpoint=PINNED_ENDPOINT,
                       spend_config=tmp_path / "c.json", ledger_path=tmp_path / "l.json")
    client = runner.build_e1_client(guard=None, base_url=PINNED_ENDPOINT, arm=arm)
    assert client.base_url == PINNED_ENDPOINT
    assert client.max_attempts == 1, "max_attempts=1 is pinned in the E1 factory"
    assert client.base_url == PINNED_ENDPOINT


def test_normalize_endpoint_materialises_default_port_and_case():
    """The normaliser is what makes exact comparison well-defined."""
    assert runner.normalize_endpoint("https://Example.Invalid/api/v1") == \
        "https://example.invalid:443/api/v1"
    assert runner.normalize_endpoint("https://example.invalid:443/api/v1") == \
        runner.normalize_endpoint("https://example.invalid/api/v1")
    # A different port or path is NOT equivalent.
    assert runner.normalize_endpoint("https://example.invalid:8443/api/v1") != \
        runner.normalize_endpoint("https://example.invalid/api/v1")
    assert runner.normalize_endpoint("https://example.invalid/api/v2") != \
        runner.normalize_endpoint("https://example.invalid/api/v1")


# ------------------------------------------------------- single writer (T26)


def test_t26_execution_lease_fails_closed_on_a_second_writer(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    first = runner.ExecutionLease(run_dir, {"run_id": "a"})
    with first:
        assert first.path.exists()
        second = runner.ExecutionLease(run_dir, {"run_id": "b"})
        with pytest.raises(e1.E1LiveBlocked) as excinfo:
            second.acquire()
        assert "execution lease" in str(excinfo.value)
        # The holder is unaffected by the refused second writer.
        assert json.loads(first.path.read_text())["run_id"] == "a"
    assert not first.path.exists()
    with runner.ExecutionLease(run_dir, {"run_id": "c"}):
        pass


def test_t26b_the_lease_disclaims_project_level_single_writer_scope():
    """The lease is a mechanism; it is not an approved project-wide policy."""
    doc = " ".join((runner.ExecutionLease.__doc__ or "").split())
    assert "not a project-wide single-writer policy" in doc
    assert "does not lock the shared spend ledger" in doc
    # E1 never takes the ledger's advisory lock itself: it only mentions it.
    source = (ROOT / "scripts" / "run_experiment_e1.py").read_text(encoding="utf-8")
    assert "import fcntl" not in source
    assert "fcntl.flock" not in source
    assert "LOCK_EX" not in source


# ------------------------------------------------------- protocol details


def test_d16_the_dry_run_responder_is_keyed_by_cell_not_by_message_text(tmp_path):
    responder = runner.CellKeyedFakeResponder()
    plan = e1.build_plan()
    out = tmp_path / "run"
    out.mkdir()
    client = FakeAgentClient(responder=responder, model="fake-e1-receiver")
    outcome = runner.run_plan(plan, client, "e1-d16", e1.EXEC_DRY_RUN_FAKE, out,
                              responder=responder)
    assert outcome.status == "completed"
    # The fake answered B exactly for the two B-target cells and A elsewhere.
    for record in e1.read_jsonl(out / e1.TRIALS_FILENAME):
        expected = "B" if e1.CELL_TARGET[record["cell_id"]] == "B" else "A"
        assert record["parsed_action"] == expected, record["trial_id"]
    assert len(responder.calls) == 80
    # It refuses to answer outside a trial, so it cannot run standalone.
    with pytest.raises(e1.E1InputError):
        runner.CellKeyedFakeResponder().__call__([{"content": "x"}], 1.0, 1, 0)
    with pytest.raises(e1.E1InputError):
        responder.set_trial("c_not_a_cell", "x")


def test_d16b_the_fake_responder_rejects_a_prompt_that_is_not_the_expected_text():
    responder = runner.CellKeyedFakeResponder()
    responder.set_trial(e1.CELL_A_ABSENT, "some other prompt")
    with pytest.raises(e1.E1InputError):
        responder.__call__([{"role": "user", "content": "different"}], 1.0, 1, 0)


def test_d17_one_parse_retry_and_unparseable_replies_are_recorded_not_imputed(tmp_path):
    plan = e1.build_plan()
    out = tmp_path / "run"
    out.mkdir()

    def responder(messages, temperature, seed, index):
        return "not json at all"

    client, outcome = counting_run(plan, out, run_id="e1-retry", responder=responder)
    # One retry per trial: 160 calls for 80 trials, then every trial is invalid.
    assert len(client.calls) == 160
    assert outcome.status == "completed"
    assert outcome.n_failed == 0
    records = e1.read_jsonl(out / e1.TRIALS_FILENAME)
    assert {record["validity"] for record in records} == {"invalid"}
    assert {record["parsed_action"] for record in records} == {None}
    assert {record["attempt"] for record in records} == {2}
    # Nothing is imputed, so the analysis is explicitly not estimable.
    analysis = e1.analyze(records, plan, n_boot=10)
    assert analysis["status"] == e1.STATUS_NOT_ESTIMABLE
    assert analysis["reason"] == e1.REASON_EMPTY_ELIGIBLE_POPULATION


# ============================================================================
# Production live-path wiring (offline: temp configs, temp ledgers, no network)
# ============================================================================

SONNET_MODEL = "anthropic/claude-sonnet-4.6"
GPT_MODEL = "openai/gpt-5.4-mini"


def write_e1_config(path, model, *, phase_cap_usd=2.0, cap_usd=14.0, backstop_usd=37.34,
                    price=(3.0, 15.0)):
    """A minimal valid spend config, in the same shape as configs/spend.json."""
    path.write_text(json.dumps({
        "provider": "openrouter",
        "model": model,
        "allocations": {"pilot_audit": {"cap_usd": cap_usd,
                                        "phase_caps_usd": {"gate": 12.0, "pilot": phase_cap_usd}}},
        "backstop_usd": backstop_usd,
        "stale_after_s": 600,
        "prices": {f"openrouter/{model}": {"input_per_million": price[0],
                                           "output_per_million": price[1]}},
    }, indent=2) + "\n", encoding="utf-8")
    return path


def make_arm(tmp_path, alias, model, monkeypatch, endpoint=TEST_ENDPOINT, **config_kwargs):
    """A temporary arm: temp config + temp ledger created via LedgerStore.init.

    ``endpoint`` defaults to :data:`TEST_ENDPOINT`, which is also ``approve_env``'s
    default, so a temporary arm and its environment agree without touching the real
    approved endpoint.
    """
    from monitors.ledger import LedgerStore

    config = write_e1_config(tmp_path / f"{alias}_spend.json", model, **config_kwargs)
    ledger = tmp_path / alias / "ledger.json"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    LedgerStore(ledger).init(config.read_bytes())
    arm = runner.E1Arm(alias=alias, provider="openrouter", model=model, endpoint=endpoint,
                       spend_config=config, ledger_path=ledger)
    monkeypatch.setitem(runner.APPROVED_E1_ARMS, alias, arm)
    return arm


def approve_env(monkeypatch, base_url=TEST_ENDPOINT):
    """Supply the environment values the live path requires, without a real key.

    ``base_url`` defaults to :data:`TEST_ENDPOINT` so it matches ``make_arm``'s
    temporary arm endpoint; the real approved endpoint is never used in tests.
    """
    monkeypatch.setenv("APART_API_KEY", "unit-test-placeholder-not-a-real-key")
    monkeypatch.setenv("APART_BASE_URL", base_url)


def strategy_for(arm):
    return runner.LiveStrategy(model_alias=arm.alias, spend_config=arm.spend_config,
                               ledger_path=arm.ledger_path, allocation=arm.allocation,
                               phase=arm.phase, base_url="", approved=True)


def read_ledger(arm):
    from monitors.ledger import LedgerStore, Policy, config_sha256
    policy = Policy.from_config(json.loads(arm.spend_config.read_text(encoding="utf-8")))
    return LedgerStore(arm.ledger_path).load(
        expected_config_hash=config_sha256(arm.spend_config.read_bytes()), policy=policy)


def test_e1_token_bound_matches_production():
    """The E1 bound must stay identical to monitors.client._input_token_bound."""
    from monitors.client import _input_token_bound

    plan = e1.build_plan()
    lookup = e1.scenario_index(plan)
    system = {"role": "system", "content": runner.receiver_system(e1.RECEIVER_BRIEF)}
    for scenario_id, cell_id in e1.planned_trials(plan):
        variant = lookup[scenario_id]["variant_index"]
        messages = [system, {"role": "user",
                             "content": e1.receiver_prompt(e1.message_for(cell_id, variant))}]
        assert runner.request_input_token_bound(messages) == _input_token_bound(messages)
    first, retry = runner.e1_max_input_bounds(plan)
    assert first > 0 and retry > first


def test_1_production_resolvers_build_from_temp_config_and_ledger(tmp_path, monkeypatch):
    """Requirement 1: the production chain resolves, validates and opens a run."""
    approve_env(monkeypatch)
    arm = make_arm(tmp_path, "sonnet46", SONNET_MODEL, monkeypatch)

    context = runner.live_strategy_resolvers(strategy_for(arm))
    assert context.provider == "openrouter" and context.model == SONNET_MODEL
    assert context.arm.alias == "sonnet46"
    assert context.resolve_price() == {"provider": "openrouter", "model": SONNET_MODEL,
                                       "input_per_million": "3.0",
                                       "output_per_million": "15.0"}
    # Fresh ledger: the $2.00 pilot phase cap is the binding control.
    assert context.headroom_micro() == 2_000_000
    assert context.worst_case_micro() == runner.worst_case_run_micro(context.policy)
    assert 0 < context.worst_case_micro() <= 2_000_000

    # The mutating step opens the run and builds the pinned client, offline.
    client, swept = context.open_run("e1-unit-run", "0" * 64, "tester")
    assert swept == []
    assert client.guard is not None
    assert client.model == SONNET_MODEL

    ledger = read_ledger(arm)
    header = ledger["runs"]["e1-unit-run"]
    assert header["provenance"]["model"] == f"openrouter/{SONNET_MODEL}"
    assert header["provenance"]["base_url"] == "https://example.invalid"
    assert header["provenance"]["e1_arm"] == "sonnet46"
    assert header["allocation_id"] == "pilot_audit" and header["phase"] == "pilot"
    # No reservation, no dispatch, no settlement happened.
    assert ledger["events"] == []


def test_6_live_client_pins_max_attempts_one(tmp_path, monkeypatch):
    """Requirement 6: each HTTP attempt is a reservation, so the pin matters."""
    approve_env(monkeypatch)
    arm = make_arm(tmp_path, "gpt54mini", GPT_MODEL, monkeypatch, price=(0.75, 4.5))
    context = runner.live_strategy_resolvers(strategy_for(arm))
    client, _swept = context.open_run("e1-pin", "1" * 64, "tester")
    assert client.max_attempts == 1
    assert context.worst_case_micro() == runner.worst_case_run_micro(context.policy)


def test_2_missing_ledger_is_refused_without_implicit_init(tmp_path, monkeypatch):
    """Requirement 2: a missing E1 ledger fails closed; nothing is created."""
    from dataclasses import replace

    approve_env(monkeypatch)
    arm = make_arm(tmp_path, "sonnet46", SONNET_MODEL, monkeypatch)
    absent = replace(arm, ledger_path=tmp_path / "absent" / "ledger.json")
    monkeypatch.setitem(runner.APPROVED_E1_ARMS, arm.alias, absent)

    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.live_strategy_resolvers(strategy_for(absent))
    assert "ledger_missing" in str(excinfo.value)
    assert not (tmp_path / "absent" / "ledger.json").exists()


def test_3_arm_config_mismatch_is_refused(tmp_path, monkeypatch):
    """Requirement 3: a Sonnet arm pointed at the GPT config is refused."""
    from dataclasses import replace

    approve_env(monkeypatch)
    arm = make_arm(tmp_path, "sonnet46", SONNET_MODEL, monkeypatch)
    wrong = write_e1_config(tmp_path / "wrong.json", GPT_MODEL, price=(0.75, 4.5))
    bad = replace(arm, spend_config=wrong)
    monkeypatch.setitem(runner.APPROVED_E1_ARMS, arm.alias, bad)

    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.live_strategy_resolvers(strategy_for(bad))
    assert "arm_config_mismatch" in str(excinfo.value)


def test_3b_ledger_bound_to_another_config_is_refused(tmp_path, monkeypatch):
    """Requirement 3: a ledger bound to a different config hash is refused."""
    from dataclasses import replace

    approve_env(monkeypatch)
    arm = make_arm(tmp_path, "sonnet46", SONNET_MODEL, monkeypatch)
    # Same model, different config bytes: the ledger's recorded binding no longer matches.
    other_config = write_e1_config(tmp_path / "other.json", SONNET_MODEL, cap_usd=13.0)
    bad = replace(arm, spend_config=other_config)
    monkeypatch.setitem(runner.APPROVED_E1_ARMS, arm.alias, bad)

    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.live_strategy_resolvers(strategy_for(bad))
    assert "ledger_binding_mismatch" in str(excinfo.value)


def test_4_canonical_ledger_is_refused(tmp_path, monkeypatch):
    """Requirement 4: E1 may never run against results/spend/ledger.json."""
    from dataclasses import replace

    approve_env(monkeypatch)
    arm = make_arm(tmp_path, "sonnet46", SONNET_MODEL, monkeypatch)
    bad = replace(arm, ledger_path=runner.CANONICAL_LEDGER_PATH)
    monkeypatch.setitem(runner.APPROVED_E1_ARMS, arm.alias, bad)

    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.live_strategy_resolvers(strategy_for(bad))
    # Refused before any read of the canonical ledger.
    assert "canonical_ledger_refused" in str(excinfo.value)


def test_5_allocation_phase_mismatch_is_refused(tmp_path, monkeypatch):
    """Requirement 5: only (pilot_audit, pilot) is approved for E1."""
    from dataclasses import replace

    approve_env(monkeypatch)
    arm = make_arm(tmp_path, "sonnet46", SONNET_MODEL, monkeypatch)
    strategy = replace(strategy_for(arm), allocation="sprint", phase="extension")
    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.live_strategy_resolvers(strategy)
    assert "allocation_phase_not_approved" in str(excinfo.value)


def test_5b_reconfigured_pair_cap_is_refused(tmp_path, monkeypatch):
    """Requirement 5: a config whose pilot cap is not $2.00 is refused."""
    approve_env(monkeypatch)
    arm = make_arm(tmp_path, "sonnet46", SONNET_MODEL, monkeypatch, phase_cap_usd=5.0)
    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.live_strategy_resolvers(strategy_for(arm))
    assert "pair_cap_changed" in str(excinfo.value)


def test_5c_missing_environment_values_are_refused(tmp_path, monkeypatch):
    """A missing key or endpoint fails closed before the ledger is touched."""
    approve_env(monkeypatch)
    arm = make_arm(tmp_path, "sonnet46", SONNET_MODEL, monkeypatch)

    monkeypatch.delenv("APART_API_KEY", raising=False)
    monkeypatch.delenv("APART_BASE_URL", raising=False)
    # Neutralise any local .env so the absence is unambiguous.
    monkeypatch.setattr(runner, "ROOT", tmp_path / "no-such-root")
    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.live_strategy_resolvers(strategy_for(arm))
    assert "api_key_missing" in str(excinfo.value)

    monkeypatch.setenv("APART_API_KEY", "unit-test-placeholder-not-a-real-key")
    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.live_strategy_resolvers(strategy_for(arm))
    assert "base_url_missing" in str(excinfo.value)


# ------------------------------------------- aggregate budget, lock, pricing

#: Global audit-event sink; cleared by the test that uses it.
_NETWORK_EVENTS: list[str] = []


def _record_network_event(event: str, args: tuple) -> None:
    if event.startswith(("socket.", "urllib.", "http.client.")):
        _NETWORK_EVENTS.append(event)


sys.addaudithook(_record_network_event)


def _policy_for(arm):
    from monitors.ledger import Policy
    return Policy.from_config(json.loads(arm.spend_config.read_text(encoding="utf-8")))


def test_approved_caps_match_the_shipped_configs():
    """The approved E1 numbers must equal what the shipped configs actually say."""
    assert runner.E1_APPROVED_PAIR_CAP_MICRO == 2_000_000
    assert runner.E1_AGGREGATE_CEILING_MICRO == 4_000_000
    for alias, arm in runner.APPROVED_E1_ARMS.items():
        policy = _policy_for(arm)
        assert policy.pair_cap("pilot_audit", "pilot") == runner.E1_APPROVED_PAIR_CAP_MICRO, alias
        assert policy.require_model() == (arm.provider, arm.model), alias


def test_7_live_wiring_makes_no_provider_request_or_socket(tmp_path, monkeypatch):
    """Requirement 7: resolve -> preflight -> aggregate -> open_run stays offline."""
    _NETWORK_EVENTS.clear()
    approve_env(monkeypatch)
    arm = make_arm(tmp_path, "sonnet46", SONNET_MODEL, monkeypatch)

    context = runner.live_strategy_resolvers(strategy_for(arm))
    preflight = runner.preflight_live(context.preflight_strategy,
                                      resolve_price=context.resolve_price,
                                      headroom_micro=context.headroom_micro,
                                      worst_case_micro=context.worst_case_micro)
    assert preflight["approved"] is True
    aggregate = runner.aggregate_budget_preflight(
        worst_case_micro=context.worst_case_micro(), approved={arm.alias: arm},
        ledger_root=tmp_path)
    assert aggregate["projected_micro"] <= runner.E1_AGGREGATE_CEILING_MICRO
    client, _swept = context.open_run("e1-offline", "3" * 64, "tester")
    assert client.guard is not None

    assert _NETWORK_EVENTS == [], f"unexpected network audit events: {_NETWORK_EVENTS}"


def test_8_aggregate_ceiling_blocks_insufficient_budget(tmp_path, monkeypatch):
    """Requirement 8: committed + worst case above the ceiling is refused."""
    approve_env(monkeypatch)
    arm = make_arm(tmp_path, "sonnet46", SONNET_MODEL, monkeypatch)
    worst = runner.worst_case_run_micro(_policy_for(arm))

    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.aggregate_budget_preflight(worst_case_micro=worst, ceiling_micro=worst - 1,
                                          approved={arm.alias: arm}, ledger_root=tmp_path)
    assert "aggregate_budget_insufficient" in str(excinfo.value)

    ok = runner.aggregate_budget_preflight(worst_case_micro=worst, ceiling_micro=worst,
                                           approved={arm.alias: arm}, ledger_root=tmp_path)
    assert ok["projected_micro"] == worst
    assert ok["atomic_across_ledgers"] is False
    assert "Not an atomic cross-ledger budget controller" in ok["note"]


def test_9_aggregate_counts_both_ledgers_and_all_outstanding_reservations(tmp_path, monkeypatch):
    """Requirement 9: both approved ledgers are summed; reservations stay counted.

    Operates on TEMPORARY fixtures only (temp configs + temp ledgers created with
    ``LedgerStore.init`` under ``tmp_path``). The real ledgers, the shared ledger
    and the provider are never touched.
    """
    from monitors.ledger import (LedgerStore, Policy, commitments, config_sha256,
                                 make_run_header, open_run, reserve)

    approve_env(monkeypatch)
    sonnet = make_arm(tmp_path, "sonnet46", SONNET_MODEL, monkeypatch)
    gpt = make_arm(tmp_path, "gpt54mini", GPT_MODEL, monkeypatch, price=(0.75, 4.5))
    approved = {"sonnet46": sonnet, "gpt54mini": gpt}

    empty = runner.aggregate_budget_preflight(worst_case_micro=0, approved=approved,
                                              ledger_root=tmp_path)
    assert empty["committed_by_arm_micro"] == {"sonnet46": 0, "gpt54mini": 0}
    assert set(empty["committed_by_arm_micro"]) == set(approved)

    config_bytes = sonnet.spend_config.read_bytes()
    digest = config_sha256(config_bytes)
    policy = Policy.from_config(json.loads(config_bytes.decode("utf-8")))
    store = LedgerStore(sonnet.ledger_path)
    open_run(store, policy, make_run_header(
        "e1-t9", "pilot_audit", "pilot", digest, {"plan_sha256": "x"},
        "2026-01-01T00:00:00Z", provenance={"model": f"openrouter/{SONNET_MODEL}"}),
        digest, "tester")
    reserve(store, policy, digest, "e1-t9", "call-1", 0, f"openrouter/{SONNET_MODEL}", 5_307)

    after = runner.aggregate_budget_preflight(worst_case_micro=7_000, approved=approved,
                                              ledger_root=tmp_path)
    assert after["committed_by_arm_micro"]["sonnet46"] == 5_307
    assert after["committed_by_arm_micro"]["gpt54mini"] == 0
    assert after["committed_micro"] == 5_307
    assert after["projected_micro"] == 5_307 + 7_000
    # Counted once, at its worst case, and not released.
    ledger = read_ledger(sonnet)
    assert commitments(ledger)["total"] == 5_307
    assert ledger["events"][0]["state"] == "reserved"


def test_10_unapproved_third_ledger_or_path_is_refused(tmp_path, monkeypatch):
    """Requirement 10: a third E1 ledger, or an unapproved path, is refused."""
    from dataclasses import replace
    from monitors.ledger import LedgerStore

    approve_env(monkeypatch)
    sonnet = make_arm(tmp_path, "sonnet46", SONNET_MODEL, monkeypatch)
    gpt = make_arm(tmp_path, "gpt54mini", GPT_MODEL, monkeypatch, price=(0.75, 4.5))
    approved = {"sonnet46": sonnet, "gpt54mini": gpt}

    rogue = tmp_path / "rogue" / "ledger.json"
    rogue.parent.mkdir(parents=True, exist_ok=True)
    LedgerStore(rogue).init(sonnet.spend_config.read_bytes())

    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.aggregate_budget_preflight(worst_case_micro=0, approved=approved,
                                          ledger_root=tmp_path)
    assert "unapproved_e1_ledger_present" in str(excinfo.value)

    strategy = replace(strategy_for(sonnet), ledger_path=tmp_path / "elsewhere.json")
    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.live_strategy_resolvers(strategy)
    assert "ledger_path_not_approved" in str(excinfo.value)


def test_10b_missing_approved_ledger_blocks_the_aggregate_check(tmp_path, monkeypatch):
    """An absent approved ledger means the aggregate cannot be established."""
    from dataclasses import replace

    approve_env(monkeypatch)
    sonnet = make_arm(tmp_path, "sonnet46", SONNET_MODEL, monkeypatch)
    absent = replace(sonnet, ledger_path=tmp_path / "nope" / "ledger.json")
    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.aggregate_budget_preflight(worst_case_micro=0,
                                          approved={"sonnet46": absent},
                                          ledger_root=tmp_path / "empty-root")
    assert "approved_e1_ledger_missing" in str(excinfo.value)


def test_11_12_global_lock_blocks_a_second_run_across_dirs_and_models(tmp_path):
    """Requirements 11-12: one E1 spend-bearing run at a time, project-wide."""
    lock_path = tmp_path / "e1_run.lock"
    first = runner.E1ProjectLock(lock_path, {"run_id": "e1-first",
                                            "model_alias": "sonnet46", "pid": os.getpid()})
    with first:
        assert lock_path.exists()
        assert json.loads(lock_path.read_text())["run_id"] == "e1-first"
        # A different OUTPUT DIRECTORY and a different MODEL are both refused.
        for other in ({"run_id": "e1-other", "model_alias": "sonnet46"},
                      {"run_id": "e1-other", "model_alias": "gpt54mini"}):
            with pytest.raises(e1.E1LiveBlocked) as excinfo:
                runner.E1ProjectLock(lock_path, other).acquire()
            assert "e1_run_lock_held" in str(excinfo.value)
            assert "NEVER removed automatically" in str(excinfo.value)
        info = runner.describe_lock(lock_path)
        assert info["present"] is True
        assert info["holder"]["run_id"] == "e1-first"
        assert info["pid_alive"] is True
    assert not lock_path.exists()
    with runner.E1ProjectLock(lock_path, {"run_id": "e1-later"}):
        pass


def test_12_cli_live_run_is_blocked_before_preflight_while_the_lock_is_held(tmp_path, monkeypatch):
    """Requirement 12: the lock is taken before preflight and before any output."""
    approve_env(monkeypatch)
    holder = runner.E1ProjectLock(runner.E1_GLOBAL_LOCK_PATH,
                                  {"run_id": "e1-holder", "model_alias": "sonnet46"})
    holder.acquire()
    try:
        out = tmp_path / "second-run"
        code = runner.main(["--out-dir", str(out), "--confirm-live",
                            "--i-have-live-authorization", "--model-alias", "gpt54mini",
                            "--allocation", "pilot_audit", "--phase", "pilot",
                            "--base-url", "https://example.invalid"])
        assert code == 5
        # Blocked before the output directory was even created.
        assert not out.exists()
    finally:
        holder.release()


def test_13_stale_lock_is_never_removed_automatically(tmp_path):
    """Requirement 13: stale-lock recovery is deliberate and manual; never automatic."""
    lock_path = tmp_path / "e1_run.lock"
    stale = runner.E1ProjectLock(lock_path, {"run_id": "e1-dead", "pid": 2 ** 22})
    stale.acquire()
    stale._held = False          # simulate a process that died without running __exit__
    assert lock_path.exists()

    with pytest.raises(e1.E1LiveBlocked) as excinfo:
        runner.E1ProjectLock(lock_path, {"run_id": "e1-next"}).acquire()
    assert "e1_run_lock_held" in str(excinfo.value)
    assert "NEVER removed automatically" in str(excinfo.value)
    # The lock file survives the refusal: nothing expires, no --force exists.
    assert lock_path.exists()

    info = runner.describe_lock(lock_path)
    assert info["holder"]["run_id"] == "e1-dead"
    assert info["pid_alive"] is not True

    # Recovery: deliberate manual deletion, then the lock can be taken.
    lock_path.unlink()
    with runner.E1ProjectLock(lock_path, {"run_id": "e1-recovered"}):
        pass
    assert not lock_path.exists()


def test_14_unmapped_served_model_aborts_further_dispatch(tmp_path, monkeypatch):
    """Requirement 14: an unpriced served model stops the run at once."""
    approve_env(monkeypatch)
    arm = make_arm(tmp_path, "sonnet46", SONNET_MODEL, monkeypatch)
    policy = _policy_for(arm)
    plan = e1.build_plan()

    def run_once(out_dir, served_model):
        client = FakeAgentClient(responder=lambda *_: json.dumps({"action": "A", "note": ""}),
                                 model=SONNET_MODEL)
        original = client.call_model

        def call_model(messages, temperature=1.0, seed=None, max_tokens=150, call_id=None):
            response = original(messages, temperature, seed, max_tokens, call_id)
            response.served_model = served_model
            response.served_provider = "openrouter"
            return response

        monkeypatch.setattr(client, "call_model", call_model)
        outcome = runner.run_plan(plan, client, "e1-pricing", e1.EXEC_LIVE, out_dir,
                                  price_policy=policy)
        return client, outcome

    # 14a. An unpriced served model stops the run after exactly one dispatch.
    unpriced = tmp_path / "unpriced"
    unpriced.mkdir()
    client, outcome = run_once(unpriced, "some/undocumented-snapshot-2026-01")
    assert outcome.status == "failed"
    assert outcome.stop_reason == runner.FATAL_SERVED_MODEL_UNPRICED
    assert outcome.n_attempted == 1
    assert len(client.calls) == 1, "no further dispatch after an unpriced served model"
    records = e1.read_jsonl(unpriced / e1.TRIALS_FILENAME)
    assert len(records) == 80
    statuses = Counter(record["execution_status"] for record in records)
    assert statuses[e1.EXEC_FAILED] == 1
    assert statuses[e1.EXEC_NOT_ATTEMPTED] == 79
    failed = [r for r in records if r["execution_status"] == e1.EXEC_FAILED][0]
    assert failed["error"] == runner.FATAL_SERVED_MODEL_UNPRICED
    assert failed["error_stage"] == "receiver"
    assert failed["served_model"] == "some/undocumented-snapshot-2026-01"
    assert failed["validity"] is None and failed["parsed_action"] is None
    # The unpriced reply is never recorded as a successful settlement.
    assert "no configured price" in outcome.error

    # 14b. Control: the configured served model runs to completion.
    priced = tmp_path / "priced"
    priced.mkdir()
    client, outcome = run_once(priced, SONNET_MODEL)
    assert outcome.status == "completed"
    assert len(client.calls) == 80


def test_served_price_resolution_matches_the_ledger_policy():
    """resolve_served_price must agree with Policy.cost_micro on the same inputs."""
    from monitors.ledger import Policy

    policy = Policy.from_config({
        "provider": "openrouter", "model": "anthropic/claude-sonnet-4.6",
        "allocations": {"pilot_audit": {"cap_usd": 1.0, "phase_caps_usd": {"pilot": 1.0}}},
        "backstop_usd": 1.0, "stale_after_s": 600,
        "prices": {"openrouter/anthropic/claude-sonnet-4.6":
                   {"input_per_million": 3.0, "output_per_million": 15.0}},
    })
    usage = {"prompt_tokens": 10, "completion_tokens": 10}
    cases = ((None, None),
             ("anthropic/claude-sonnet-4.6", "openrouter"),
             ("some/other-model", "openrouter"),
             ("anthropic/claude-sonnet-4.6", "unlisted-provider"))
    for served_model, served_provider in cases:
        resolved = runner.resolve_served_price(policy, served_model, served_provider)
        priced = policy.cost_micro(usage, served_model, served_provider) is not None
        assert (resolved is not None) is priced, (served_model, served_provider)


# ---------------------------------------------------- Phase A isolation (T30)


#: SHA-256 of the frozen Phase A sources, captured at E1 implementation time.
#: A mismatch means the frozen Phase A implementation was edited by this work.
FROZEN_PHASE_A_SHA256 = {
    "src/apart/phase_a_live.py":
        "409dbda7c3b55035e0dded228046532887faca696bbfcbe1050f3c222514e893",
    "src/apart/phase_a_to_trace.py":
        "7d05db9da292b2dfa0cdd24f850562e41ff5fe277956aec705d6888919b181f6",
    "src/apart/phase_a_conditions.py":
        "959f5022770ed6fe6c76734d515ed77f051597ddbca11f8bac0b1d7cfa84a983",
    "src/apart/phase_a_models.py":
        "403f1bc698ef57e9ccd0053f23464380bc8ed66a1c8322f99cfeeb31e940d346",
    "src/apart/phase_a_runner.py":
        "54477e72a9ec90cc3b632f52d6dcc44e359ec071f8d820ba07056d5fb1d23349",
    "src/apart/phase_a_stats.py":
        "91febf15fe250766a8cd0c3ee8588e8139fa529dc6e143b33317b74fb206d016",
    "src/apart/phase_a_parser.py":
        "3eafe3b6eaccc56b52745d8e74251663c668a0ee818dcc32737965bf7ac3278e",
}

E1_MODULES = ("src/apart/experiment_e1.py", "src/apart/experiment_e1_adapter.py",
              "scripts/run_experiment_e1.py")


def test_t30_frozen_phase_a_sources_are_byte_identical():
    for relative, expected in FROZEN_PHASE_A_SHA256.items():
        digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        assert digest == expected, f"{relative} was modified by the E1 work"


def test_t30b_no_phase_a_module_is_imported_by_e1():
    for relative in E1_MODULES:
        source = (ROOT / relative).read_text(encoding="utf-8")
        for module in ("phase_a_live", "phase_a_to_trace", "phase_a_conditions",
                       "phase_a_models", "phase_a_runner", "phase_a_stats",
                       "phase_a_parser"):
            assert f"from apart.{module} import" not in source, (relative, module)
            assert f"import apart.{module}" not in source, (relative, module)


def test_t30c_no_e1_identifier_appears_in_the_frozen_phase_a_sources():
    for relative in FROZEN_PHASE_A_SHA256:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert e1.PLAN_VERSION not in source, relative
        assert str(e1.PLAN_SEED) not in source, relative
        for cell_id in e1.CELLS:
            assert cell_id not in source, (relative, cell_id)


def test_t30d_e1_does_not_read_or_write_phase_a_or_phase_b_run_directories(tmp_path):
    """A dry run writes only inside its own directory and never globs runs/ or results/."""
    out = tmp_path / "run"
    assert runner.main(["--out-dir", str(out), "--dry-run"]) == 0
    for relative in E1_MODULES:
        source = (ROOT / relative).read_text(encoding="utf-8")
        for forbidden in ("phase-a-live", "phaseb_verification", "supplementary_phase_b_audit"):
            assert forbidden not in source, (relative, forbidden)
