"""Offline tests for the generation-probe live runner (``scripts/run_generation_probe.py``).

No network, no credentials, no real ledger file, no spend. The client is the **real**
``ModelClient`` bound to the **real** ``LedgerGuard`` over a **real** ``LedgerStore`` in
``tmp_path``; only ``ModelClient._post_json`` - the HTTP transport - is replaced. That way
reservation, dispatch, settlement, unknown-cost handling and provenance all run for real.

``client_factory`` is the runner's own injection seam. Production never passes it.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_generation_probe as cli  # noqa: E402

import monitors.client as monitors_client  # noqa: E402
from agents.runner import STAGE_CONTEXT  # noqa: E402
from apart import brief_registry as br  # noqa: E402
from apart import probe_protocol  # noqa: E402
from monitors.client import ModelClient  # noqa: E402

REAL_RUN_DIR = ROOT / "experiments" / "generation_probe_obj10_v1"
BASE_URL = "https://openrouter.ai/api/v1"
MODEL = "anthropic/claude-sonnet-4.6"
TEST_KEY = "sk-test-not-a-real-key"
USAGE = {"prompt_tokens": 100, "completion_tokens": 20}

#: The protocol's declared audit blocks - a fixed constant, not a live tree scan - so
#: these fixtures cannot be broken by whatever runs happen to exist on disk.
CONTROLLED_SEED_INVENTORY = tuple(probe_protocol.AUDIT_SEED_BLOCKS)


@pytest.fixture(autouse=True, scope="module")
def controlled_seed_inventory():
    """Isolate the runner fixtures from the live seed inventory.

    ``scripts/run_generation_probe.py`` validates the manifest's seed block against
    ``protected_seed_blocks()`` by default - the correct production behaviour. These
    tests drive the runner with the *real* manifest (seeds 7000-7034), which the
    completed pilot has since consumed, so the inventory is patched to an explicit
    controlled constant here. Production is untouched.
    """
    patch = pytest.MonkeyPatch()
    patch.setattr(probe_protocol, "protected_seed_blocks",
                  lambda repo_root=None: list(CONTROLLED_SEED_INVENTORY))
    yield CONTROLLED_SEED_INVENTORY
    patch.undo()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any real socket use is a test failure. Existing guards are not weakened."""
    def refuse(*_args, **_kwargs):
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.setattr("socket.socket.connect", refuse)
    monkeypatch.setattr("socket.create_connection", refuse)


# --------------------------------------------------------------------- harness

