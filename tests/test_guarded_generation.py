"""Stage 4G: guarded agent generation — call identity, guard binding, provenance.

Offline only. The single network seam (``ModelClient._post_json``) is replaced by
a stub, so the real ``LedgerGuard`` reserve → dispatch → settle path is exercised
with no provider contact. ``urllib.request.urlopen`` is blocked as an autouse
guard as well.

Nothing is written inside the repository: the ledger, raw artifacts and every
output live under ``tmp_path``.
"""

import hashlib
import json
import urllib.error
from pathlib import Path

import pytest

from agents.client import FakeAgentClient
from agents.runner import (
    MAX_STAGE_ATTEMPTS,
    STATUS_API_ERROR,
    USAGE_REPORTED,
    USAGE_UNKNOWN,
    build_trace,
    run_trace,
    stage_call_id,
)
from apart.extension import select_monitored_set
from apart.labeler import TrajectoryClass, label_trajectory
from monitors.client import ApiError, FakeClient, LedgerGuard, ModelClient, SpendConfigError
from monitors.ledger import (
    AdmissionRefused,
    LedgerStore,
    Policy,
    config_sha256,
    make_run_header,
    open_run,
)
from monitors.runner import run_corpus, write_raw_response

ROOT = Path(__file__).resolve().parents[1]

# Documented values (configs/spend.json + pilot_parameters/extension_spec caps),
# expressed in the Policy schema. NOTE: configs/spend.json itself is protected and
# still lacks ``allocations``/``backstop_usd``/``stale_after_s``; the proposed diff
# is in the Stage 4G report and is deliberately NOT applied here.
PROVIDER = "openrouter"
MODEL = "anthropic/claude-sonnet-4.6"
BOUND_MODEL = f"{PROVIDER}/{MODEL}"
BASE_URL = "https://openrouter.ai/api/v1"
PHASE_CAPS = {"smoke": 0.10, "gate": 1.00, "pilot": 8.90, "extension": 30.00}
CONFIG = {
    "provider": PROVIDER,
    "model": MODEL,
    "prices": {BOUND_MODEL: {"input_per_million": 3.0, "output_per_million": 15.0}},
    "allocations": {
        "pilot_audit": {"cap_usd": 10.00,
                        "phase_caps_usd": {k: PHASE_CAPS[k] for k in ("smoke", "gate", "pilot")}},
        "sprint": {"cap_usd": 30.00,
                   "phase_caps_usd": {k: PHASE_CAPS[k] for k in ("smoke", "gate", "extension")}},
    },
    "backstop_usd": 50.0,
    "stale_after_s": 600,
}
CONFIG_BYTES = json.dumps(CONFIG, sort_keys=True).encode()
CONFIG_HASH = config_sha256(CONFIG_BYTES)
POLICY = Policy.from_config(CONFIG)

# One payload that satisfies every stage parser (§1).
STAGE_TEXT = json.dumps({"plan": "A", "note": "", "message": "hello", "action": "A"})


def ok_response(text, usage={"prompt_tokens": 10, "completion_tokens": 5}):
    body = {"choices": [{"message": {"content": text}}], "model": MODEL, "id": "resp-1"}
    if usage is not None:
        body["usage"] = usage
    return body


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr("urllib.request.urlopen", refuse)


def make_store(tmp_path, config_bytes=CONFIG_BYTES, config_hash=CONFIG_HASH, policy=POLICY,
               phase="smoke", allocation="sprint"):
    store = LedgerStore(tmp_path / "spend" / "ledger.json")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.init(config_bytes)
    header = make_run_header("gen-1", allocation, phase, config_hash, {},
                             "2026-10-22T00:00:00Z",
                             {"model": BOUND_MODEL, "base_url": BASE_URL})
    open_run(store, policy, header, config_hash, "MM")
    return store


def make_guarded_client(tmp_path, transport, **kwargs):
    """Real LedgerGuard + real ModelClient; only ``_post_json`` is stubbed."""
    store = make_store(tmp_path)
    guard = LedgerGuard(store, POLICY, CONFIG_HASH, "gen-1")
    client = ModelClient(guard=guard, api_key="sk-test-value", base_url=BASE_URL,
                         load_env=False, sleep=lambda _s: None, max_attempts=1, **kwargs)
    sent = []

    def post(payload):
        sent.append(payload)
        return transport(payload, len(sent))

    client._post_json = post
    return client, sent, store


# ---------------------------------------------------------------- requirements 1, 4

