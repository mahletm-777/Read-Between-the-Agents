"""Focused offline tests for the OBJ-1.0 generation-probe execution path.

Everything here is offline: no network, no credentials, no real ledger file, no
``LedgerStore``, no extraction/labeler/monitor import. Outputs go to ``tmp_path``.
"""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import urllib.request
from pathlib import Path

import pytest

import monitors.client
import monitors.ledger
from agents.client import FakeAgentClient
from apart import behavior_probe
from apart import brief_registry as br
from apart.behavior_probe import load_probe_manifest, probe_plan
from apart.generation_probe import (GENERATION_PROVENANCE_EXTRA_FIELDS, RawRecordingClient,
                                    TraceCallWindow, assert_writable_target,
                                    build_call_record, check_brief_delivery,
                                    manifest_brief_version, run_generation_probe,
                                    source_state)
from apart.probe_dry_run import (DryRunBook, DryRunClient, fixture_manifest,
                                 fixture_policy_config, fixture_responder, load_run_config,
                                 run_dry_run)
from apart import probe_protocol
from apart.probe_protocol import (GENERATION_PROBE_MANIFEST_VERSION,
                                  GENERATION_PROBE_PROTOCOL_VERSION,
                                  GENERATION_PROBE_REQUIRED_FIELDS,
                                  GENERATION_PROBE_VARIANTS, STAGE_COUNT, ProtocolError,
                                  evaluate_execution_gate, objective_document_sha256,
                                  protected_seed_blocks, validate_execution_gate,
                                  validate_generation_probe_manifest, validate_objective,
                                  validate_seed_block)
from apart.provenance import ProvenanceLog
from apart.raw_replies import RawReplyError, RawReplyLog, split_call_id
from monitors.client import ApiError
from monitors.ledger import Policy, config_sha256

ROOT = Path(__file__).resolve().parents[1]
STAGE_4N_RUN = ROOT / "runs" / "ext-probe-v21-sonnet46"
WORKFLOW_B_RUN = ROOT / "experiments" / "generation_probe_obj10_v1"

#: The protocol's own declared audit blocks. A fixed constant, not a working-tree scan,
#: so fixtures here cannot be disturbed by whatever runs happen to exist on disk.
CONTROLLED_SEED_INVENTORY = tuple(probe_protocol.AUDIT_SEED_BLOCKS)

#: The real, tree-scanning discovery function, captured before any patch is applied.
#: Tests that must assert production behaviour call this instead of the patched name.
REAL_PROTECTED_SEED_BLOCKS = protected_seed_blocks


@pytest.fixture(autouse=True, scope="module")
def controlled_seed_inventory():
    """Isolate seed fixtures from the live inventory.

    ``validate_seed_block`` resolves ``protected_seed_blocks`` from
    ``apart.probe_protocol``'s globals when no inventory is supplied, so patching that
    name gives every fixture in this module an explicit, controlled inventory.

    Production is untouched: the default path still scans the real working tree, which
    is why ``test_production_validator_refuses_the_consumed_pilot_block`` below asserts
    the real refusal separately.
    """
    patch = pytest.MonkeyPatch()
    patch.setattr(probe_protocol, "protected_seed_blocks",
                  lambda repo_root=None: list(CONTROLLED_SEED_INVENTORY))
    yield CONTROLLED_SEED_INVENTORY
    patch.undo()


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    """Refuse the network, a real model client and a real ledger store in this file."""
    def refuse(*_args, **_kwargs):
        raise AssertionError("this test file must not do this")

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(monitors.client.ModelClient, "__init__", refuse)
    monkeypatch.setattr(monitors.ledger.LedgerStore, "__init__", refuse)



# ------------------------------------------------------------------- helpers

def manifest(**overrides) -> dict:
    data = fixture_manifest()
    data.update(overrides)
    return data


def policy_config(**overrides) -> dict:
    config = fixture_policy_config()
    config.update(overrides)
    return config


def harness(data: dict, config: dict, tmp_path: Path, *, client=None,
            book_cls=DryRunBook, raw=True, call_ids=None, invalid_stages=(),
            declined_seeds=()):
    """Minimal test wiring that mirrors how ``run_dry_run`` assembles a run."""
    policy = Policy.from_config(config)
    config_hash = config_sha256(json.dumps(config, sort_keys=True).encode("utf-8"))
    book = book_cls(policy=policy, config_hash=config_hash, manifest=data)
    ids = call_ids if call_ids is not None else []
    if client is None:
        client = DryRunClient(
            FakeAgentClient(fixture_responder(ids, invalid_stages=invalid_stages,
                                              declined_seeds=declined_seeds),
                            model=data["model"]), book, ids)
    provenance = ProvenanceLog(tmp_path / "provenance" / "calls.jsonl",
                              run_id=data["run_id"])
    raw_log = (RawReplyLog(tmp_path / "raw" / "replies.jsonl", run_id=data["run_id"])
               if raw else None)
    return book, client, provenance, raw_log, policy, config_hash


def run(data: dict, config: dict, tmp_path: Path, **kwargs):
    book, client, provenance, raw_log, policy, config_hash = harness(
        data, config, tmp_path, **kwargs)
    result = run_generation_probe(
        manifest=data, policy=policy, store=book, config_hash=config_hash,
        agent_client=client, provenance=provenance, raw_log=raw_log)
    return result, book, provenance, raw_log


def row(seed: int, outcome: str, *, reason=None, variant="V0", condition="FREE_MSG") -> dict:
    return {"trace_id": f"ext-{seed:04d}", "seed": seed, "variant": variant,
            "condition": condition, "outcome": outcome, "invalid_reason": reason,
            "sender": "agent_1", "receiver": "agent_2"}


# ------------------------------------------------------- protocol acceptance

def test_fixture_manifest_validates_for_the_new_protocol():
    data = validate_generation_probe_manifest(manifest())
    assert data["probe_manifest_version"] == GENERATION_PROBE_MANIFEST_VERSION
    assert data["protocol_version"] == GENERATION_PROBE_PROTOCOL_VERSION
    assert data["objective_id"] == "OBJ-1.0"


def test_unknown_manifest_version_is_refused():
    for version in ("4n-1.0", "generation-probe-2.0", "", None, 1.0):
        with pytest.raises(ProtocolError) as excinfo:
            validate_generation_probe_manifest(manifest(probe_manifest_version=version))
        assert "unsupported probe_manifest_version" in str(excinfo.value)


def test_protocol_version_must_match_the_manifest_version():
    with pytest.raises(ProtocolError) as excinfo:
        validate_generation_probe_manifest(
            manifest(protocol_version=behavior_probe.PROTOCOL_VERSION))
    assert "does not match" in str(excinfo.value)


def test_required_field_list_covers_the_stage_4n_contract():
    assert set(GENERATION_PROBE_REQUIRED_FIELDS) >= set(behavior_probe.REQUIRED_FIELDS)
    assert "objective_id" in GENERATION_PROBE_REQUIRED_FIELDS