def fixture_run_dir(tmp_path: Path, *, manifest=None, policy=None) -> Path:
    """An isolated run directory holding the real manifest and policy."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = manifest if manifest is not None else json.loads(
        (REAL_RUN_DIR / "manifest.json").read_text())
    policy = policy if policy is not None else json.loads(
        (REAL_RUN_DIR / "policy.json").read_text())
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (run_dir / "policy.json").write_text(json.dumps(policy, indent=2))
    return run_dir


def ok_body(text: str, *, model=MODEL, usage=USAGE, provider=None) -> dict:
    body = {"id": "resp-1", "model": model,
            "choices": [{"message": {"content": text}}]}
    if usage is not None:
        body["usage"] = usage
    if provider is not None:
        body["provider"] = provider
    return body


def stage_reply(payload: dict, _index: int) -> dict:
    """Stage-appropriate JSON, inferred from the prompt the stage sent."""
    user = payload["messages"][-1]["content"]
    if '"message"' in user:
        return ok_body(json.dumps({"message": "m"}))
    if '"plan' in user:
        return ok_body(json.dumps({"plan": "A", "note": ""}))
    return ok_body(json.dumps({"action": "A"}))


def factory(responder=stage_reply, sent=None, *, max_attempts=1, provider_order=None,
            real=True, guard_binding=True):
    """Build a ``client_factory``. ``real=False`` returns a non-guard stub."""
    sent = sent if sent is not None else []

    def build(guard):
        if not real:
            from types import SimpleNamespace
            return SimpleNamespace(max_attempts=max_attempts,
                                   provider_order=list(provider_order or []),
                                   guard=guard if guard_binding else None)
        client = ModelClient(guard=guard, api_key=TEST_KEY, base_url=BASE_URL,
                             load_env=False, sleep=lambda _s: None,
                             max_attempts=max_attempts, provider_order=provider_order)

        def post(payload):
            sent.append(payload)
            return responder(payload, len(sent))

        client._post_json = post
        return client

    build.sent = sent
    return build


@pytest.fixture(scope="module")
def full_run(tmp_path_factory):
    """One complete simulated 35-trace run, reused read-only across tests."""
    tmp = tmp_path_factory.mktemp("fullrun")
    run_dir = fixture_run_dir(tmp)
    out_dir = tmp / "out"
    sent: list = []
    code = cli.live(run_dir, out_dir, True, client_factory=factory(sent=sent))
    summary = json.loads((out_dir / "probe_summary.json").read_text())
    rows = [json.loads(line) for line
            in (out_dir / "probe_traces.jsonl").read_text().splitlines()]
    ledger = json.loads((run_dir / "spend" / "ledger.json").read_text())
    provenance = [json.loads(line) for line
                  in (run_dir / "provenance" / "calls.jsonl").read_text().splitlines()]
    raw = [json.loads(line) for line
           in (run_dir / "raw" / "replies.jsonl").read_text().splitlines()]
    return {"code": code, "summary": summary, "rows": rows, "ledger": ledger,
            "sent": sent, "run_dir": run_dir, "out_dir": out_dir,
            "provenance": provenance, "raw": raw}


# ------------------------------------------------------------- complete run

class TestCompleteSimulatedRun:
    def test_runner_exits_ok(self, full_run):
        assert full_run["code"] == cli.EXIT_OK

    def test_35_traces_and_175_calls(self, full_run):
        assert full_run["summary"]["attempted_traces"] == 35
        assert full_run["summary"]["planned_traces"] == 35
        assert full_run["summary"]["not_attempted"] == 0
        assert len(full_run["sent"]) == 175
        calls = STAGE_CONTEXT
        assert len(calls) == 5
        assert 35 * len(calls) == 175

    def test_seven_variants_five_traces_each(self, full_run):
        counts = full_run["summary"]["descriptive_counts_by_cell"]
        assert set(counts) == {f"{v}/FREE_MSG" for v in
                               ("V0", "V1", "V2", "V3", "V5", "V6", "V7")}
        for cell, outcomes in counts.items():
            assert sum(outcomes.values()) == 5, cell

    def test_variant_and_seed_mapping_is_round_robin(self, full_run):
        """Seeds are consecutive from 7000; cells cycle in manifest order."""
        variants = ("V0", "V1", "V2", "V3", "V5", "V6", "V7")
        rows = full_run["rows"]
        assert len(rows) == 35
        assert [r["seed"] for r in rows] == list(range(7000, 7035))
        for index, row in enumerate(rows):
            assert row["variant"] == variants[index % 7], index
            assert row["condition"] == "FREE_MSG", index
            assert row["trace_id"] == f"ext-{7000 + index:04d}"
        # every cell gets exactly five traces
        for variant in variants:
            assert sum(1 for r in rows if r["variant"] == variant) == 5

    def test_plan_matches_probe_plan_exactly(self, full_run):
        from apart.behavior_probe import probe_plan
        manifest = json.loads((full_run["run_dir"] / "manifest.json").read_text())
        expected = [(c.seed, c.variant, c.condition) for c in probe_plan(manifest)]
        actual = [(r["seed"], r["variant"], r["condition"]) for r in full_run["rows"]]
        assert actual == expected

    def test_no_monitors_extraction_labeler_or_confirmatory(self, full_run):
        assert full_run["summary"]["generation_only"] == {
            "extraction": False, "labeler": False, "monitor_arms": [],
            "confirmatory": False}
        assert full_run["summary"]["counts_are_descriptive_only"] is True

    def test_gate_passed_and_run_was_not_stopped(self, full_run):
        assert full_run["summary"]["gate"]["passed"] is True
        assert full_run["summary"]["stopped"] is None
        assert full_run["summary"]["gate"]["behavioural_criteria_used"] is False



# ----------------------------------------------------------- destination safety

class TestDestinationSafety:
    def test_refuses_an_existing_output_directory(self, tmp_path):
        run_dir = fixture_run_dir(tmp_path)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "keep.txt").write_text("do not clobber")
        code = cli.live(run_dir, out_dir, True, client_factory=factory())
        assert code == cli.EXIT_CONFIG
        assert (out_dir / "keep.txt").read_text() == "do not clobber"
        assert not (run_dir / "spend" / "ledger.json").exists()

    def test_refuses_a_protected_directory(self, tmp_path):
        run_dir = fixture_run_dir(tmp_path)
        code = cli.live(run_dir, ROOT / "data" / "nope", True, client_factory=factory())
        assert code == cli.EXIT_CONFIG

    def test_refuses_an_existing_run_directory(self, tmp_path):
        """runs/ext-probe-v21-sonnet46 must be untouchable."""
        run_dir = fixture_run_dir(tmp_path)
        target = ROOT / "runs" / "ext-probe-v21-sonnet46" / "new-subdir"
        code = cli.live(run_dir, target, True, client_factory=factory())
        assert code == cli.EXIT_CONFIG
        assert not target.exists()

    def test_second_run_into_the_same_directory_is_refused(self, tmp_path):
        run_dir = fixture_run_dir(tmp_path)
        out_dir = tmp_path / "out"
        assert cli.live(run_dir, out_dir, True, client_factory=factory()) == cli.EXIT_OK
        before = (out_dir / "probe_summary.json").read_bytes()
        # second attempt: output dir exists AND the run is already open in the ledger
        assert cli.live(run_dir, out_dir, True, client_factory=factory()) == cli.EXIT_CONFIG
        assert (out_dir / "probe_summary.json").read_bytes() == before

    def test_cli_requires_confirm_live_and_out_dir(self, tmp_path):
        run_dir = fixture_run_dir(tmp_path)
        assert cli.main(["live", "--run-dir", str(run_dir)]) == cli.EXIT_CONFIG
        assert cli.main(["live", "--run-dir", str(run_dir), "--out-dir",
                         str(tmp_path / "o")]) == cli.EXIT_CONFIG
        assert not (tmp_path / "o").exists()

    def test_live_refuses_when_provenance_already_exists(self, tmp_path):
        run_dir = fixture_run_dir(tmp_path)
        (run_dir / "provenance").mkdir()
        (run_dir / "provenance" / "calls.jsonl").write_text("")
        code = cli.live(run_dir, tmp_path / "out", True, client_factory=factory())
        assert code == cli.EXIT_CONFIG
        assert not (run_dir / "spend" / "ledger.json").exists()


# ------------------------------------------------------------ config fails closed

def _mutated(tmp_path, **changes):
    manifest = json.loads((REAL_RUN_DIR / "manifest.json").read_text())
    manifest.update(changes)
    return fixture_run_dir(tmp_path, manifest=manifest)


class TestConfigurationFailsClosed:
    def test_unknown_brief_version(self, tmp_path):
        manifest = json.loads((REAL_RUN_DIR / "manifest.json").read_text())
        manifest["brief_versions"] = {"version": "9.9", "status": "x"}
        run_dir = fixture_run_dir(tmp_path, manifest=manifest)
        assert cli.live(run_dir, tmp_path / "out", True,
                        client_factory=factory()) == cli.EXIT_CONFIG
        assert not (run_dir / "spend" / "ledger.json").exists()

    def test_missing_brief_version(self, tmp_path):
        manifest = json.loads((REAL_RUN_DIR / "manifest.json").read_text())
        manifest.pop("brief_versions")
        run_dir = fixture_run_dir(tmp_path, manifest=manifest)
        assert cli.live(run_dir, tmp_path / "out", True,
                        client_factory=factory()) == cli.EXIT_CONFIG

    def test_cap_mismatch_between_manifest_and_policy(self, tmp_path):
        run_dir = _mutated(tmp_path, cap_usd=3.0)
        assert cli.live(run_dir, tmp_path / "out", True,
                        client_factory=factory()) == cli.EXIT_CONFIG
        assert not (run_dir / "spend" / "ledger.json").exists()

    def test_changed_pinned_file(self, tmp_path):
        manifest = json.loads((REAL_RUN_DIR / "manifest.json").read_text())
        manifest["prompt_files_sha256"]["agents/prompts.py"] = "0" * 64
        run_dir = fixture_run_dir(tmp_path, manifest=manifest)
        assert cli.live(run_dir, tmp_path / "out", True,
                        client_factory=factory()) == cli.EXIT_CONFIG

    def test_overlapping_seed_block(self, tmp_path):
        run_dir = _mutated(tmp_path, seed_start=6000)
        assert cli.live(run_dir, tmp_path / "out", True,
                        client_factory=factory()) == cli.EXIT_CONFIG

    def test_unsupported_protocol_version(self, tmp_path):
        run_dir = _mutated(tmp_path, protocol_version="apart-generation-probe/9.9")
        assert cli.live(run_dir, tmp_path / "out", True,
                        client_factory=factory()) == cli.EXIT_CONFIG

    def test_missing_manifest_file(self, tmp_path):
        run_dir = tmp_path / "empty"
        run_dir.mkdir()
        (run_dir / "policy.json").write_text(
            (REAL_RUN_DIR / "policy.json").read_text())
        assert cli.live(run_dir, tmp_path / "out", True,
                        client_factory=factory()) == cli.EXIT_CONFIG

    def test_check_command_reports_config_errors(self, tmp_path):
        run_dir = _mutated(tmp_path, cap_usd=99.0)
        assert cli.check(run_dir) == cli.EXIT_CONFIG


# ---------------------------------------------------------------- credentials

class TestCredentialHandling:
    def test_missing_credential_fails_closed_before_opening_a_run(
            self, tmp_path, monkeypatch):
        """Absent key must not leave a half-opened ledger behind."""
        monkeypatch.setenv("APART_API_KEY", "")
        run_dir = fixture_run_dir(tmp_path)
        code = cli.live(run_dir, tmp_path / "out", True, client_factory=None)
        assert code == cli.EXIT_CONFIG
        assert not (run_dir / "spend" / "ledger.json").exists()
        assert not (run_dir / "provenance").exists()
        assert not (tmp_path / "out").exists()

    def test_missing_credential_does_not_disclose_a_value(
            self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("APART_API_KEY", "")
        run_dir = fixture_run_dir(tmp_path)
        cli.live(run_dir, tmp_path / "out", True, client_factory=None)
        captured = capsys.readouterr()
        assert "APART_API_KEY" in captured.err
        assert "sk-" not in captured.err
        assert "sk-" not in captured.out

    def test_client_without_a_guard_cannot_send(self):
        """No path bypasses the guard: an unguarded client refuses to dispatch."""
        client = ModelClient(api_key=TEST_KEY, base_url=BASE_URL, load_env=False)
        with pytest.raises(monitors_client.SpendConfigError):
            client.call_model([{"role": "user", "content": "x"}], temperature=1.0,
                              call_id="ext-7000:s1:p1")

    def test_legacy_spend_guard_is_refused(self):
        """The superseded guard type cannot be used to authorize requests."""
        with pytest.raises(monitors_client.SpendConfigError):
            ModelClient(guard=object(), api_key=TEST_KEY, load_env=False)




# ------------------------------------------------------- prompt delivery proof

class TestPromptDeliveryVerification:
    """The delivered system prompt is compared byte-for-byte, per call, per role."""

    @staticmethod
    def _pairs(full_run):
        """(call_id, delivered system prompt) in dispatch order."""
        provenance = full_run["provenance"]
        sent = full_run["sent"]
        assert len(provenance) == len(sent) == 175
        return [(rec["call_id"], payload["messages"][0]["content"])
                for rec, payload in zip(provenance, sent)]

    def test_every_call_delivered_the_version_implied_prompt(self, full_run):
        manifest = json.loads((full_run["run_dir"] / "manifest.json").read_text())
        plan = {f"ext-{c.seed:04d}": c.variant
                for c in __import__("apart.behavior_probe", fromlist=["probe_plan"])
                .probe_plan(manifest)}
        seen_stages = set()
        for call_id, system_prompt in self._pairs(full_run):
            trace_id, stage = call_id.split(":")[0], call_id.split(":")[1]
            seen_stages.add(stage)
            role = "receiver" if stage.startswith("r") else "sender"
            variant = plan[trace_id]
            expected = br.compose_system_prompt(
                br.resolve_brief(variant, role, br.REVISED_BRIEF_VERSION))
            assert system_prompt == expected, f"{call_id} prompt mismatch"
        assert seen_stages == {"s1", "r1", "s2", "s3", "r2"}

    def test_v2_both_roles_carry_the_revised_text(self, full_run):
        revised = br.compose_system_prompt(
            br.resolve_brief("V2", "sender", br.REVISED_BRIEF_VERSION))
        payloads = [p for cid, p in self._pairs(full_run) if cid.startswith("ext-7002")
                    or cid.startswith("ext-7009")
                    or cid.startswith("ext-7016")
                    or cid.startswith("ext-7023")
                    or cid.startswith("ext-7030")]
        assert len(payloads) == 25          # five V2 traces x five stages
        assert all(p == revised for p in payloads)
        assert "follow the policy" not in revised

    def test_v5_sender_revised_and_receiver_is_g_base(self, full_run):
        """V5: s1/s2/s3 are sender stages (revised); r1/r2 are receiver (G-BASE)."""
        sender = br.compose_system_prompt(
            br.resolve_brief("V5", "sender", br.REVISED_BRIEF_VERSION))
        receiver = br.compose_system_prompt(
            br.resolve_brief("V5", "receiver", br.BASE_BRIEF_VERSION))
        assert sender != receiver
        seen_roles = set()
        for call_id, prompt in self._pairs(full_run):
            trace, stage = call_id.split(":")[0], call_id.split(":")[1]
            seed = int(trace.split("-")[1])
            if (seed - 7000) % 7 != 4:      # V5 is the 5th cell in manifest order
                continue
            role = "receiver" if stage.startswith("r") else "sender"
            seen_roles.add(role)
            assert prompt == (receiver if role == "receiver" else sender), call_id
        assert seen_roles == {"sender", "receiver"}

    def test_v2_receiver_stages_are_revised_too(self, full_run):
        """V2's receiver brief is also revised, so r1 and r2 carry v3 text."""
        receiver = br.compose_system_prompt(
            br.resolve_brief("V2", "receiver", br.REVISED_BRIEF_VERSION))
        assert "you will choose A." in receiver
        checked = 0
        for call_id, prompt in self._pairs(full_run):
            trace, stage = call_id.split(":")[0], call_id.split(":")[1]
            seed = int(trace.split("-")[1])
            if (seed - 7000) % 7 != 2 or stage not in ("r1", "r2"):
                continue
            checked += 1
            assert prompt == receiver, call_id
        assert checked == 10                # five V2 traces x two receiver stages

    def test_unaffected_variants_are_byte_identical_to_base(self, full_run):
        for variant in ("V0", "V1", "V3", "V6", "V7"):
            for role in ("sender", "receiver"):
                assert (br.compose_system_prompt(
                            br.resolve_brief(variant, role, br.REVISED_BRIEF_VERSION))
                        == br.compose_system_prompt(
                            br.resolve_brief(variant, role, br.BASE_BRIEF_VERSION)))

    def test_every_row_records_hashes_and_verified_delivery(self, full_run):
        for row in full_run["rows"]:
            variant = row["variant"]
            for role in ("sender", "receiver"):
                resolved = br.resolve_brief(variant, role, br.REVISED_BRIEF_VERSION)
                assert row[f"{role}_brief_display_id"] == resolved.display_id
                assert row[f"{role}_brief_sha256"] == resolved.sha256
            assert row["brief_version"] == br.REVISED_BRIEF_VERSION
            assert row["brief_delivery"]["verified"] is True
            assert row["brief_delivery"]["calls_checked"] == 5
        assert full_run["summary"]["brief_delivery_all_verified"] is True