def test_fake_generation_still_works_offline():
    client = FakeAgentClient(lambda *_: STAGE_TEXT)
    result = run_trace(1000, "V0", "FREE_MSG", client)
    assert result["agent_model"] == "fake-agent"
    assert [c["call_id"] for c in client.calls] == [
        "ext-1000:s1:p1", "ext-1000:r1:p1", "ext-1000:s2:p1", "ext-1000:s3:p1", "ext-1000:r2:p1",
    ]
    trace = build_trace(result)
    assert trace.agent_status == "ok"
    assert len(trace.messages) == 1


def test_stage_call_ids_are_deterministic_and_unique():
    assert stage_call_id(1000, "s1") == "ext-1000:s1"
    assert stage_call_id(1000, "s1") == stage_call_id(1000, "s1")
    assert stage_call_id(1000, "s1") != stage_call_id(1001, "s1")
    assert stage_call_id(1000, "s1") != stage_call_id(1000, "r1")


def test_every_stage_call_identity_is_unique(tmp_path):
    client, _sent, store = make_guarded_client(tmp_path, lambda p, n: ok_response(STAGE_TEXT))
    result = run_trace(1000, "V0", "FREE_MSG", client)
    ids = [c["call_id"] for c in result["calls"]]
    assert ids == ["ext-1000:s1:p1", "ext-1000:r1:p1", "ext-1000:s2:p1",
                   "ext-1000:s3:p1", "ext-1000:r2:p1"]
    assert len(set(ids)) == len(ids)
    ledger = store.load(expected_config_hash=CONFIG_HASH, policy=POLICY)
    assert sorted(e["call_id"] for e in ledger["events"]) == sorted(ids)


# ---------------------------------------------------------------- requirements 2, 3

def test_guarded_agent_request_requires_a_valid_guard(monkeypatch):
    monkeypatch.setenv("APART_API_KEY", "sk-test-value")
    client = ModelClient(load_env=False, base_url=BASE_URL)
    assert client.guard is None
    with pytest.raises(SpendConfigError, match="no LedgerGuard"):
        client.call_model([{"role": "user", "content": "x"}], 0.0, max_tokens=8,
                          call_id="ext-1000:s1:p1")


def test_missing_call_id_is_rejected_and_impossible_via_generation(tmp_path):
    client, sent, _ = make_guarded_client(tmp_path, lambda p, n: ok_response(STAGE_TEXT))
    with pytest.raises(SpendConfigError, match="call_id is required"):
        client.call_model([{"role": "user", "content": "x"}], 0.0, max_tokens=8)
    assert sent == []
    run_trace(1000, "V0", "NO_MSG", client)
    assert sent, "run_trace must always supply call_id"


def test_a_non_guard_object_is_rejected_by_model_client(tmp_path):
    """Only a LedgerGuard may authorize requests; anything else is refused on construction."""
    store = make_store(tmp_path)
    with pytest.raises(SpendConfigError):
        ModelClient(guard=store, api_key="k", load_env=False)


# ---------------------------------------------------------------- requirement 5

def test_sender_and_receiver_share_one_configured_model(tmp_path):
    client, sent, _ = make_guarded_client(tmp_path, lambda p, n: ok_response(STAGE_TEXT))
    result = run_trace(1001, "V1", "FREE_MSG", client)
    assert result["sender"] != result["receiver"]
    assert client.model == MODEL
    assert result["agent_model"] == MODEL
    assert {c["requested_model"] for c in result["calls"]} == {MODEL}
    assert {p["model"] for p in sent} == {MODEL}
    assert build_trace(result).agent_model == MODEL


def test_payload_model_slug_and_ledger_key_come_from_one_config_entry(tmp_path):
    """The request payload carries the bare slug; pricing/provenance use the
    provider-qualified key. Both derive from the same spend-config entry, so the
    payload can never name a model the guard is not pricing."""
    client, sent, store = make_guarded_client(tmp_path, lambda p, n: ok_response(STAGE_TEXT))
    run_trace(1000, "V0", "NO_MSG", client)
    assert {p["model"] for p in sent} == {MODEL}          # bare slug in the payload
    ledger = store.load(expected_config_hash=CONFIG_HASH, policy=POLICY)
    assert {e["model"] for e in ledger["events"]} == {BOUND_MODEL}   # provider-qualified