def test_missing_required_field_is_refused():
    data = manifest()
    data.pop("objective_id")
    with pytest.raises(ProtocolError) as excinfo:
        validate_generation_probe_manifest(data)
    assert "missing required fields" in str(excinfo.value)


def test_stage_4n_manifest_still_validates_under_its_own_semantics():
    """Historical manifests keep their own validator, gate and code/prompt pins."""
    data = load_probe_manifest(STAGE_4N_RUN / "manifest.json")
    assert data["probe_manifest_version"] == behavior_probe.PROBE_MANIFEST_VERSION
    assert data["feasibility_gate"]["require"]["V0/FREE_MSG"] == "A/A"
    for name, digest in data["prompt_files_sha256"].items():
        actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        assert actual == digest, f"{name} changed: Stage 4N pins must stay valid"


def test_stage_4n_manifest_is_not_accepted_by_the_new_validator():
    data = json.loads((STAGE_4N_RUN / "manifest.json").read_text(encoding="utf-8"))
    with pytest.raises(ProtocolError):
        validate_generation_probe_manifest(data)


# --------------------------------------------------------------- objective id

def test_objective_id_must_be_canonical():
    with pytest.raises(ProtocolError) as excinfo:
        validate_generation_probe_manifest(manifest(objective_id="OBJ-2.0"))
    assert "canonical objective" in str(excinfo.value)


def test_pinned_objective_document_hash_is_checked_live():
    good = {"objective_id": "OBJ-1.0", "objective_doc_sha256": objective_document_sha256()}
    assert validate_objective(good) == "OBJ-1.0"
    assert validate_generation_probe_manifest(manifest(**good))["objective_doc_sha256"]
    with pytest.raises(ProtocolError):
        validate_objective({"objective_id": "OBJ-1.0", "objective_doc_sha256": "0" * 64})



# ---------------------------------------------------- execution-validity gate

def test_gate_passes_on_attempt_count_not_on_behaviour():
    rows = [row(7000 + i, "B/B", variant="V7") for i in range(35)]
    gate = evaluate_execution_gate(rows, manifest()["feasibility_gate"])
    assert gate["passed"] is True
    assert gate["behavioural_criteria_used"] is False
    assert gate["descriptive_outcome_counts"] == {"B/B": 35}


def test_gate_result_ignores_messages_and_coordination_metadata():
    base = [row(7000 + i, "A/A") for i in range(35)]
    noisy = [dict(item, sender="agent_2", receiver="agent_1",
                  sender_brief="G-WARN", receiver_brief="G-WARN",
                  s1={"message": "covert text"}) for item in base]
    gate_base = evaluate_execution_gate(base, manifest()["feasibility_gate"])
    gate_noisy = evaluate_execution_gate(noisy, manifest()["feasibility_gate"])
    assert gate_base["passed"] == gate_noisy["passed"] is True
    assert gate_noisy["checks"] == gate_base["checks"]


def test_a_rare_behaviour_cannot_fail_the_gate():
    rows = [row(7000 + i, "A/A") for i in range(34)] + [row(7034, "B/B")]
    assert evaluate_execution_gate(rows, manifest()["feasibility_gate"])["passed"] is True


def test_invalid_traces_are_counted_and_fail_only_by_their_own_allowance():
    rows = [row(7000 + i, "A/A") for i in range(33)]
    rows += [row(7033, "invalid", reason="s3:parse_failure"),
             row(7034, "invalid", reason="s2:api_error")]
    gate = evaluate_execution_gate(rows, manifest()["feasibility_gate"])
    assert gate["passed"] is False
    assert gate["checks"]["invalid_traces"] == {"observed": 2, "allowed": 0, "passed": False}
    assert gate["invalid_reasons"] == [
        {"trace_id": "ext-7033", "reason": "s3:parse_failure"},
        {"trace_id": "ext-7034", "reason": "s2:api_error"}]
    lenient = evaluate_execution_gate(
        rows, manifest(feasibility_gate={"kind": "execution_validity",
                                         "min_attempted_traces": 35,
                                         "max_invalid_traces": 2})["feasibility_gate"])
    assert lenient["passed"] is True


def test_stopped_run_fails_the_gate_even_with_enough_traces():
    rows = [row(7000 + i, "A/A") for i in range(35)]
    gate = evaluate_execution_gate(rows, manifest()["feasibility_gate"], "call limit reached")
    assert gate["passed"] is False
    assert gate["checks"]["no_pipeline_stop"]["passed"] is False


def test_legacy_cell_outcome_gate_cannot_be_inherited():
    legacy_gates = (
        {"require": {"V0/FREE_MSG": "A/A"}},
        {"kind": "cell_outcome", "require": {"V0/FREE_MSG": "A/A"}},
        {"kind": "execution_validity", "min_attempted_traces": 35,
         "max_invalid_traces": 0, "expect": {"V0/FREE_MSG": "A/A"}},
    )
    for gate in legacy_gates:
        with pytest.raises(ProtocolError):
            validate_execution_gate(gate)
        with pytest.raises(ProtocolError):
            validate_generation_probe_manifest(manifest(feasibility_gate=gate))


# -------------------------------------- D6: fixed-count invalid-trace boundary

def probe_manifest() -> dict:
    """The proposed probe manifest itself, not a fixture.

    The D6 rule is a fixed count for a fixed 35-trace design, so the boundary is
    asserted against the artifact that will actually be run rather than against the
    dry-run fixture (whose tolerance is deliberately 0).
    """
    return json.loads((WORKFLOW_B_RUN / "manifest.json").read_text(encoding="utf-8"))


def test_probe_manifest_gate_is_the_approved_fixed_count():
    """D6, as approved: at most 6 of 35 invalid; min_attempted_traces stays 35."""
    gate = probe_manifest()["feasibility_gate"]
    assert validate_execution_gate(gate)["kind"] == "execution_validity"
    assert gate["min_attempted_traces"] == 35
    assert gate["max_invalid_traces"] == 6
    # No rate/fraction spelling is accepted, because none is implementable here.
    assert not {"max_invalid_fraction", "max_invalid_rate",
                "invalid_rate_threshold"} & set(gate)


def test_d6_six_invalid_of_35_passes_the_invalid_count_check():
    rows = ([row(7000 + i, "A/A") for i in range(29)]
            + [row(7029 + i, "invalid", reason="s2:parse_failure") for i in range(6)])
    assert len(rows) == 35
    result = evaluate_execution_gate(rows, probe_manifest()["feasibility_gate"])
    assert result["checks"]["invalid_traces"] == {"observed": 6, "allowed": 6,
                                                  "passed": True}
    assert result["passed"] is True
    assert result["behavioural_criteria_used"] is False


def test_d6_seven_invalid_of_35_fails_the_invalid_count_check():
    rows = ([row(7000 + i, "A/A") for i in range(28)]
            + [row(7028 + i, "invalid", reason="s3:parse_failure") for i in range(7)])
    assert len(rows) == 35
    result = evaluate_execution_gate(rows, probe_manifest()["feasibility_gate"])
    assert result["checks"]["invalid_traces"] == {"observed": 7, "allowed": 6,
                                                  "passed": False}
    assert result["passed"] is False
    # The other two checks are unaffected: this failure is the count alone.
    assert result["checks"]["attempted_traces"]["passed"] is True
    assert result["checks"]["no_pipeline_stop"]["passed"] is True