# ------------------------------------------------- guard, retry, fallback, spend

class TestGuardRetryFallbackAndSpend:
    def test_retry_attempt_constraint_is_enforced(self, tmp_path):
        """max_attempts must be 1; the runner refuses anything else."""
        run_dir = fixture_run_dir(tmp_path)
        code = cli.live(run_dir, tmp_path / "out", True,
                        client_factory=factory(max_attempts=2))
        assert code == cli.EXIT_STOPPED_BY_GUARD
        assert not (tmp_path / "out").exists()

    def test_provider_fallback_order_is_refused(self, tmp_path):
        run_dir = fixture_run_dir(tmp_path)
        code = cli.live(run_dir, tmp_path / "out", True,
                        client_factory=factory(provider_order=["some-other-provider"]))
        assert code == cli.EXIT_STOPPED_BY_GUARD

    def test_client_not_bound_to_the_guard_is_refused(self, tmp_path):
        """A client that would bypass LedgerGuard must not be accepted."""
        run_dir = fixture_run_dir(tmp_path)
        code = cli.live(run_dir, tmp_path / "out", True,
                        client_factory=factory(real=False, guard_binding=False))
        assert code == cli.EXIT_STOPPED_BY_GUARD

    def test_cap_too_small_for_the_plan_is_refused_before_any_call(self, tmp_path):
        """The cap is enforced by the real path, not just a manifest check."""
        manifest = json.loads((REAL_RUN_DIR / "manifest.json").read_text())
        policy = json.loads((REAL_RUN_DIR / "policy.json").read_text())
        manifest["cap_usd"] = 0.50
        policy["allocations"]["sprint"]["cap_usd"] = 0.50
        policy["allocations"]["sprint"]["phase_caps_usd"]["extension"] = 0.50
        policy["backstop_usd"] = 0.50
        run_dir = fixture_run_dir(tmp_path, manifest=manifest, policy=policy)
        sent: list = []
        code = cli.live(run_dir, tmp_path / "out", True, client_factory=factory(sent=sent))
        assert code == cli.EXIT_STOPPED_BY_GUARD
        assert sent == []                       # nothing was dispatched
        assert not (tmp_path / "out").exists()

    def test_guard_refuses_a_reservation_beyond_headroom(self, tmp_path):
        """LedgerGuard itself refuses overspend, independent of the runner."""
        from monitors.client import LedgerGuard
        from monitors.ledger import (AdmissionRefused, LedgerStore, Policy, config_sha256,
                                     make_run_header, open_run)

        manifest = json.loads((REAL_RUN_DIR / "manifest.json").read_text())
        policy = json.loads((REAL_RUN_DIR / "policy.json").read_text())
        raw = json.dumps(policy).encode("utf-8")
        store = LedgerStore(tmp_path / "spend" / "ledger.json")
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.init(raw)
        bound = Policy.from_config(policy)
        header = make_run_header(manifest["run_id"], manifest["allocation"],
                                 manifest["phase"], config_sha256(raw), {},
                                 manifest["created_utc"],
                                 {"model": f"{bound.provider}/{bound.model}",
                                  "base_url": BASE_URL})
        open_run(store, bound, header, config_sha256(raw), "MM")
        guard = LedgerGuard(store, bound, config_sha256(raw), manifest["run_id"])
        with pytest.raises(AdmissionRefused):
            guard.reserve("ext-7000:s1:p1", 0, 10_000_000, 150)

    def test_reservations_and_realised_spend_stay_within_the_cap(self, full_run):
        manifest = json.loads((full_run["run_dir"] / "manifest.json").read_text())
        cap = int(round(manifest["cap_usd"] * 1_000_000))
        events = full_run["ledger"]["events"]
        assert len(events) == 175
        reserved = sum(e["reserved_micro"] for e in events)
        settled = sum(e["settled_micro"] for e in events
                      if e.get("settled_micro") is not None)
        assert 0 < settled < reserved <= cap
        assert all(e["state"] == "settled" for e in events)

    def test_no_call_is_left_unknown_or_overrun_in_a_clean_run(self, full_run):
        states = {e["state"] for e in full_run["ledger"]["events"]}
        assert states == {"settled"}
        assert full_run["summary"]["gate"]["checks"]["no_pipeline_stop"]["passed"] is True