def test_model_must_match_the_guard_binding(tmp_path):
    store = make_store(tmp_path)
    guard = LedgerGuard(store, POLICY, CONFIG_HASH, "gen-1")
    with pytest.raises(SpendConfigError, match="does not match the bound spend config"):
        ModelClient(guard=guard, api_key="k", load_env=False, base_url=BASE_URL,
                    model="some/other-model")


# ---------------------------------------------------------------- requirements 6, 7

def test_reservation_is_durable_before_dispatch(tmp_path):
    snapshots = []
    holder = {}

    def transport(payload, n):
        snapshots.append(json.loads(holder["store"].path.read_text()))
        return ok_response(STAGE_TEXT)

    client, _sent, store = make_guarded_client(tmp_path, transport)
    holder["store"] = store
    run_trace(1000, "V0", "NO_MSG", client)
    first = snapshots[0]
    assert len(first["events"]) == 1, "the reservation must exist before the request is sent"
    assert first["events"][0]["model"] == BOUND_MODEL
    assert first["events"][0]["reserved_micro"] > 0


def test_reservation_failure_prevents_dispatch(tmp_path):
    tiny = json.loads(json.dumps(CONFIG))
    tiny["allocations"]["sprint"]["phase_caps_usd"]["smoke"] = 0.000001  # 1 microdollar
    tiny_bytes = json.dumps(tiny, sort_keys=True).encode()
    tiny_hash = config_sha256(tiny_bytes)
    tiny_policy = Policy.from_config(tiny)
    store = make_store(tmp_path, config_bytes=tiny_bytes, config_hash=tiny_hash, policy=tiny_policy)
    guard = LedgerGuard(store, tiny_policy, tiny_hash, "gen-1")
    client = ModelClient(guard=guard, api_key="k", base_url=BASE_URL, load_env=False,
                         sleep=lambda _s: None, max_attempts=1)
    sent = []
    client._post_json = lambda payload: (sent.append(payload), ok_response(STAGE_TEXT))[1]
    with pytest.raises(AdmissionRefused):
        run_trace(1000, "V0", "NO_MSG", client)
    assert sent == [], "a refused reservation must send nothing"


# ---------------------------------------------------------------- requirements 8, 10, 11

def test_transport_error_surfaces_as_api_error_without_network(tmp_path):
    def transport(payload, n):
        raise urllib.error.URLError("boom")

    client, sent, _ = make_guarded_client(tmp_path, transport)
    with pytest.raises(ApiError):
        client.call_model([{"role": "user", "content": "x"}], 0.0, max_tokens=8,
                          call_id="ext-1000:s1:p1")
    assert len(sent) == 1, "max_attempts=1 must mean exactly one dispatch attempt"


def test_api_error_fails_the_stage_and_the_trace_is_not_valid(tmp_path):
    def transport(payload, n):
        raise urllib.error.URLError("boom")

    client, sent, _ = make_guarded_client(tmp_path, transport)
    result = run_trace(1000, "V0", "FREE_MSG", client)
    assert result["s1"]["status"] == STATUS_API_ERROR
    assert result["s1"]["reason"] == "api_error"
    # S3/R2 are skipped once S2 fails, so exactly S1, R1 and S2 were attempted.
    assert len(sent) == 3
    trace = build_trace(result)
    assert trace.agent_status != "ok"
    truth = label_trajectory(trace)
    assert truth.trajectory_class is TrajectoryClass.AMBIGUOUS
    assert truth.in_taxonomy is False


def test_malformed_response_retries_then_fails_closed(tmp_path):
    client, sent, store = make_guarded_client(tmp_path, lambda p, n: ok_response("not json"))
    result = run_trace(1000, "V0", "NO_MSG", client)
    assert result["s1"]["status"] == "agent_refusal"
    assert result["s1"]["reason"] == "parse_retries_exhausted"
    s1_ids = [c["call_id"] for c in result["calls"] if c["call_id"].startswith("ext-1000:s1")]
    assert s1_ids == [f"ext-1000:s1:p{i}" for i in range(1, MAX_STAGE_ATTEMPTS + 1)]
    assert len(sent) == 4 * MAX_STAGE_ATTEMPTS, "4 stages x 3 parse attempts"
    ledger = store.load(expected_config_hash=CONFIG_HASH, policy=POLICY)
    assert len(ledger["events"]) == 4 * MAX_STAGE_ATTEMPTS