def test_d6_fewer_than_35_attempted_traces_fails_the_minimum_check():
    """A short run cannot pass by having few invalid traces."""
    rows = [row(7000 + i, "A/A") for i in range(34)]
    result = evaluate_execution_gate(rows, probe_manifest()["feasibility_gate"])
    assert result["checks"]["attempted_traces"] == {"observed": 34, "required": 35,
                                                    "passed": False}
    assert result["checks"]["invalid_traces"]["passed"] is True
    assert result["passed"] is False


def test_d6_invalid_traces_stay_in_the_attempted_denominator():
    rows = ([row(7000 + i, "A/A") for i in range(29)]
            + [row(7029 + i, "invalid", reason="s1:api_error") for i in range(6)])
    result = evaluate_execution_gate(rows, probe_manifest()["feasibility_gate"])
    # 35 attempted, not 29 attempted-and-valid.
    assert result["checks"]["attempted_traces"]["observed"] == 35
    assert len(result["invalid_reasons"]) == 6


def test_d6_outcomes_never_influence_execution_validity():
    """Execution validity is not behavioural: any outcome mix passes if counts do."""
    gate = probe_manifest()["feasibility_gate"]
    for outcomes in ([ "A/A"] * 35, ["B/B"] * 35,
                     ["A/B", "B/A", "A/A", "B/B"] * 8 + ["A/A"] * 3):
        rows = [row(7000 + i, o) for i, o in enumerate(outcomes)]
        assert evaluate_execution_gate(rows, gate)["passed"] is True


# ---------------------------------------------------- seven-variant manifest

def test_seven_variants_are_required_exactly_once_each():
    assert GENERATION_PROBE_VARIANTS == ("V0", "V1", "V2", "V3", "V5", "V6", "V7")
    assert len(manifest()["cells"]) == 7
    for cells, note in (
        ([[variant, "FREE_MSG"] for variant in GENERATION_PROBE_VARIANTS if variant != "V7"],
         "missing variant"),
        ([[variant, "FREE_MSG"] for variant in GENERATION_PROBE_VARIANTS] + [["V7", "NEUTRAL_MSG"]],
         "extra cell"),
        ([["V4", "FREE_MSG"]] + [[v, "FREE_MSG"] for v in GENERATION_PROBE_VARIANTS[1:]],
         "unknown variant"),
        ([["V0", "FREE_MSG"]] + [[v, "FREE_MSG"] for v in GENERATION_PROBE_VARIANTS[1:6]]
         + [["V0", "FREE_MSG"]], "duplicate"),
    ):
        with pytest.raises(ProtocolError):
            validate_generation_probe_manifest(manifest(cells=cells))


def test_condition_must_be_allowed_for_the_variant():
    cells = [[v, "FREE_MSG"] for v in GENERATION_PROBE_VARIANTS]
    cells[0] = ["V0", "NO_SUCH_CONDITION"]
    with pytest.raises(ProtocolError) as excinfo:
        validate_generation_probe_manifest(manifest(cells=cells))
    assert "not allowed for" in str(excinfo.value)


def test_35_traces_and_175_calls_arithmetic_is_enforced():
    plan = probe_plan(manifest())
    assert len(plan) == 35
    assert manifest()["max_calls"] == 35 * STAGE_COUNT == 175
    assert [cell.seed for cell in plan][:3] == [7000, 7001, 7002]
    assert sorted({cell.variant for cell in plan}) == list(GENERATION_PROBE_VARIANTS)
    with pytest.raises(ProtocolError):
        validate_generation_probe_manifest(manifest(max_traces=34))
    with pytest.raises(ProtocolError):
        validate_generation_probe_manifest(manifest(max_calls=170))
    with pytest.raises(ProtocolError):
        validate_generation_probe_manifest(manifest(per_cell=4))



def test_generation_scope_locks_are_enforced():
    for overrides in ({"extraction": True}, {"labeler": True}, {"confirmatory": True},
                      {"monitor_arms": ["E"]}, {"schedule": "sequential"},
                      {"max_attempts": 2}, {"stage_parse_attempts": 3},
                      {"agent_temperature": 0.0}, {"agent_max_tokens": 256},
                      {"stop_conditions": {"retries": "parse", "fallback_model": "none"}},
                      {"stop_conditions": {"retries": "none",
                                           "fallback_model": "gemini-flash"}},
                      {"stage_context": {"s1": []}}):
        with pytest.raises(ProtocolError):
            validate_generation_probe_manifest(manifest(**overrides))


def test_prompt_files_must_pin_the_brief_text_and_match_the_tree():
    stale = dict(manifest()["prompt_files_sha256"])
    stale["agents/prompts.py"] = "0" * 64
    with pytest.raises(ProtocolError) as excinfo:
        validate_generation_probe_manifest(manifest(prompt_files_sha256=stale))
    assert "changed since the manifest was frozen" in str(excinfo.value)

    with pytest.raises(ProtocolError):
        validate_generation_probe_manifest(manifest(prompt_files_sha256={"README.md": "0" * 64}))

    pins = manifest()["prompt_files_sha256"]
    missing = {"agents/prompts.py": pins["agents/prompts.py"], "agents/nope.py": "0" * 64}
    with pytest.raises(ProtocolError):
        validate_generation_probe_manifest(manifest(prompt_files_sha256=missing))


# ------------------------------------------------------------ seed safeguards

def test_historical_seed_blocks_are_protected():
    blocks = {(low, high) for low, high, _source in protected_seed_blocks()}
    # audit-named blocks
    for block in ((0, 13), (100, 139), (900, 905), (5000, 5035), (6000, 6014)):
        assert block in blocks
    sources = " ".join(source for _l, _h, source in protected_seed_blocks())
    # evidence-based discovery beyond the audit list, incl. today's monitor smoke run
    for fragment in ("data/frozen/gate_v2_traces.jsonl", "results/pilot_v1/scores_v1.jsonl",
                     "results/gate_v1/scores_v1.jsonl",
                     "results/preflight_v1/scores_v1.jsonl",
                     "runs/ext-probe-v21-sonnet46/out/probe_traces.jsonl"):
        assert fragment in sources, fragment


def test_proposed_seed_block_must_be_disjoint():
    """Disjointness is evaluated against an explicit, controlled inventory."""
    assert validate_seed_block(7000, 35, protected=CONTROLLED_SEED_INVENTORY) == (7000, 7034)
    for start, count in ((6000, 15), (5999, 2), (6010, 10), (5000, 36), (4990, 11),
                         (0, 14), (100, 40), (900, 6)):
        with pytest.raises(ProtocolError) as excinfo:
            validate_seed_block(start, count, protected=CONTROLLED_SEED_INVENTORY)
        assert "overlaps" in str(excinfo.value)


