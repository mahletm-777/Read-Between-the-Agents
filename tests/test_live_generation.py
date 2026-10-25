"""Stage 4K: guarded live generation path — extractor, CLI wiring, provenance.

Offline only. The single network seam (``ModelClient._post_json``) is replaced by
a stub and ``urllib.request.urlopen`` is blocked by an autouse guard, so the real
``LedgerGuard`` reserve -> dispatch -> settle path is exercised with no provider
contact. Every ledger, corpus and provenance file lives under ``tmp_path``; the
repository is never written to.

Covers the 18 behaviours required for the capped live run.
"""

import hashlib
import io
import json
import urllib.error
from pathlib import Path

import pytest

import scripts.generate_extension as gen
from agents.client import FakeAgentClient
from agents.runner import run_trace, stage_call_id
from apart.live_generation import (
    ManifestError,
    check_model_binding,
    live_plan,
    load_manifest,
    run_live,
    validate_manifest,
    write_corpus,
)
from apart.provenance import REQUIRED_FIELDS, ProvenanceError, ProvenanceLog, make_record
from extraction.client import (
    EXTRACTOR_MAX_TOKENS,
    build_extractor_request,
    ExtractorError,
    FakeExtractorClient,
    GuardedExtractorClient,
    parse_extraction,
)
from extraction.prompts import EXTRACTOR_PROMPT_SHA256, GAME_CONTEXT_SHA256, extractor_system
from monitors.client import ApiError, LedgerGuard, ModelClient, _input_token_bound
from monitors.ledger import (
    AdmissionRefused,
    DuplicateReservation,
    LedgerError,
    LedgerStore,
    Policy,
    commitments,
    config_sha256,
    make_run_header,
    open_run,
    sweep_stale,
)

ROOT = Path(__file__).resolve().parents[1]

PROVIDER = "openrouter"
MODEL = "anthropic/claude-sonnet-4.6"
BOUND_MODEL = f"{PROVIDER}/{MODEL}"
BASE_URL = "https://openrouter.ai/api/v1"
SERVED_PROVIDER = "Claude Platform on AWS"  # what OpenRouter reported in the smoke test
RUN_ID = "feas-test-1"

# A payload that satisfies every stage parser (§1).
STAGE_TEXT = json.dumps({"plan": "A", "note": "", "message": "we should both choose B",
                         "action": "B"})
# A payload that satisfies the extractor's JSON contract (§6.1).
EXTRACTOR_TEXT = json.dumps({
    "claims": [{"target": "B", "type": "proposal", "confidence": "clear"}],
    "coordination_attempt": "yes", "attempt_type": "EXPLICIT",
    "directness": "DIRECT", "speech_act_types": ["PROPOSAL"], "note": "joint plan",
})
USAGE = {"prompt_tokens": 100, "completion_tokens": 20}

# Worst-case reservation the guard computes for one extractor call of "hello",
# derived from source rather than hardcoded: UTF-8 byte bound at $3/M in plus
# max_tokens at $15/M out (Policy.worst_case_micro).
EXTRACTOR_BOUND = _input_token_bound(build_extractor_request("hello").messages())
EXTRACTOR_RESERVED = EXTRACTOR_BOUND * 3 + EXTRACTOR_MAX_TOKENS * 15


def policy_config(cap_usd: float = 3.0) -> dict:
    return {
        "provider": PROVIDER,
        "model": MODEL,
        "prices": {BOUND_MODEL: {"input_per_million": 3.0, "output_per_million": 15.0}},
        "allocations": {"sprint": {"cap_usd": cap_usd,
                                   "phase_caps_usd": {"extension": cap_usd}}},
        "backstop_usd": cap_usd,
        "stale_after_s": 600,
    }


def policy_bytes(cap_usd: float = 3.0) -> bytes:
    return json.dumps(policy_config(cap_usd)).encode("utf-8")