def test_unknown_usage_is_recorded_as_unknown_not_zero(tmp_path):
    client, _sent, store = make_guarded_client(
        tmp_path, lambda p, n: ok_response(STAGE_TEXT, usage=None))
    result = run_trace(1000, "V0", "NO_MSG", client)
    assert result["calls"], "expected a call log"
    for entry in result["calls"]:
        assert entry["usage"] is None
        assert entry["usage_status"] == USAGE_UNKNOWN
    ledger = store.load(expected_config_hash=CONFIG_HASH, policy=POLICY)
    unknown = [e for e in ledger["events"] if e["state"] == "unknown"]
    assert unknown, "usage-less responses settle as unknown, never as zero cost"
    assert all(e["reserved_micro"] > 0 for e in unknown)


def test_reported_usage_is_preserved_verbatim(tmp_path):
    client, _sent, _store = make_guarded_client(tmp_path, lambda p, n: ok_response(STAGE_TEXT))
    result = run_trace(1000, "V0", "NO_MSG", client)
    for entry in result["calls"]:
        assert entry["usage"] == {"prompt_tokens": 10, "completion_tokens": 5}
        assert entry["usage_status"] == USAGE_REPORTED


def test_call_log_carries_stage_and_trace_provenance(tmp_path):
    client, _sent, _store = make_guarded_client(tmp_path, lambda p, n: ok_response(STAGE_TEXT))
    result = run_trace(1007, "V0", "NO_MSG", client)
    for entry in result["calls"]:
        assert entry["trace_id"] == "ext-1007"
        assert entry["stage"] in {"plan", "message", "action"}
        assert entry["response_id"] == "resp-1"
        assert entry["served_model"] == MODEL
        assert entry["requested_model"] == MODEL
        assert isinstance(entry["prompt_sha256"], str) and len(entry["prompt_sha256"]) == 64
        assert entry["temperature"] == 1.0
        assert entry["max_tokens"] > 0


def test_raw_outputs_cannot_be_overwritten(tmp_path):
    first = write_raw_response(tmp_path / "raw", 1000, "M1", 0, ["a"])
    assert json.loads(first.read_text(encoding="utf-8"))["responses"] == ["a"]
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_raw_response(tmp_path / "raw", 1000, "M1", 0, ["b"])
    assert json.loads(first.read_text(encoding="utf-8"))["responses"] == ["a"]


def test_monitor_run_raw_output_is_isolated_per_directory(tmp_path):
    trace = build_trace(run_trace(1000, "V0", "NO_MSG",
                                  FakeAgentClient(lambda *_: STAGE_TEXT)))
    raw_a = tmp_path / "run_a" / "raw"
    records, _ = run_corpus([trace], FakeClient(), created_utc="2026-10-22T00:00:00Z",
                            raw_dir=raw_a)
    assert records and all(r["status"] == "ok" for r in records)
    assert any(raw_a.iterdir())
    with pytest.raises(FileExistsError):
        run_corpus([trace], FakeClient(), created_utc="2026-10-22T00:00:00Z", raw_dir=raw_a)
    records_b, _ = run_corpus([trace], FakeClient(), created_utc="2026-10-22T00:00:00Z",
                              raw_dir=tmp_path / "run_b" / "raw")
    assert records_b == records


def test_frozen_inputs_and_labels_are_unchanged():
    pins = {
        "data/frozen/pilot_v2_traces.jsonl": "cf7615e107b1be03e9ddd23e1b280a611b30fc0489945412d321e736dee201ac",
        "data/frozen/pilot_v2_labels.jsonl": "22e6457632051f5d749d2409fe53d72d1b96da5fee248e9b495c983b22e1008c",
        "data/frozen/gate_v2_traces.jsonl": "0d2db6bb629733d45f960731f7750ace7a34f2c2db928a2a865f7262b6aa7059",
        "data/frozen/gate_v2_labels.jsonl": "e3879004ccf4b995e823f3823951460b85a3f4cf96d1d50b29a2219082185354",
    }
    for rel, digest in pins.items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest, rel


def test_selection_is_deterministic_and_monitor_blind():
    pool = [{"seed": s, "class": "C-01", "attempt_type": "NONE", "outcome": "A/A"}
            for s in (1002, 1000, 1001)]
    forwards = select_monitored_set(pool, class_quotas={"C-01": 2}, cell_targets={})
    backwards = select_monitored_set(list(reversed(pool)), class_quotas={"C-01": 2},
                                     cell_targets={})
    assert forwards["selected_seeds"] == backwards["selected_seeds"] == [1000, 1001]
    assert forwards["shortfalls"] == []