def test_seed_check_can_be_given_an_explicit_block_list():
    with pytest.raises(ProtocolError):
        validate_seed_block(100, 5, protected=[(100, 139, "stub")])
    assert validate_seed_block(200, 5, protected=[(100, 139, "stub")]) == (200, 204)


def test_manifest_seed_block_is_validated():
    for start in (6000, 5000, 0, 100, 900):
        with pytest.raises(ProtocolError):
            validate_generation_probe_manifest(manifest(seed_start=start))


def test_seed_block_check_rejects_non_integers():
    for start, count in (("6000", 15), (7000.5, 35), (7000, True), (7000, 0)):
        with pytest.raises(ProtocolError):
            validate_seed_block(start, count, protected=CONTROLLED_SEED_INVENTORY)


# --- production protection (real working tree, deliberately NOT the controlled fixture)

def test_production_validator_refuses_the_consumed_pilot_block():
    """Regression: production must keep refusing seeds 7000-7034.

    The completed OBJ-1.0 pilot consumed this block, so the real inventory now contains
    it. ``validate_seed_block`` with no inventory supplied is what production does, so
    this asserts the refusal against the actual repository state - the same call the
    runner makes. It must not be weakened to let a re-run reuse the block.
    """
    real = REAL_PROTECTED_SEED_BLOCKS()          # unpatched, tree-scanning
    assert real != list(CONTROLLED_SEED_INVENTORY), "fixture must not have replaced this"
    with pytest.raises(ProtocolError, match="overlaps"):
        validate_seed_block(7000, 35, protected=real)
    for start, count in ((7000, 1), (7034, 1), (7033, 2), (6900, 200), (7000, 35)):
        with pytest.raises(ProtocolError, match="overlaps"):
            validate_seed_block(start, count, protected=real)


def test_production_inventory_records_the_completed_pilot_output():
    """The consumed block is discovered from the run's own output, by evidence."""
    real = REAL_PROTECTED_SEED_BLOCKS()
    sources = " ".join(source for _low, _high, source in real)
    assert "runs/generation-probe-obj10-v1-live-20261023/probe_traces.jsonl" in sources
    covering = [(low, high) for low, high, _ in real if low <= 7000 <= high]
    assert covering, "7000 is not covered by any real protected block"


def test_a_fresh_disjoint_block_is_still_accepted_by_production():
    """The refusal is specific to consumed blocks, not a blanket rejection."""
    real = REAL_PROTECTED_SEED_BLOCKS()
    assert validate_seed_block(7035, 35, protected=real) == (7035, 7069)



# ------------------------------------------------------- provenance additions

def _entry(**overrides):
    entry = {"call_id": "ext-7000:s3:p1", "stage": "action", "status": "ok",
             "prompt_sha256": "a" * 64, "usage": None, "usage_status": "unknown",
             "temperature": 1.0, "max_tokens": 150, "seed": 7000, "response_id": None,
             "served_model": None, "served_provider": None,
             "context_stages": ["s1", "s2"], "input_messages": 4,
             "request_hash_format": "messages-json-sorted-v1"}
    entry.update(overrides)
    return entry


def _event(**overrides):
    event = {"attempt": 0, "reserved_micro": 1234, "settled_micro": 1200,
             "state": "settled", "dispatched_ts": "2026-01-01T00:00:00Z"}
    event.update(overrides)
    return event


def test_call_record_carries_generation_settings_and_keeps_null_distinguishable():
    data = manifest()
    record = build_call_record(data, _entry(), _event(), "ext-7000")
    from apart.provenance import REQUIRED_FIELDS
    for name in REQUIRED_FIELDS:
        assert name in record
    for name in GENERATION_PROVENANCE_EXTRA_FIELDS:
        assert name in record
    assert record["temperature"] == 1.0
    assert record["max_tokens"] == 150
    assert record["seed"] == 7000
    assert record["objective_id"] == "OBJ-1.0"
    assert record["protocol_version"] == GENERATION_PROBE_PROTOCOL_VERSION
    assert record["prompt_version"] == data["prompt_version"]
    assert record["usage_status"] == "unknown"
    assert record["response_id"] is None
    # a provider that reports nothing must not be back-filled from the request
    assert record["served_model"] is None
    assert record["served_provider"] is None
    assert record["model"] == data["model"]


def test_call_record_marks_reported_usage_and_served_identity():
    record = build_call_record(
        manifest(),
        _entry(status="parse_failure", usage={"prompt_tokens": 10, "completion_tokens": 2},
               usage_status="reported", response_id="resp-1", served_model="upstream/x",
               served_provider="upstream"),
        _event(state="unknown", settled_micro=None, dispatched_ts="t"), "ext-7000")
    assert record["usage"] == {"prompt_tokens": 10, "completion_tokens": 2}
    assert record["usage_status"] == "reported"
    assert record["response_id"] == "resp-1"
    assert record["served_model"] == "upstream/x"
    assert record["settlement_state"] == "unknown"
    assert record["failure_category"] == "parse_failure"


def test_provenance_log_accepts_extended_records_and_refuses_overwrite(tmp_path):
    path = tmp_path / "provenance" / "calls.jsonl"
    log = ProvenanceLog(path, run_id="r")
    log.record(build_call_record(manifest(run_id="r"), _entry(), _event(), "ext-7000"))
    written = log.records()
    assert len(written) == 1
    assert written[0]["temperature"] == 1.0
    with pytest.raises(ValueError):
        ProvenanceLog(path, run_id="r")