def manifest_for(run_id: str = RUN_ID, per_cell: int = 1, variants=None,
                 cap_usd: float = 3.0, **overrides) -> dict:
    variants = variants or {"V0": ["FREE_MSG", "NO_MSG"]}
    data = {
        "manifest_version": "1.0", "run_id": run_id,
        "design_status": "feasibility_pilot_not_confirmatory", "c_set_measured": False,
        "provider": PROVIDER, "model": MODEL, "base_url": BASE_URL,
        "allocation": "sprint", "phase": "extension",
        "variants": variants, "per_cell": per_cell,
        "max_traces": sum(len(v) for v in variants.values()) * per_cell,
        "max_calls": 200, "max_attempts": 1, "agent_max_tokens": 150,
        "extractor_max_tokens": EXTRACTOR_MAX_TOKENS, "seed_start": 5000,
        "cap_usd": cap_usd, "label_definition": "Y_H = collusive_talk OR safety_violation",
        "monitor_arms": [],
        "stop_conditions": {"max_trace_failures": 0, "max_unknown_reservations": 0},
    }
    data.update(overrides)
    return data


def ok_response(text, usage=USAGE, model=MODEL, provider=SERVED_PROVIDER):
    body = {"choices": [{"message": {"content": text}}], "model": model, "id": "resp-1"}
    if usage is not None:
        body["usage"] = usage
    if provider is not None:
        body["provider"] = provider
    return body


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any real socket use is a test failure."""
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr("urllib.request.urlopen", refuse)


def make_run(tmp_path, transport, *, cap_usd=3.0, run_id=RUN_ID, phase="extension"):
    """Real LedgerStore + real LedgerGuard + real ModelClient; only transport stubbed."""
    cfg = policy_bytes(cap_usd)
    config_hash = config_sha256(cfg)
    policy = Policy.from_config(json.loads(cfg))
    store = LedgerStore(tmp_path / "spend" / "ledger.json")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.init(cfg)
    header = make_run_header(run_id, "sprint", phase, config_hash, {},
                             "2026-10-22T00:00:00Z",
                             {"model": BOUND_MODEL, "base_url": BASE_URL})
    open_run(store, policy, header, config_hash, "MM")
    guard = LedgerGuard(store, policy, config_hash, run_id)
    client = ModelClient(guard=guard, api_key="sk-test-value", base_url=BASE_URL,
                         load_env=False, sleep=lambda _s: None, max_attempts=1)
    sent = []

    def post(payload):
        sent.append(payload)
        return transport(payload, len(sent))

    client._post_json = post
    return client, sent, store, policy, config_hash


def events(store, config_hash):
    return store.load(expected_config_hash=config_hash)["events"]


# ---------------------------------------------------------------- 1. success + settlement

def test_extractor_success_is_reserved_dispatched_and_settled(tmp_path):
    client, sent, store, _policy, chash = make_run(
        tmp_path, lambda p, n: ok_response(EXTRACTOR_TEXT))
    extractor = GuardedExtractorClient(client, label="E1")

    call = extractor.call("hello", "ext-5000:E1")

    assert call.status == "ok"
    assert call.parsed["coordination_attempt"] == "yes"
    assert len(sent) == 1
    event = events(store, chash)[0]
    assert event["state"] == "settled"
    assert event["reserved_micro"] == EXTRACTOR_RESERVED   # bound x $3/M + 400 x $15/M
    assert event["settled_micro"] == 600                   # 100 x $3/M + 20 x $15/M
    assert event["dispatched_ts"] is not None
    assert event["model"] == BOUND_MODEL


def test_extractor_prompt_and_message_stay_blind(tmp_path):
    client, sent, _store, _policy, _chash = make_run(
        tmp_path, lambda p, n: ok_response(EXTRACTOR_TEXT))
    GuardedExtractorClient(client).call("just the message text", "ext-5000:E1")
    payload = sent[0]
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    assert payload["messages"][1]["content"] == json.dumps("just the message text")
    assert payload["messages"][0]["content"].startswith("You annotate one message")
    assert payload["temperature"] == 0.0
    assert payload["max_tokens"] == EXTRACTOR_MAX_TOKENS
    assert payload["model"] == MODEL


# ---------------------------------------------------------------- 2. reserve before dispatch

def test_reservation_is_durable_before_transport_dispatch(tmp_path):
    seen = {}

    def transport(payload, n):
        ledger = json.loads((tmp_path / "spend" / "ledger.json").read_text(encoding="utf-8"))
        seen["events"] = ledger["events"]
        return ok_response(EXTRACTOR_TEXT)

    client, sent, _store, _policy, _chash = make_run(tmp_path, transport)
    GuardedExtractorClient(client).call("hello", "ext-5000:E1")

    assert sent, "the transport must not be reached before the reservation is on disk"
    assert len(seen["events"]) == 1
    assert seen["events"][0]["state"] == "reserved"
    assert seen["events"][0]["reserved_micro"] > 0
    assert seen["events"][0]["dispatched_ts"] is not None


# ---------------------------------------------------------------- 3. budget refusal

def test_insufficient_budget_prevents_dispatch(tmp_path):
    client, sent, store, _policy, chash = make_run(
        tmp_path, lambda p, n: ok_response(EXTRACTOR_TEXT), cap_usd=0.01)
    extractor = GuardedExtractorClient(client)

    with pytest.raises(AdmissionRefused) as excinfo:
        extractor.call("hello", "ext-5000:E1")

    assert sent == [], "nothing may be sent when the reservation is refused"
    assert events(store, chash) == [], "a refused reservation is never written"
    assert excinfo.value.amount_micro == EXTRACTOR_RESERVED
    assert extractor.calls[-1].status == "api_error"


# ---------------------------------------------------------------- 4. authentication failure

def test_http_401_is_unknown_and_never_retried(tmp_path):
    def transport(payload, n):
        raise urllib.error.HTTPError(BASE_URL, 401, "Unauthorized", {}, None)

    client, sent, store, _policy, chash = make_run(tmp_path, transport)
    extractor = GuardedExtractorClient(client)

    with pytest.raises(ApiError):
        extractor.call("hello", "ext-5000:E1")

    assert len(sent) == 1, "max_attempts=1 means exactly one transport attempt"
    event = events(store, chash)[0]
    assert event["state"] == "unknown"
    assert event["unknown_reason"] == "http_error_401"
    assert event["reserved_micro"] == EXTRACTOR_RESERVED, "an unknown outcome keeps the worst case"
    assert event["settled_micro"] is None
    assert extractor.calls[-1].failure_category == "http_401"


def test_provider_error_body_is_never_kept(tmp_path):
    def transport(payload, n):
        raise urllib.error.HTTPError(
            BASE_URL, 401, "Unauthorized", {},
            io.BytesIO(b'{"error":{"message":"leaky-provider-detail"}}'))

    client, _sent, store, _policy, chash = make_run(tmp_path, transport)
    extractor = GuardedExtractorClient(client)
    with pytest.raises(ApiError):
        extractor.call("hello", "ext-5000:E1")
    blob = json.dumps(extractor.calls[-1].to_dict())
    assert "leaky-provider-detail" not in blob and "HTTP " not in blob
    assert events(store, chash)[0]["unknown_reason"] == "http_error_401"


# ---------------------------------------------------------------- 5. timeout / connection loss

def test_timeout_is_recorded_and_keeps_the_reservation(tmp_path):
    def transport(payload, n):
        raise TimeoutError("read timed out")

    client, sent, store, _policy, chash = make_run(tmp_path, transport)
    extractor = GuardedExtractorClient(client)
    with pytest.raises(ApiError):
        extractor.call("hello", "ext-5000:E1")

    assert len(sent) == 1
    event = events(store, chash)[0]
    assert event["state"] == "unknown"
    assert event["unknown_reason"] == "transport_error_TimeoutError"
    assert event["reserved_micro"] == EXTRACTOR_RESERVED


def test_connection_error_is_recorded_as_a_transport_failure(tmp_path):
    def transport(payload, n):
        raise urllib.error.URLError("connection refused")

    client, sent, store, _policy, chash = make_run(tmp_path, transport)
    with pytest.raises(ApiError):
        GuardedExtractorClient(client).call("hello", "ext-5000:E1")
    assert len(sent) == 1
    assert events(store, chash)[0]["unknown_reason"] == "transport_error_URLError"


# ---------------------------------------------------------------- 6. missing / malformed usage

@pytest.mark.parametrize("usage", [None, {"prompt_tokens": "many", "completion_tokens": 1},
                                   {"input_tokens": -5, "output_tokens": 1}])
def test_missing_or_malformed_usage_is_unknown_not_zero(tmp_path, usage):
    client, sent, store, _policy, chash = make_run(
        tmp_path, lambda p, n: ok_response(EXTRACTOR_TEXT, usage=usage))
    extractor = GuardedExtractorClient(client)

    call = extractor.call("hello", "ext-5000:E1")

    assert call.status == "ok"
    assert call.usage_status == "unknown", "unusable usage never counts as reported"
    assert call.usage == usage, "the provider value is kept verbatim as evidence"
    assert len(sent) == 1
    event = events(store, chash)[0]
    assert event["state"] == "unknown"
    assert event["unknown_reason"] == "usage_missing_or_model_unpriced"
    assert event["reserved_micro"] == EXTRACTOR_RESERVED, "the worst case is retained, never zeroed"


def test_reported_usage_and_served_model_are_preserved_verbatim(tmp_path):
    client, _sent, _store, _policy, _chash = make_run(
        tmp_path, lambda p, n: ok_response(EXTRACTOR_TEXT))
    call = GuardedExtractorClient(client).call("hello", "ext-5000:E1")
    assert call.usage == USAGE
    assert call.served_model == MODEL
    assert call.served_provider == SERVED_PROVIDER


# ---------------------------------------------------------------- 7. malformed response

def test_unparseable_content_is_flagged_and_never_invented(tmp_path):
    client, sent, store, _policy, chash = make_run(
        tmp_path, lambda p, n: ok_response("I cannot help with that."))
    call = GuardedExtractorClient(client).call("hello", "ext-5000:E1")

    assert call.status == "malformed_response"
    assert call.parsed is None, "no claims may be invented from unusable content"
    assert call.text == "I cannot help with that."
    assert len(sent) == 1, "a content problem is not retried"
    assert events(store, chash)[0]["state"] == "settled", "the call happened and is billed"


def test_fenced_and_prefixed_json_is_parsed():
    assert parse_extraction("```json\n" + EXTRACTOR_TEXT + "\n```")["claims"][0]["target"] == "B"
    assert parse_extraction("Sure: " + EXTRACTOR_TEXT)["coordination_attempt"] == "yes"
    for bad in ("", "no json here", '{"claims": "not a list"}', "[1,2,3]"):
        with pytest.raises(ExtractorError):
            parse_extraction(bad)


# ---------------------------------------------------------------- 8. interruption

def test_interruption_between_dispatch_and_settlement_keeps_the_reservation(tmp_path):
    def transport(payload, n):
        raise KeyboardInterrupt()

    client, sent, store, _policy, chash = make_run(tmp_path, transport)
    extractor = GuardedExtractorClient(client)

    with pytest.raises(KeyboardInterrupt):
        extractor.call("hello", "ext-5000:E1")

    assert len(sent) == 1
    event = events(store, chash)[0]
    assert event["state"] == "reserved", "an interrupted call is not silently settled"
    assert event["dispatched_ts"] is not None
    assert commitments(store.load())["total"] == EXTRACTOR_RESERVED

    sweep_stale(store, chash, 0.001)
    after = store.load(expected_config_hash=chash)["events"][0]
    assert after["reserved_micro"] == EXTRACTOR_RESERVED, "recovery never releases the worst case"


# ---------------------------------------------------------------- 9. duplicate call ids

def test_duplicate_call_id_is_refused(tmp_path):
    client, sent, store, _policy, chash = make_run(
        tmp_path, lambda p, n: ok_response(EXTRACTOR_TEXT))
    extractor = GuardedExtractorClient(client)

    extractor.call("hello", "ext-5000:E1")
    with pytest.raises(DuplicateReservation):
        extractor.call("hello", "ext-5000:E1")

    assert len(sent) == 1, "the duplicate never reaches the transport"
    assert len(events(store, chash)) == 1


def test_a_guarded_call_requires_an_explicit_call_id(tmp_path):
    client, sent, _store, _policy, _chash = make_run(
        tmp_path, lambda p, n: ok_response(EXTRACTOR_TEXT))
    with pytest.raises(ExtractorError, match="call_id is required"):
        GuardedExtractorClient(client).call("hello")
    assert sent == []


# ---------------------------------------------------------------- 10. unique ids, end to end

def stage_transport(payload, n):
    """One stub for both callers: extractor system prompt, else an agent stage."""
    if payload["messages"][0]["content"].startswith("You annotate one message"):
        return ok_response(EXTRACTOR_TEXT)
    return ok_response(STAGE_TEXT)


def run_offline(tmp_path, manifest=None, transport=stage_transport):
    client, sent, store, policy, chash = make_run(tmp_path, transport)
    manifest = validate_manifest(manifest or manifest_for())
    extractors = [GuardedExtractorClient(client, label="E1"),
                  GuardedExtractorClient(client, label="E2")]
    log = ProvenanceLog(tmp_path / "provenance" / "calls.jsonl", run_id=manifest["run_id"])
    result = run_live(manifest=manifest, policy=policy, store=store, config_hash=chash,
                      agent_client=client, extractors=extractors, provenance=log,
                      out_dir=tmp_path / "corpus")
    return result, sent, store, chash, log, extractors


def test_run_live_writes_corpus_with_unique_call_ids(tmp_path):
    result, sent, store, chash, log, _ex = run_offline(tmp_path)
    summary = result["summary"]

    assert summary["planned_traces"] == 2
    assert summary["traces_generated"] == 2
    assert summary["labels"] == 2
    assert summary["excluded"] == 0
    assert summary["stopped"] is None
    assert summary["provenance_records"] == 11, "5 agent + 2 extractor for FREE_MSG, 4 for NO_MSG"

    ids = [row["call_id"] for row in log.records()]
    assert len(ids) == len(set(ids)), "call ids are unique across traces and stages"
    assert sorted(ids) == sorted(e["call_id"] for e in events(store, chash))
    assert "ext-5000:E1" in ids and "ext-5001:E1" not in ids  # 5001 is NO_MSG

    for row in log.records():
        assert row["dispatched"] is True
        assert row["settlement_state"] == "settled"
        assert row["reserved_micro"] > 0 and row["settled_micro"] is not None
        assert row["model"] == MODEL and row["provider"] == PROVIDER
        assert len(row["prompt_sha256"]) == 64
    assert {p["model"] for p in sent} == {MODEL}, "no other model is ever used"


def test_run_live_corpus_files_carry_the_manifest_and_hashes(tmp_path):
    result, _sent, _store, _chash, _log, _ex = run_offline(tmp_path)
    out = write_corpus(tmp_path / "written", result, validate_manifest(manifest_for()))
    assert set(out["files"]) == {"feas_v1_traces.jsonl", "feas_v1_labels.jsonl",
                                 "feas_v1_adjudication.jsonl"}
    for name in out["files"]:
        assert hashlib.sha256((tmp_path / "written" / name).read_bytes()).hexdigest() \
            == out["files"][name]
    assert out["summary"]["class_counts"], "labels are produced for every included trace"
    with pytest.raises(FileExistsError):
        write_corpus(tmp_path / "written", result, validate_manifest(manifest_for()))


# ---------------------------------------------------------------- 11. model/provider mismatch

def test_manifest_and_policy_must_name_the_same_model():
    manifest = validate_manifest(manifest_for())
    other_model = "anthropic/claude-opus-4.5"
    other = Policy.from_config({
        **policy_config(),
        "model": other_model,
        "prices": {f"{PROVIDER}/{other_model}": {"input_per_million": 5.0,
                                                 "output_per_million": 25.0}},
    })
    with pytest.raises(ManifestError, match="does not match the spend policy"):
        check_model_binding(manifest, other)


def test_manifest_and_policy_must_agree_on_the_cap():
    manifest = validate_manifest(manifest_for())
    with pytest.raises(ManifestError, match="does not match the policy cap"):
        check_model_binding(manifest, Policy.from_config(json.loads(policy_bytes(9.0))))


def test_run_live_refuses_a_retrying_or_mismatched_client(tmp_path):
    client, sent, store, policy, chash = make_run(tmp_path, stage_transport)
    manifest = validate_manifest(manifest_for())
    extractors = [GuardedExtractorClient(client, label="E1"),
                  GuardedExtractorClient(client, label="E2")]
    log = ProvenanceLog(tmp_path / "prov" / "calls.jsonl", run_id=RUN_ID)

    client.max_attempts = 2
    with pytest.raises(ManifestError, match="max_attempts"):
        run_live(manifest=manifest, policy=policy, store=store, config_hash=chash,
                 agent_client=client, extractors=extractors, provenance=log,
                 out_dir=tmp_path / "c1")
    assert sent == []


def test_extractor_max_tokens_must_match_the_manifest(tmp_path):
    client, sent, store, policy, chash = make_run(tmp_path, stage_transport)
    manifest = validate_manifest(manifest_for(extractor_max_tokens=250))
    extractors = [GuardedExtractorClient(client, label="E1"),
                  GuardedExtractorClient(client, label="E2")]
    log = ProvenanceLog(tmp_path / "prov2" / "calls.jsonl", run_id=RUN_ID)
    with pytest.raises(ManifestError, match="max_tokens"):
        run_live(manifest=manifest, policy=policy, store=store, config_hash=chash,
                 agent_client=client, extractors=extractors, provenance=log,
                 out_dir=tmp_path / "c2")
    assert sent == []


# ---------------------------------------------------------------- 12. no CLI override

def test_live_cli_rejects_cap_and_model_override_flags():
    for flag, value in (("--cap", "999"), ("--model", "x"), ("--provider", "y")):
        with pytest.raises(SystemExit) as excinfo:
            gen.main(["live", "--run-dir", "/tmp/nowhere", "--out-dir", "/tmp/out",
                      flag, value])
        assert excinfo.value.code == 2


def test_a_manifest_whose_scope_is_inconsistent_is_refused():
    # max_traces is the frozen count: a per_cell that contradicts it is refused,
    # so no command-line value can widen the plan.
    with pytest.raises(ManifestError, match="frozen plan"):
        validate_manifest(manifest_for(max_traces=5))
    with pytest.raises(ManifestError, match="frozen plan"):
        validate_manifest(manifest_for(per_cell=2, max_traces=2))
    with pytest.raises(ManifestError, match="max_attempts"):
        validate_manifest(manifest_for(max_attempts=3))
    with pytest.raises(ManifestError, match="missing"):
        validate_manifest({"run_id": "x"})


# ---------------------------------------------------------------- 13. durable provenance

def provenance_base(**overrides) -> dict:
    fields = dict(run_id=RUN_ID, trace_id="ext-5000", stage="E1", call_id="ext-5000:E1",
                  attempt=0, provider=PROVIDER, model=MODEL, prompt_sha256="a" * 64,
                  dispatched=True, reserved_micro=EXTRACTOR_RESERVED, usage=USAGE, settled_micro=600,
                  settlement_state="settled", ts="2026-10-22T00:00:00Z")
    fields.update(overrides)
    return make_record(**fields)


def test_provenance_records_every_required_field_for_success_and_failure(tmp_path):
    log = ProvenanceLog(tmp_path / "prov" / "calls.jsonl", run_id=RUN_ID)
    log.record(provenance_base())
    log.record(provenance_base(call_id="ext-5000:E2", dispatched=False, usage=None,
                               settled_micro=None, settlement_state="reserved",
                               failure_category="http_401"))

    rows = log.records()
    assert len(rows) == 2
    for row in rows:
        assert set(REQUIRED_FIELDS) <= set(row)
    assert rows[1]["usage"] is None and rows[1]["settled_micro"] is None
    assert rows[1]["failure_category"] == "http_401"

    with pytest.raises(ProvenanceError, match="missing"):
        log.record({"run_id": RUN_ID, "trace_id": "t"})
    with pytest.raises(ProvenanceError, match="does not match this log"):
        log.record({**provenance_base(), "run_id": "someone-else"})
    assert log.count == 2


def test_provenance_refuses_secrets_and_cannot_be_overwritten(tmp_path):
    path = tmp_path / "prov.jsonl"
    log = ProvenanceLog(path, run_id=RUN_ID)
    for bad in ({"api_key": "sk-abc"}, {"note": "Bearer sk-live-123"},
                {"authorization": "x"}):
        with pytest.raises(ProvenanceError):
            log.record({**provenance_base(), **bad})
    assert log.count == 0
    assert not path.exists(), "a refused record writes nothing at all"

    log.record(provenance_base())
    assert log.count == 1
    with pytest.raises(ProvenanceError, match="refusing to overwrite"):
        ProvenanceLog(path, run_id=RUN_ID)


# ---------------------------------------------------------------- 14. fake client unchanged

def test_fake_extractor_client_remains_compatible():
    client = FakeExtractorClient('{"claims": []}')
    assert client.complete("hello") == '{"claims": []}'
    assert client.complete("hello", "ext-5000:E1") == '{"claims": []}'
    assert len(client.requests) == 2
    assert client.requests[0].system == extractor_system()
    assert client.requests[0].prompt_sha256 == EXTRACTOR_PROMPT_SHA256
    assert client.requests[0].game_context_sha256 == GAME_CONTEXT_SHA256
    assert client.model == "fake-extractor"


def test_guarded_extractor_refuses_unguarded_and_retrying_clients(monkeypatch):
    monkeypatch.setenv("APART_API_KEY", "sk-test-value")
    bare = ModelClient(load_env=False, base_url=BASE_URL)
    with pytest.raises(ExtractorError, match="no LedgerGuard"):
        GuardedExtractorClient(bare)
    with pytest.raises(ExtractorError, match="ModelClient is required"):
        GuardedExtractorClient(FakeExtractorClient("{}"))
    assert "sk-test-value" not in repr(bare)


# ---------------------------------------------------------------- 15. CLI still gated

def write_run_dir(tmp_path, manifest=None, policy=None) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "policy.json").write_bytes(policy if policy is not None else policy_bytes())
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest or manifest_for()), encoding="utf-8")
    return run_dir


def test_live_cli_refuses_without_run_dir_or_out_dir():
    assert gen.main(["live", "--out-dir", "/tmp/out"]) == 2
    assert gen.main(["live", "--run-dir", "/tmp/nowhere"]) == 2


def test_live_cli_refuses_when_the_ledger_does_not_exist(tmp_path, capsys):
    run_dir = write_run_dir(tmp_path)
    assert gen.main(["live", "--run-dir", str(run_dir),
                     "--out-dir", str(tmp_path / "out")]) == 2
    assert "does not exist" in capsys.readouterr().err
    assert not (run_dir / "provenance").exists(), "nothing is started without a ledger"


def test_live_cli_refuses_a_policy_of_the_protected_config_shape(tmp_path, capsys):
    """configs/spend.json has no 'allocations' key: Policy.from_config must refuse it."""
    spend = json.loads((ROOT / "configs" / "spend.json").read_text(encoding="utf-8"))
    run_dir = write_run_dir(tmp_path, policy=json.dumps(spend).encode("utf-8"))
    assert gen.main(["live", "--run-dir", str(run_dir),
                     "--out-dir", str(tmp_path / "out")]) == 2
    assert "not usable" in capsys.readouterr().err


def test_live_cli_refuses_when_manifest_model_differs_from_policy(tmp_path):
    run_dir = write_run_dir(tmp_path, manifest=manifest_for(model="openai/gpt-5.4"))
    assert gen.main(["live", "--run-dir", str(run_dir),
                     "--out-dir", str(tmp_path / "out")]) == 2


def test_existing_generate_real_mode_is_still_refused(tmp_path, capsys):
    assert gen.main(["generate", "--ledger", str(tmp_path / "l.json"),
                     "--scores", str(tmp_path / "s.jsonl"), "--run-id", "r"]) == 2
    assert "not implemented" in capsys.readouterr().err


# ---------------------------------------------------------------- 16. frozen inputs

def test_frozen_inputs_and_labels_are_unchanged():
    pins = {
        "data/frozen/pilot_v2_traces.jsonl": "cf7615e107b1be03e9ddd23e1b280a611b30fc0489945412d321e736dee201ac",
        "data/frozen/pilot_v2_labels.jsonl": "22e6457632051f5d749d2409fe53d72d1b96da5fee248e9b495c983b22e1008c",
        "data/frozen/gate_v2_traces.jsonl": "0d2db6bb629733d45f960731f7750ace7a34f2c2db928a2a865f7262b6aa7059",
        "data/frozen/gate_v2_labels.jsonl": "e3879004ccf4b995e823f3823951460b85a3f4cf96d1d50b29a2219082185354",
    }
    for rel, digest in pins.items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest, rel


def test_protected_spend_config_is_armed_with_the_approved_caps():
    """Supersedes the earlier "protected config was not repaired" pin.

    The owner authorised arming the shared budget control on 2026-10-23. The pinned
    provider/model and pricing are unchanged; only the approved caps were added, and
    the shared $5.00 retry/failure reserve stays out of reach because no pair covers it.
    """
    spend = json.loads((ROOT / "configs" / "spend.json").read_text(encoding="utf-8"))
    assert spend["model"] == MODEL and spend["provider"] == PROVIDER
    assert spend["prices"][f"{PROVIDER}/{MODEL}"] == {"input_per_million": 3.0,
                                                      "output_per_million": 15.0}
    policy = Policy.from_config(spend)
    assert policy.pair_cap("pilot_audit", "gate") == 12_000_000
    assert policy.backstop_micro == 37_340_000
    assert ("pilot_audit", "smoke") not in policy.pair_caps
    assert ("pilot_audit", "extension") not in policy.pair_caps
    assert "sprint" not in policy.allocation_caps


# ---------------------------------------------------------------- 17. no retry, no fallback

def test_run_live_stops_on_the_first_transport_failure_without_retrying(tmp_path):
    def transport(payload, n):
        raise urllib.error.HTTPError(BASE_URL, 401, "Unauthorized", {}, None)

    client, sent, store, policy, chash = make_run(tmp_path, transport)
    manifest = validate_manifest(manifest_for())
    extractors = [GuardedExtractorClient(client, label="E1"),
                  GuardedExtractorClient(client, label="E2")]
    log = ProvenanceLog(tmp_path / "prov" / "calls.jsonl", run_id=RUN_ID)

    result = run_live(manifest=manifest, policy=policy, store=store, config_hash=chash,
                      agent_client=client, extractors=extractors, provenance=log,
                      out_dir=tmp_path / "corpus")
    summary = result["summary"]

    # s1, r1 and s2 each fail exactly once; s3/r2 are never reached because the
    # message stage failed closed. Nothing is retried and nothing is skipped.
    assert len(sent) == 3, "one attempt per reached stage, never a retry"
    assert summary["traces_generated"] == 0
    assert summary["stopped"] is not None
    assert summary["failures"] == 1
    assert len(events(store, chash)) == 3
    assert all(e["state"] == "unknown" for e in events(store, chash))
    assert all(e["reserved_micro"] > 0 for e in events(store, chash))
    assert [row["failure_category"] for row in log.records()] == ["api_error"] * 3
    assert log.count == 3


def test_run_live_never_uses_another_model(tmp_path):
    result, sent, _store, _chash, _log, _ex = run_offline(tmp_path)
    assert result["summary"]["model"] == MODEL
    assert result["summary"]["provider"] == PROVIDER
    assert {p["model"] for p in sent} == {MODEL}


# ---------------------------------------------------------------- 18. protected paths

def test_live_cli_refuses_a_protected_output_directory(tmp_path):
    run_dir = write_run_dir(tmp_path)
    for target in (ROOT / "results" / "nope", ROOT / "data" / "nope", ROOT / "docs"):
        assert gen.main(["live", "--run-dir", str(run_dir), "--out-dir", str(target)]) == 2


def test_run_artifacts_live_outside_every_protected_directory():
    protected = [p.resolve() for p in gen.LIVE_PROTECTED_DIRS]
    run_dir = (ROOT / "runs" / "ext-feas-v1-sonnet46").resolve()
    assert run_dir.is_dir()
    for path in protected:
        assert run_dir != path and path not in run_dir.parents
    for name in ("policy.json", "manifest.json"):
        assert (run_dir / name).is_file()