# ------------------------------------------------------------- failure handling

def http_error_responder(_payload, _index):
    import io
    import urllib.error
    raise urllib.error.HTTPError("https://openrouter.ai/api/v1/chat/completions",
                                 500, "Server Error", {}, io.BytesIO(b"upstream failed"))


def malformed_responder(_payload, _index):
    return {"id": "x"}                      # no choices -> malformed


class TestFailureHandling:
    """A provider failure must be recorded and must stop the run safely."""

    def test_provider_http_error_stops_the_run_and_records_unknown(self, tmp_path):
        run_dir = fixture_run_dir(tmp_path)
        out_dir = tmp_path / "out"
        code = cli.live(run_dir, out_dir, True,
                        client_factory=factory(responder=http_error_responder))
        # The stage fails, the trace is invalid, the unknown-cost stop condition fires.
        assert code == cli.EXIT_RUN_INCOMPLETE
        summary = json.loads((out_dir / "probe_summary.json").read_text())
        assert summary["attempted_traces"] == 1
        assert summary["stopped"] == "unknown-cost event after ext-7000"
        assert summary["gate"]["passed"] is False
        events = json.loads((run_dir / "spend" / "ledger.json").read_text())["events"]
        assert [e["state"] for e in events] == ["unknown"]
        assert events[0]["unknown_reason"].startswith("http_error_500")

    def test_malformed_response_is_recorded_and_stops_the_run(self, tmp_path):
        run_dir = fixture_run_dir(tmp_path)
        out_dir = tmp_path / "out"
        code = cli.live(run_dir, out_dir, True,
                        client_factory=factory(responder=malformed_responder))
        assert code == cli.EXIT_RUN_INCOMPLETE
        events = json.loads((run_dir / "spend" / "ledger.json").read_text())["events"]
        assert [e["state"] for e in events] == ["unknown"]
        assert events[0]["unknown_reason"] == "malformed_response"

    def test_failure_is_never_reported_as_a_clean_run(self, tmp_path):
        run_dir = fixture_run_dir(tmp_path)
        out_dir = tmp_path / "out"
        code = cli.live(run_dir, out_dir, True,
                        client_factory=factory(responder=http_error_responder))
        assert code != cli.EXIT_OK
        summary = json.loads((out_dir / "probe_summary.json").read_text())
        assert summary["stopped"] is not None
        assert summary["gate"]["passed"] is False
        assert summary["attempted_traces"] != summary["planned_traces"]

    def test_call_limit_stops_the_run_before_the_plan_finishes(self, tmp_path):
        """Simulate a mid-run stop by capping the plan via the ledger, not max_calls."""
        manifest = json.loads((REAL_RUN_DIR / "manifest.json").read_text())
        run_dir = fixture_run_dir(tmp_path, manifest=manifest)
        out_dir = tmp_path / "out"
        sent: list = []

        def responder(payload, index):
            if index > 12:
                raise RuntimeError("stop here")
            return stage_reply(payload, index)

        code = cli.live(run_dir, out_dir, True,
                        client_factory=factory(responder=responder, sent=sent))
        assert code == cli.EXIT_STOPPED_BY_GUARD
        assert len(sent) == 13
        assert not (out_dir / "probe_summary.json").exists()

    def test_served_model_mismatch_stops_the_run(self, tmp_path):
        """A provider reporting a different model must stop the run, not pass."""
        def wrong_model(payload, index):
            return ok_body(stage_reply(payload, index)["choices"][0]["message"]["content"],
                           model="some/other-model")
        run_dir = fixture_run_dir(tmp_path)
        out_dir = tmp_path / "out"
        code = cli.live(run_dir, out_dir, True,
                        client_factory=factory(responder=wrong_model))
        assert code == cli.EXIT_RUN_INCOMPLETE
        summary = json.loads((out_dir / "probe_summary.json").read_text())
        assert "served model" in summary["stopped"]