def _current_head_ref() -> str:
    """The ref this checkout is actually on, as ``refs/heads/<branch>``.

    ``source_state`` reads ``.git/HEAD`` directly, so the value it reports depends on the
    branch the clone happens to be on. Resolving it from git keeps the test branch-agnostic
    instead of hard-coding ``refs/heads/main``.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1],
    )
    if result.returncode != 0:
        pytest.skip("not a git checkout; no HEAD ref to compare against")
    name = result.stdout.strip()
    if not name or name == "HEAD":
        pytest.skip("detached HEAD; there is no branch ref to compare against")
    return f"refs/heads/{name}"


def test_source_state_reports_head_without_claiming_cleanliness():
    state = source_state()
    assert state["source_ref"] == _current_head_ref()
    assert state["source_commit"] is None or len(state["source_commit"]) == 40
    assert state["source_dirty"] is None      # not determined, never claimed clean
    assert source_state(observed={"source_dirty": True})["source_dirty"] is True



# ------------------------------------------------------------- raw sidecar

def test_split_call_id_parses_the_stage_protocol():
    assert split_call_id("ext-7000:s2:p1") == ("ext-7000", "s2", 1)
    for bad in ("ext-7000:s2", "ext-7000:s2:p", "", None, 7):
        with pytest.raises(RawReplyError):
            split_call_id(bad)


def test_raw_sidecar_records_text_and_links_the_call(tmp_path):
    log = RawReplyLog(tmp_path / "raw" / "replies.jsonl", run_id="r")
    log.record(call_id="ext-7000:s2:p1", received=True, text='{"message": "hi"}',
               status="received", ts="2026-01-01T00:00:00Z")
    record = log.records()[0]
    assert (record["run_id"], record["trace_id"], record["stage"], record["attempt"]) == (
        "r", "ext-7000", "s2", 1)
    assert record["text"] == '{"message": "hi"}'
    assert record["text_sha256"] == hashlib.sha256(b'{"message": "hi"}').hexdigest()
    assert record["received"] is True


def test_raw_sidecar_never_invents_text_for_a_failed_transport(tmp_path):
    log = RawReplyLog(tmp_path / "replies.jsonl", run_id="r")
    for kwargs in ({"received": False, "text": "made up", "status": "transport_error",
                    "error": "ApiError"},
                   {"received": True, "text": None, "status": "received", "error": None},
                   {"received": False, "text": None, "status": "transport_error",
                    "error": None}):
        with pytest.raises(RawReplyError):
            log.record(call_id="ext-7000:s1:p1", ts="t", **kwargs)
    assert log.records() == []


def test_raw_sidecar_refuses_credential_shaped_text_and_overwrites(tmp_path):
    path = tmp_path / "replies.jsonl"
    log = RawReplyLog(path, run_id="r")
    with pytest.raises(ValueError):
        log.record(call_id="ext-7000:s1:p1", received=True,
                   text="send headers: Bearer abc123", status="received", ts="t")
    assert log.records() == []
    log.record(call_id="ext-7000:s1:p1", received=True, text="ok", status="received", ts="t")
    with pytest.raises(RawReplyError):
        RawReplyLog(path, run_id="r")


class _BoomClient:
    model = "m"

    def call_model(self, messages, **kwargs):
        raise ApiError("provider unreachable")


def test_raw_recording_client_mirrors_replies_and_re_raises(tmp_path):
    log = RawReplyLog(tmp_path / "replies.jsonl", run_id="r")
    wrapper = RawRecordingClient(FakeAgentClient(lambda *_: '{"action": "A"}', model="m"), log)
    wrapper.call_model([{"role": "user", "content": "x"}], call_id="ext-7001:s3:p1")
    first = log.records()[0]
    assert first["text"] == '{"action": "A"}' and first["stage"] == "s3"

    boom = RawRecordingClient(_BoomClient(), log)
    with pytest.raises(ApiError):
        boom.call_model([{"role": "user", "content": "x"}], call_id="ext-7002:s1:p1")
    last = log.records()[-1]
    assert last["received"] is False and last["text"] is None
    assert last["error"] == "ApiError"


def test_raw_recording_client_delegates_model_and_max_attempts():
    wrapper = RawRecordingClient(FakeAgentClient(model="m"), None)
    assert wrapper.model == "m"
    assert getattr(wrapper, "max_attempts", 1) == 1


# ------------------------------------------------- invalid trace preservation

def test_invalid_trace_is_preserved_with_reason_and_keeps_its_raw_reply(tmp_path):
    gate = {"kind": "execution_validity", "min_attempted_traces": 35,
            "max_invalid_traces": 1}
    data = manifest(run_id="r-invalid", feasibility_gate=gate)
    result, book, provenance, raw_log = run(data, policy_config(), tmp_path,
                                            invalid_stages=[(7000, "s1")])
    rows = result["rows"]
    assert len(rows) == 35                      # failures stay in the denominator
    first = rows[0]
    assert first["outcome"] == "invalid"
    assert first["invalid_reason"].startswith("s1:")
    assert first["s1"]["status"] != "ok"
    assert result["summary"]["gate"]["passed"] is True
    assert result["summary"]["gate"]["checks"]["invalid_traces"]["observed"] == 1
    assert result["summary"]["descriptive_counts_by_cell"]["V0/FREE_MSG"] == {
        "A/A": 4, "invalid": 1}

    texts = {record["call_id"]: record["text"] for record in raw_log.records()}
    assert texts["ext-7000:s1:p1"] == "I would rather not answer in JSON."
    assert "ext-7000:s2:p1" not in texts         # the failed trace stopped there
    failed = [record for record in provenance.records()
              if record["call_id"] == "ext-7000:s1:p1"][0]
    assert failed["failure_category"] == "parse_failure"


# ------------------------------------------------------------ stop conditions

class _UnknownCostBook(DryRunBook):
    def reserve_and_settle(self, **kwargs):
        event = super().reserve_and_settle(**kwargs)
        self._ledger["events"][-1] = dict(event, state="unknown", settled_micro=None,
                                          unknown_reason="stub")
        return event


class _OverrunBook(DryRunBook):
    def reserve_and_settle(self, **kwargs):
        event = super().reserve_and_settle(**kwargs)
        self._ledger["events"][-1] = dict(event,
                                          settled_micro=event["reserved_micro"] + 1)
        return event


def test_unknown_cost_stops_the_run(tmp_path):
    result, *_ = run(manifest(run_id="r-unknown"), policy_config(), tmp_path,
                     book_cls=_UnknownCostBook)
    assert result["summary"]["stopped"].startswith("unknown-cost event")
    assert len(result["rows"]) == 1
    assert result["summary"]["gate"]["passed"] is False


def test_settlement_overrun_stops_the_run(tmp_path):
    result, *_ = run(manifest(run_id="r-overrun"), policy_config(), tmp_path,
                     book_cls=_OverrunBook)
    assert result["summary"]["stopped"].startswith(
        "a settlement exceeded its reservation")
    assert len(result["rows"]) == 1


class _ServedMismatchClient:
    """Reports a served model that differs from the requested one."""

    def __init__(self, inner, served: str) -> None:
        self._inner = inner
        self.model = inner.model
        self._served = served

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def call_model(self, messages, **kwargs):
        response = self._inner.call_model(messages, **kwargs)
        response.served_model = self._served
        return response


def test_served_model_mismatch_stops_the_run(tmp_path):
    data = manifest(run_id="r-served")
    config = policy_config()
    policy = Policy.from_config(config)
    config_hash = config_sha256(json.dumps(config, sort_keys=True).encode("utf-8"))
    book = DryRunBook(policy=policy, config_hash=config_hash, manifest=data)
    ids: list = []
    client = DryRunClient(
        _ServedMismatchClient(FakeAgentClient(fixture_responder(ids), model=data["model"]),
                              "upstream/other"), book, ids)
    result = run_generation_probe(
        manifest=data, policy=policy, store=book, config_hash=config_hash,
        agent_client=client,
        provenance=ProvenanceLog(tmp_path / "provenance" / "calls.jsonl",
                                 run_id=data["run_id"]))
    assert result["summary"]["stopped"].startswith("served model")
    assert len(result["rows"]) == 1
    assert result["rows"][0]["served_models"] == ["upstream/other"]


def test_budget_refusal_sends_no_request(tmp_path):
    data = manifest(run_id="r-budget", cap_usd=0.001)
    config = policy_config()
    config["allocations"]["sprint"]["cap_usd"] = 0.001
    config["allocations"]["sprint"]["phase_caps_usd"]["extension"] = 0.001
    config["backstop_usd"] = 0.001
    inner = FakeAgentClient(fixture_responder([]), model=data["model"])
    book, client, provenance, raw_log, policy, config_hash = harness(
        data, config, tmp_path, client=inner)
    with pytest.raises(behavior_probe.ProbeError):
        run_generation_probe(
            manifest=data, policy=policy, store=book, config_hash=config_hash,
            agent_client=client, provenance=provenance, raw_log=raw_log)
    assert inner.calls == []
    assert raw_log.records() == []
    assert provenance.records() == []


def test_reuse_of_a_run_id_with_ledger_events_is_refused(tmp_path):
    data = manifest(run_id="r-dup")
    config = policy_config()
    book, client, provenance, raw_log, policy, config_hash = harness(data, config, tmp_path)
    book.reserve_and_settle(messages=[{"role": "user", "content": "x"}], max_tokens=150,
                            call_id="ext-7000:s1:p1")
    with pytest.raises(behavior_probe.ProbeError) as excinfo:
        run_generation_probe(
            manifest=data, policy=policy, store=book, config_hash=config_hash,
            agent_client=client, provenance=provenance, raw_log=raw_log)
    assert "already has ledger events" in str(excinfo.value)




# ------------------------------------------------------------------ dry run

def test_dry_run_writes_fresh_artifacts_and_refuses_to_overwrite(tmp_path):
    out = tmp_path / "dry"
    outcome = run_dry_run(out_dir=out)
    assert outcome["calls"] == 175
    assert outcome["summary"]["attempted_traces"] == 35
    assert outcome["summary"]["gate"]["passed"] is True
    assert len(outcome["provenance_records"]) == 175
    assert len(outcome["raw_records"]) == 175
    for relative in ("manifest.json", "config.json", "provenance/calls.jsonl",
                     "raw/replies.jsonl", "out/probe_traces.jsonl",
                     "out/probe_summary.json"):
        assert (out / relative).is_file(), relative
    lines = (out / "out" / "probe_traces.jsonl").read_text().splitlines()
    assert len(lines) == 35
    assert json.loads(lines[0])["trace_id"] == "ext-7000"
    with pytest.raises(FileExistsError):
        run_dry_run(out_dir=out)


def test_dry_run_output_dir_must_be_a_fresh_directory(tmp_path):
    existing = tmp_path / "already-here"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        run_dry_run(out_dir=existing)


def test_dry_run_makes_no_network_call_and_opens_no_ledger(tmp_path):
    # the autouse `offline_only` fixture already refuses urlopen, socket.connect,
    # ModelClient and LedgerStore for every test in this file; this test proves the
    # full dry run completes under those refusals and writes no ledger file.
    outcome = run_dry_run(out_dir=tmp_path / "dry")
    assert outcome["summary"]["gate"]["passed"] is True
    assert list(tmp_path.rglob("ledger*.json")) == []
    assert list(tmp_path.rglob("*.env")) == []


def test_run_output_carries_no_labels_or_monitor_scores(tmp_path):
    outcome = run_dry_run(out_dir=tmp_path / "dry")
    serialized = json.dumps(outcome["summary"]) + (tmp_path / "dry" / "out" /
                                                  "probe_traces.jsonl").read_text()
    for token in ("e_label", "E-label", "monitor_score", "ground_truth",
                  "trajectory_class", "probe_label"):
        assert token not in serialized


def test_dry_run_layout_is_reproducible_for_the_same_fixture(tmp_path):
    first = run_dry_run(out_dir=tmp_path / "a")
    second = run_dry_run(out_dir=tmp_path / "b")
    assert first["call_ids"] == second["call_ids"]
    assert first["summary"]["descriptive_counts_by_cell"] == \
        second["summary"]["descriptive_counts_by_cell"]
    assert first["summary"]["worst_case_micro"] == second["summary"]["worst_case_micro"]


def test_dry_run_with_injected_failures_reports_them(tmp_path):
    outcome = run_dry_run(out_dir=tmp_path / "dry", invalid_stages=[(7000, "s1")],
                          declined_seeds=[7001])
    summary = outcome["summary"]
    assert summary["gate"]["passed"] is False          # 1 invalid against allowance 0
    assert summary["gate"]["checks"]["invalid_traces"]["observed"] == 1
    assert summary["descriptive_counts_by_cell"]["V0/FREE_MSG"] == {
        "A/A": 4, "invalid": 1}
    assert outcome["raw_records"][0]["received"] is True


def test_dry_run_accepts_a_supplied_manifest_and_policy(tmp_path):
    data = manifest(run_id="r-supplied")
    config = policy_config()
    out = tmp_path / "supplied"
    outcome = run_dry_run(out_dir=out, manifest=data, policy_config=config)
    assert outcome["summary"]["run_id"] == "r-supplied"
    assert json.loads((out / "manifest.json").read_text())["run_id"] == "r-supplied"


# --------------------------------------------------------- output protection

def test_writable_target_refusal_rules(tmp_path):
    from apart.generation_probe import ProbePathError

    for relative in (("data", "x"), ("docs", "x"), ("results", "x"),
                     ("configs", "x"), ("AI Context", "x"),
                     ("runs", "ext-probe-v21-sonnet46", "provenance", "new.jsonl")):
        with pytest.raises(ProbePathError):
            assert_writable_target(ROOT.joinpath(*relative))
    assert assert_writable_target(tmp_path / "fresh") == (tmp_path / "fresh").resolve()
    # a brand-new run directory is allowed, an existing one is not
    assert assert_writable_target(ROOT / "runs" / "dry-run-only-fresh-name")



# --------------------------------------------------------------- scope locks

FORBIDDEN_REFERENCES = (
    "apart.labeler", "apart.e_label", "apart.e_analysis", "apart.analysis",
    "apart.live_generation", "apart.environment", "monitors.observable",
    "monitors.runner", "label_trajectory", "GroundTruth",
)


def test_new_modules_reference_no_labeler_monitor_or_extractor_code():
    """Scope lock at source level: the probe code has no such call site at all.

    Note: ``apart/__init__.py`` imports ``.labeler`` and ``monitors/__init__.py``
    imports ``.observable``, so *importing* anything under those packages loads those
    modules. That is pre-existing package behaviour (it is equally true of
    ``apart.provenance`` alone), and no module here calls them.
    """
    for name in ("generation_probe.py", "probe_protocol.py", "probe_dry_run.py",
                 "raw_replies.py"):
        text = (ROOT / "src" / "apart" / name).read_text(encoding="utf-8")
        for token in FORBIDDEN_REFERENCES:
            assert token not in text, f"{name} references {token}"


def test_stage_count_matches_the_implemented_protocol():
    from agents.runner import STAGE_CONTEXT
    assert STAGE_COUNT == len(STAGE_CONTEXT) == 5
    assert list(STAGE_CONTEXT) == ["s1", "r1", "s2", "s3", "r2"]


def test_load_run_config_reads_a_run_directory_without_writing(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    data = manifest(run_id="r-dir")
    config = policy_config()
    (run_dir / "manifest.json").write_text(json.dumps(data))
    (run_dir / "policy.json").write_text(json.dumps(config))
    loaded_manifest, loaded_config = load_run_config(run_dir)
    assert loaded_manifest["run_id"] == "r-dir" and loaded_config["provider"] == "openrouter"
    # the dry run then refuses the same directory as an output target
    with pytest.raises(FileExistsError):
        run_dry_run(out_dir=run_dir)



# ------------------------------------------- workflow B integration (read-only)

workflow_b_present = pytest.mark.skipif(
    not (WORKFLOW_B_RUN / "manifest.json").is_file(),
    reason="the proposed generation-probe artifacts are not present")


@workflow_b_present
def test_proposed_manifest_and_policy_are_accepted_by_this_protocol():
    data, config = load_run_config(WORKFLOW_B_RUN)
    validate_generation_probe_manifest(data)
    policy = Policy.from_config(config)
    from apart.behavior_probe import check_binding
    check_binding(data, policy)          # manifest cap == policy phase/allocation/backstop
    assert data["probe_manifest_version"] == GENERATION_PROBE_MANIFEST_VERSION
    assert data["protocol_version"] == GENERATION_PROBE_PROTOCOL_VERSION
    assert data["feasibility_gate"]["kind"] == "execution_validity"
    assert "require" not in data["feasibility_gate"]


@workflow_b_present
def test_proposed_plan_arithmetic_and_worst_case_fit_the_proposed_cap():
    from apart.behavior_probe import plan_worst_case
    data, config = load_run_config(WORKFLOW_B_RUN)
    policy = Policy.from_config(config)
    worst = plan_worst_case(data, policy)
    assert (worst["traces"], worst["calls"]) == (35, 175)
    assert worst["fits"] is True, (
        f"worst case {worst['total_micro']} µ$ exceeds the proposed cap "
        f"{worst['cap_micro']} µ$")


@workflow_b_present
def test_dry_run_against_the_proposed_artifacts_needs_no_network(tmp_path):
    data, config = load_run_config(WORKFLOW_B_RUN)
    outcome = run_dry_run(out_dir=tmp_path / "proposed", manifest=data,
                          policy_config=config)
    summary = outcome["summary"]
    assert summary["run_id"] == data["run_id"]
    assert summary["attempted_traces"] == 35
    assert outcome["calls"] == 175
    assert summary["gate"]["passed"] is True
    assert summary["stopped"] is None
    assert len(outcome["provenance_records"]) == 175


def test_synthetic_reservations_never_exceed_the_planned_worst_case(tmp_path):
    """The in-memory reservations are derived from the real bound + policy.

    A reservation above the pre-computed worst case would mean the plan bound is not
    an upper bound; that must be impossible.
    """
    from apart.behavior_probe import plan_worst_case
    data = manifest()
    config = policy_config()
    worst = plan_worst_case(data, Policy.from_config(config))
    result, book, _p, _r = run(data, config, tmp_path)
    reserved = sum(event["reserved_micro"] for event in book.events)
    assert len(book.events) == 175
    assert reserved <= worst["total_micro"]
    assert result["summary"]["worst_case_micro"] == worst["total_micro"]


# ---------------------------------------------- revised-brief (v3.0) integration

def revised_manifest(**overrides) -> dict:
    """Fixture manifest that declares the revised brief version explicitly."""
    data = manifest(**overrides)
    data["brief_versions"] = {"version": br.REVISED_BRIEF_VERSION, "status": "test"}
    return data


def run_capturing_calls(data: dict, config: dict, tmp_path: Path):
    """Run the probe and hand back every call's delivered messages.

    ``FakeAgentClient.calls`` accumulates across the whole run, which is exactly the
    property the per-trace call window has to cope with, so this is the realistic
    wiring rather than a per-trace client.
    """
    policy = Policy.from_config(config)
    config_hash = config_sha256(json.dumps(config, sort_keys=True).encode("utf-8"))
    book = DryRunBook(policy=policy, config_hash=config_hash, manifest=data)
    ids: list = []
    inner = FakeAgentClient(fixture_responder(ids), model=data["model"])
    client = DryRunClient(inner, book, ids)
    provenance = ProvenanceLog(tmp_path / "provenance" / "calls.jsonl",
                               run_id=data["run_id"])
    result = run_generation_probe(
        manifest=data, policy=policy, store=book, config_hash=config_hash,
        agent_client=client, provenance=provenance, raw_log=None)
    return result, inner.calls


def delivered_system_prompts(calls, plan, variant, stage=None):
    """System prompts actually delivered for one variant (optionally one stage)."""
    out = []
    for call in calls:
        cid = str(call.get("call_id") or "")
        if plan.get(cid.split(":")[0]) != variant:
            continue
        if stage is not None and f":{stage}:" not in cid:
            continue
        out.append(call["messages"][0]["content"])
    return out


class TestRevisedBriefIntegration:
    """The execution path must really deliver the v3.0 briefs, V2/V5 only."""

    def test_the_proposed_manifest_declares_the_revised_version(self):
        assert (probe_manifest()["brief_versions"]["version"]
                == br.REVISED_BRIEF_VERSION)

    def test_run_reports_the_declared_brief_version(self, tmp_path):
        result, _calls = run_capturing_calls(revised_manifest(), policy_config(), tmp_path)
        assert result["summary"]["brief_version"] == br.REVISED_BRIEF_VERSION
        assert result["summary"]["brief_delivery_all_verified"] is True
        assert result["summary"]["attempted_traces"] == 35


    def test_every_row_records_version_display_ids_and_hashes(self, tmp_path):
        result, _calls = run_capturing_calls(revised_manifest(), policy_config(), tmp_path)
        assert len(result["rows"]) == 35
        for r in result["rows"]:
            variant = r["variant"]
            sender = br.resolve_brief(variant, "sender", br.REVISED_BRIEF_VERSION)
            receiver = br.resolve_brief(variant, "receiver", br.REVISED_BRIEF_VERSION)
            assert r["brief_version"] == br.REVISED_BRIEF_VERSION
            assert r["sender_brief_display_id"] == sender.display_id
            assert r["receiver_brief_display_id"] == receiver.display_id
            assert r["sender_brief_sha256"] == sender.sha256
            assert r["receiver_brief_sha256"] == receiver.sha256
            assert r["sender_brief_sha256"] == br.brief_sha256(sender.text)
            # delivered prompt text was compared byte-for-byte, per call
            assert r["brief_delivery"]["verified"] is True
            assert r["brief_delivery"]["calls_checked"] == 5

    def test_v2_both_roles_receive_the_revised_brief(self, tmp_path):
        data = revised_manifest()
        _result, calls = run_capturing_calls(data, policy_config(), tmp_path)
        plan = {f"ext-{c.seed:04d}": c.variant for c in probe_plan(data)}
        expected = br.compose_system_prompt(
            br.resolve_brief("V2", "sender", br.REVISED_BRIEF_VERSION))
        for stage in ("s1", "s2", "s3", "r2"):
            sent = delivered_system_prompts(calls, plan, "V2", stage)
            assert sent, f"no V2 {stage} call captured"
            assert all(s == expected for s in sent), f"V2/{stage} not the revised brief"
        assert "you will choose A." in expected
        assert "follow the policy" not in expected

    def test_v5_sender_is_revised_and_receiver_keeps_g_base(self, tmp_path):
        data = revised_manifest()
        _result, calls = run_capturing_calls(data, policy_config(), tmp_path)
        plan = {f"ext-{c.seed:04d}": c.variant for c in probe_plan(data)}
        revised = br.compose_system_prompt(
            br.resolve_brief("V5", "sender", br.REVISED_BRIEF_VERSION))
        base = br.compose_system_prompt(
            br.resolve_brief("V5", "receiver", br.BASE_BRIEF_VERSION))
        assert revised != base
        for stage in ("s1", "s2", "s3"):
            sent = delivered_system_prompts(calls, plan, "V5", stage)
            assert sent and all(s == revised for s in sent), f"V5/{stage} not revised"
        r2 = delivered_system_prompts(calls, plan, "V5", "r2")
        assert r2 and all(s == base for s in r2), "V5 receiver must keep G-BASE"

    def test_unaffected_variants_deliver_byte_identical_text(self, tmp_path):
        data = revised_manifest()
        _result, calls = run_capturing_calls(data, policy_config(), tmp_path)
        plan = {f"ext-{c.seed:04d}": c.variant for c in probe_plan(data)}
        for variant in ("V0", "V1", "V3", "V6", "V7"):
            for stage in ("s1", "s2", "s3", "r1", "r2"):
                role = "receiver" if stage.startswith("r") else "sender"
                base = br.compose_system_prompt(
                    br.resolve_brief(variant, role, br.BASE_BRIEF_VERSION))
                revised = br.compose_system_prompt(
                    br.resolve_brief(variant, role, br.REVISED_BRIEF_VERSION))
                assert base == revised, f"{variant}/{role} changed across versions"
                sent = delivered_system_prompts(calls, plan, variant, stage)
                assert sent and all(s == base for s in sent), f"{variant}/{stage} drifted"

    def test_a_shared_client_across_variants_still_verifies_every_trace(self):
        """Regression: run-wide ``client.calls`` must not leak across traces."""
        client = FakeAgentClient()
        for seed, variant in ((7000, "V0"), (7001, "V2"), (7002, "V5")):
            record = br.run_trace_briefed(
                seed, variant, "FREE_MSG", TraceCallWindow(client),
                brief_version=br.REVISED_BRIEF_VERSION, prompt_version="2.1",
                max_stage_attempts=1, max_output_bytes=512, stop_on_failure=True)
            assert record["brief_delivery"]["verified"] is True
            assert record["brief_delivery"]["calls_checked"] >= 1
        assert len(client.calls) >= 3  # one client served every trace



class TestBriefIntegrationFailsClosed:
    """An unavailable or unverifiable brief version must stop the run."""

    def test_manifest_without_a_brief_version_is_refused(self, tmp_path):
        data = manifest()
        data.pop("brief_versions")
        with pytest.raises(behavior_probe.ProbeError, match="brief_versions"):
            run(data, policy_config(), tmp_path)

    def test_unknown_brief_version_is_refused(self, tmp_path):
        data = manifest(brief_versions={"version": "9.9"})
        with pytest.raises(behavior_probe.ProbeError, match="unknown brief version"):
            run(data, policy_config(), tmp_path)

    def test_unknown_version_stops_before_the_first_request(self, tmp_path):
        data = manifest(brief_versions={"version": "9.9"})
        config = policy_config()
        policy = Policy.from_config(config)
        config_hash = config_sha256(json.dumps(config, sort_keys=True).encode("utf-8"))
        book = DryRunBook(policy=policy, config_hash=config_hash, manifest=data)
        ids: list = []
        client = DryRunClient(FakeAgentClient(fixture_responder(ids), model=data["model"]),
                              book, ids)
        provenance = ProvenanceLog(tmp_path / "provenance" / "calls.jsonl",
                                   run_id=data["run_id"])
        with pytest.raises(behavior_probe.ProbeError):
            run_generation_probe(manifest=data, policy=policy, store=book,
                                 config_hash=config_hash, agent_client=client,
                                 provenance=provenance, raw_log=None)
        assert ids == []           # nothing was dispatched
        assert book.events == []   # nothing was reserved

    def test_manifest_brief_version_helper_rejects_a_missing_block(self):
        with pytest.raises(behavior_probe.ProbeError):
            manifest_brief_version({})
        with pytest.raises(behavior_probe.ProbeError):
            manifest_brief_version({"brief_versions": {}})

    def test_check_brief_delivery_rejects_unverified_evidence(self):
        with pytest.raises(behavior_probe.ProbeError, match="no brief_delivery"):
            check_brief_delivery({}, "ext-7000")
        with pytest.raises(behavior_probe.ProbeError, match="could not be verified"):
            check_brief_delivery(
                {"brief_delivery": {"verified": False, "reason": "no captured calls"}},
                "ext-7000")
        with pytest.raises(behavior_probe.ProbeError, match="zero calls"):
            check_brief_delivery(
                {"brief_delivery": {"verified": True, "calls_checked": 0}}, "ext-7000")
        assert check_brief_delivery(
            {"brief_delivery": {"verified": True, "calls_checked": 5}},
            "ext-7000")["calls_checked"] == 5


class TestTraceCallWindow:
    """The window is what makes per-trace verification correct on a shared client."""

    def test_window_exposes_only_calls_made_while_in_scope(self):
        inner = FakeAgentClient()
        first = TraceCallWindow(inner)
        first.call_model([{"role": "system", "content": "one"}], call_id="ext-7000:s1:p1")
        assert len(first.calls) == 1
        second = TraceCallWindow(inner)
        assert second.calls == [], "a new window must not see earlier traces"
        second.call_model([{"role": "system", "content": "two"}], call_id="ext-7001:s1:p1")
        assert len(second.calls) == 1
        assert len(first.calls) == 1, "the earlier window is a snapshot, not a live view"
        assert len(inner.calls) == 2, "the wrapped client still records everything"

    def test_window_delegates_other_attributes(self):
        inner = FakeAgentClient(model="delegated-model")
        window = TraceCallWindow(inner)
        assert window.model == "delegated-model"
        assert window.snapshot == inner.snapshot

    def test_window_records_the_messages_it_forwards(self):
        inner = FakeAgentClient()
        window = TraceCallWindow(inner)
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}]
        window.call_model(messages, call_id="ext-7000:s1:p1", seed=7000)
        assert window.calls[0]["messages"] == messages
        assert window.calls[0]["call_id"] == "ext-7000:s1:p1"
        # the inner client received the same prompt, unaltered
        assert inner.calls[0]["messages"] == messages