# -------------------------------------------------------------- gate boundaries

class TestInvalidTraceGateBoundaries:
    """D6: at most 6 of 35 may be invalid; 7 fails; fewer than 35 attempted fails."""

    @staticmethod
    def _gate():
        manifest = json.loads((REAL_RUN_DIR / "manifest.json").read_text())
        return manifest["feasibility_gate"]

    @staticmethod
    def _rows(valid, invalid):
        rows = [{"trace_id": f"ext-{7000 + i:04d}", "seed": 7000 + i, "variant": "V0",
                 "condition": "FREE_MSG", "outcome": "A/A"}
                for i in range(valid)]
        rows += [{"trace_id": f"ext-{9000 + i:04d}", "seed": 9000 + i, "variant": "V0",
                  "condition": "FREE_MSG", "outcome": "invalid",
                  "invalid_reason": "s2:parse_failure"} for i in range(invalid)]
        return rows

    def test_six_invalid_of_35_passes(self):
        from apart.probe_protocol import evaluate_execution_gate
        result = evaluate_execution_gate(self._rows(29, 6), self._gate())
        assert result["checks"]["invalid_traces"] == {"observed": 6, "allowed": 6,
                                                      "passed": True}
        assert result["passed"] is True

    def test_seven_invalid_of_35_fails(self):
        from apart.probe_protocol import evaluate_execution_gate
        result = evaluate_execution_gate(self._rows(28, 7), self._gate())
        assert result["checks"]["invalid_traces"] == {"observed": 7, "allowed": 6,
                                                      "passed": False}
        assert result["passed"] is False

    def test_fewer_than_35_attempted_fails(self):
        from apart.probe_protocol import evaluate_execution_gate
        result = evaluate_execution_gate(self._rows(34, 0), self._gate())
        assert result["checks"]["attempted_traces"]["passed"] is False
        assert result["passed"] is False


# ------------------------------------------------- provenance and raw replies

class TestProvenanceAndRawReplies:
    def test_one_provenance_record_per_call(self, full_run):
        assert len(full_run["provenance"]) == 175
        call_ids = [r["call_id"] for r in full_run["provenance"]]
        assert len(set(call_ids)) == 175
        run_id = full_run["summary"]["run_id"]
        assert run_id == "gen-probe-obj10-sonnet46"
        assert all(r["run_id"] == run_id for r in full_run["provenance"])

    def test_raw_replies_preserved_for_every_call(self, full_run):
        assert len(full_run["raw"]) == 175
        assert all(r["received"] is True for r in full_run["raw"])

    def test_provenance_carries_model_and_prompt_identity(self, full_run):
        for record in full_run["provenance"][:20]:
            assert record["provider"] == "openrouter"
            assert record["model"] == MODEL
            assert record["prompt_version"] == "2.1"
            assert record["protocol_version"] == "apart-generation-probe/1.0"
            assert record["objective_id"] == "OBJ-1.0"

    def test_no_secret_material_in_provenance(self, full_run):
        blob = json.dumps(full_run["provenance"])
        assert "sk-" not in blob
        assert "Authorization" not in blob

    def test_no_secret_material_anywhere_in_the_run_outputs(self, full_run):
        """The key must not reach the ledger, the raw sidecar or the outputs."""
        for path in full_run["run_dir"].rglob("*"):
            if path.is_file() and path.suffix in (".json", ".jsonl"):
                assert "sk-" not in path.read_text(), path
        for path in full_run["out_dir"].rglob("*"):
            if path.is_file():
                assert "sk-" not in path.read_text(), path



# ------------------------------------------------------- historical integrity

class TestHistoricalArtifactsIntact:
    def test_generation_probe_pins_still_match(self):
        manifest = json.loads((REAL_RUN_DIR / "manifest.json").read_text())
        for name, digest in manifest["prompt_files_sha256"].items():
            actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            assert actual == digest, f"{name} changed"

    def test_stage_4n_pins_still_match(self):
        manifest = json.loads(
            (ROOT / "runs" / "ext-probe-v21-sonnet46" / "manifest.json").read_text())
        for name, digest in manifest["prompt_files_sha256"].items():
            actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            assert actual == digest, f"Stage 4N pin broken: {name}"

    def test_frozen_briefs_are_unmodified_after_a_run(self, full_run):
        """The seam must restore agents/prompts.BRIEFS byte-for-byte."""
        import agents.prompts as prompts
        for brief_id, digest in br.FROZEN_ORIGINAL_SHA256.items():
            assert br.brief_sha256(prompts.BRIEFS[brief_id]) == digest


