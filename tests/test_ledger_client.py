"""T3 S2c/S2d: ModelClient authorized by LedgerGuard. Stubbed transport, tmp ledger, no network."""

import copy
import io
import json
import urllib.error

import pytest

import monitors.ledger as L
from monitors.client import ApiError, LedgerGuard, ModelClient, SpendConfigError, SpendGuard
from monitors.ledger import (
    AdmissionRefused,
    DuplicateReservation,
    LedgerError,
    LedgerStore,
    Policy,
    REQUEST_OUTCOME_UNCERTAIN,
    commitments,
    config_sha256,
    make_run_header,
    open_run,
    sweep_stale,
)

BASE_URL = "https://example.invalid/v1"
CONFIG = {
    "provider": "prov",
    "model": "m1",
    "prices": {"prov/m1": {"input_per_million": 3.0, "output_per_million": 15.0}},
    "allocations": {
        "pilot_audit": {"cap_usd": 8.40, "phase_caps_usd": {"smoke": 0.10, "gate": 1.00, "pilot": 8.90}},
        "sprint": {"cap_usd": 40.00, "phase_caps_usd": {"smoke": 0.10, "gate": 1.00, "extension": 30.00}},
    },
    "backstop_usd": 50.0,
    "stale_after_s": 600,
}
CONFIG_BYTES = json.dumps(CONFIG, sort_keys=True).encode()
H = config_sha256(CONFIG_BYTES)
POLICY = Policy.from_config(CONFIG)
OK = {"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5},
      "model": "m1", "id": "resp-1"}
MSG = [{"role": "user", "content": "hello"}]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr("urllib.request.urlopen", refuse)


def make_store(tmp_path, phase="extension", provenance=None):
    store = LedgerStore(tmp_path / "spend" / "ledger.json")
    store.path.parent.mkdir()
    store.init(CONFIG_BYTES)
    prov = provenance or {"model": "prov/m1", "base_url": BASE_URL}
    open_run(store, POLICY, make_run_header("r1", "sprint", phase, H, {}, "2026-10-21T00:00:00Z", prov), H, "MM")
    return store


def make_client(store, transport, **kwargs):
    guard = LedgerGuard(store, POLICY, H, "r1")
    sleeps = []
    client = ModelClient(guard=guard, api_key="sk-secret-value", base_url=BASE_URL, load_env=False,
                         sleep=sleeps.append, **kwargs)
    calls = []

    def post(payload):
        calls.append(payload)
        return transport(payload, len(calls))

    client._post_json = post
    return client, calls, sleeps


def http_error(code):
    return urllib.error.HTTPError("https://example.invalid", code, "err", {}, io.BytesIO(b"x"))


# ---------------------------------------------------------------- send ordering

def test_reservation_persistence_failure_sends_nothing(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    client, calls, _ = make_client(store, lambda p, n: OK)
    before = store.path.read_bytes()
    monkeypatch.setattr(L.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(LedgerError, match="write failed"):
        client.call_model(MSG, 0.0, max_tokens=50, call_id="r1:1:M1:0:p1")
    monkeypatch.undo()
    assert calls == []
    assert store.path.read_bytes() == before


def test_dispatch_marker_failure_sends_nothing(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    client, calls, _ = make_client(store, lambda p, n: OK)
    real = L.mark_dispatched

    def failing(*a, **k):
        raise LedgerError("ledger write failed: simulated")

    monkeypatch.setattr("monitors.client.mark_dispatched", failing)
    with pytest.raises(LedgerError):
        client.call_model(MSG, 0.0, max_tokens=50, call_id="c")
    assert calls == []
    monkeypatch.setattr("monitors.client.mark_dispatched", real)
    # the leftover reservation was never dispatched -> swept as not sent, worst case kept
    swept = sweep_stale(store, H, 600, pid_alive=lambda pid: False)
    assert [e["unknown_reason"] for e in swept] == ["not_sent_persistence_uncertain"]


def test_admission_refusal_sends_nothing(tmp_path):
    store = make_store(tmp_path, phase="smoke")          # 0.10 USD phase cap
    client, calls, _ = make_client(store, lambda p, n: OK)
    with pytest.raises(AdmissionRefused, match="phase cap"):
        client.call_model(MSG, 0.0, max_tokens=10_000, call_id="c")   # 0.15 USD worst case
    assert calls == []
    assert store.load()["events"] == []


def test_settlement_persistence_failure_stops_and_stays_conservative(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    client, calls, _ = make_client(store, lambda p, n: OK)

    def failing(*a, **k):
        raise LedgerError("ledger write failed: simulated")

    monkeypatch.setattr("monitors.client.settle_event", failing)
    with pytest.raises(LedgerError):
        client.call_model(MSG, 0.0, max_tokens=50, call_id="c")
    assert len(calls) == 1
    event = store.load()["events"][0]
    assert event["state"] == "reserved" and event["dispatched_ts"]
    swept = sweep_stale(store, H, 600, pid_alive=lambda pid: False)
    assert swept[0]["unknown_reason"] == REQUEST_OUTCOME_UNCERTAIN
    assert commitments(store.load())["total"] == event["reserved_micro"]


def test_duplicate_call_identity_no_second_reservation(tmp_path):
    store = make_store(tmp_path)
    client, calls, _ = make_client(store, lambda p, n: OK)
    client.call_model(MSG, 0.0, max_tokens=50, call_id="r1:1:M1:0:p1")
    with pytest.raises(DuplicateReservation):
        client.call_model(MSG, 0.0, max_tokens=50, call_id="r1:1:M1:0:p1")
    assert len(calls) == 1
    assert len(store.load()["events"]) == 1


# ---------------------------------------------------------------- retries and outcomes

def test_http_retries_have_distinct_attempts_and_reservations(tmp_path):
    def transport(payload, n):
        if n == 1:
            raise http_error(429)
        if n == 2:
            raise TimeoutError("slow")
        return OK

    store = make_store(tmp_path)
    client, calls, sleeps = make_client(store, transport)
    response = client.call_model(MSG, 0.0, max_tokens=50, call_id="c")
    assert response.text == "{}" and len(calls) == 3 and len(sleeps) == 2
    events = store.load()["events"]
    assert [(e["call_id"], e["attempt"], e["state"]) for e in events] == [
        ("c", 0, "unknown"), ("c", 1, "unknown"), ("c", 2, "settled")]
    assert [e["unknown_reason"] for e in events[:2]] == ["http_error_429", "transport_error_TimeoutError"]
    assert commitments(store.load())["total"] == events[0]["reserved_micro"] + events[1]["reserved_micro"] + 105


def test_four_http_errors_raise_api_error(tmp_path):
    store = make_store(tmp_path)
    client, calls, sleeps = make_client(store, lambda p, n: (_ for _ in ()).throw(http_error(500)))
    with pytest.raises(ApiError, match="after 4 attempts"):
        client.call_model(MSG, 0.0, max_tokens=50, call_id="c")
    assert len(calls) == 4 and len(sleeps) == 3
    assert [e["attempt"] for e in store.load()["events"]] == [0, 1, 2, 3]


def test_missing_usage_and_unpriced_model_stay_unknown(tmp_path):
    replies = [dict(OK, usage=None), dict(OK, model="unpriced-model")]
    store = make_store(tmp_path)
    client, calls, _ = make_client(store, lambda p, n: replies[n - 1])
    client.call_model(MSG, 0.0, max_tokens=50, call_id="a")
    client.call_model(MSG, 0.0, max_tokens=50, call_id="b")
    events = store.load()["events"]
    assert [e["state"] for e in events] == ["unknown", "unknown"]
    assert {e["unknown_reason"] for e in events} == {"usage_missing_or_model_unpriced"}
    assert commitments(store.load())["total"] == sum(e["reserved_micro"] for e in events)


def test_malformed_body_is_unknown_and_retried(tmp_path):
    replies = [{"no": "choices"}, OK]
    store = make_store(tmp_path)
    client, calls, _ = make_client(store, lambda p, n: replies[n - 1])
    client.call_model(MSG, 0.0, max_tokens=50, call_id="c")
    assert [e["unknown_reason"] for e in store.load()["events"]] == ["malformed_response", None]


# ---------------------------------------------------------------- authorization and config

def test_old_spend_guard_is_rejected(tmp_path):
    from monitors.client import ModelPrice, SpendConfig

    config = SpendConfig(provider="p", model="m", phase_caps_usd={"smoke": 1.0},
                         prices={"p/m": ModelPrice(1.0, 1.0)})
    legacy = SpendGuard(config, "smoke", tmp_path / "legacy.json", "run")
    with pytest.raises(SpendConfigError, match="legacy SpendGuard"):
        ModelClient(guard=legacy, api_key="sk", load_env=False)
    with pytest.raises(TypeError):
        ModelClient(spend_guard=legacy, api_key="sk", load_env=False)
    with pytest.raises(SpendConfigError, match="LedgerGuard"):
        ModelClient(guard=object(), api_key="sk", load_env=False)


def test_model_comes_only_from_bound_config(tmp_path, monkeypatch):
    monkeypatch.setenv("APART_MODEL", "attacker-model")
    store = make_store(tmp_path)
    client, calls, _ = make_client(store, lambda p, n: OK)
    client.call_model(MSG, 0.0, max_tokens=50, call_id="c")
    assert client.model == "m1" and calls[0]["model"] == "m1"
    with pytest.raises(SpendConfigError, match="bound spend config"):
        ModelClient(guard=LedgerGuard(store, POLICY, H, "r1"), api_key="sk", base_url=BASE_URL,
                    model="other", load_env=False)


def test_endpoint_must_match_provenance_and_be_safe(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    guard = LedgerGuard(store, POLICY, H, "r1")
    with pytest.raises(LedgerError, match="endpoint differs"):
        ModelClient(guard=guard, api_key="sk", base_url="https://other.invalid/v1", load_env=False)
    monkeypatch.setenv("APART_BASE_URL", "https://other.invalid/v1")
    with pytest.raises(LedgerError, match="endpoint differs"):
        ModelClient(guard=guard, api_key="sk", load_env=False)
    for bad in ("http://example.com/v1", "https://user:pw@example.invalid/v1", "ftp://x", "https://x/v1?k=1"):
        with pytest.raises(SpendConfigError):
            ModelClient(api_key="sk", base_url=bad, load_env=False)


def test_guard_requires_open_run_matching_config_and_provenance(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(LedgerError, match="not open"):
        LedgerGuard(store, POLICY, H, "missing-run")
    with pytest.raises(LedgerError, match="binding"):
        LedgerGuard(store, POLICY, config_sha256(b"other"), "r1")
    other = tmp_path / "b"
    other.mkdir()
    bad_prov = make_store(other, provenance={"model": "prov/other", "base_url": BASE_URL})
    with pytest.raises(LedgerError, match="bound model"):
        LedgerGuard(bad_prov, POLICY, H, "r1")


def test_unguarded_or_unidentified_calls_refused(tmp_path):
    client = ModelClient(api_key="sk", load_env=False)
    with pytest.raises(SpendConfigError, match="no LedgerGuard"):
        client.call_model(MSG, 0.0)
    store = make_store(tmp_path)
    guarded, calls, _ = make_client(store, lambda p, n: OK)
    with pytest.raises(SpendConfigError, match="call_id"):
        guarded.call_model(MSG, 0.0)
    assert calls == []


def test_api_key_never_reaches_the_ledger(tmp_path):
    store = make_store(tmp_path)
    client, _, _ = make_client(store, lambda p, n: OK)
    client.call_model(MSG, 0.0, max_tokens=50, call_id="c")
    assert "sk-secret-value" not in store.path.read_text()
    assert "sk-secret-value" not in repr(client)


def test_provider_order_is_sent_in_payload(tmp_path):
    store = make_store(tmp_path)
    client, calls, _ = make_client(store, lambda p, n: OK, provider_order=["provider-a", "provider-b"])
    client.call_model(MSG, 0.0, max_tokens=10, call_id="c")
    assert calls[0]["provider"] == {"order": ["provider-a", "provider-b"]}


# ---------------------------------------------------------------- S2d runner

def _one_trace():
    import generate_corpus as gen

    return [gen.build_trace(gen.PILOT_ROWS[0])]


SCORE = dict(OK, choices=[{"message": {"content": '{"score": 50, "rationale": "ok"}'}}])
CHECKLIST = dict(OK, choices=[{"message": {"content": json.dumps(
    {"checklist": {"messages": [], "speaker_match": [], "receiver_compliance": []}, "rationale": "ok", "score": 50})}}])


def _scoring_transport(payload, n):
    return CHECKLIST if "CHECKLIST" in payload["messages"][-1]["content"] else SCORE


def test_runner_passes_deterministic_unique_call_ids(tmp_path):
    from monitors.runner import run_corpus

    store = make_store(tmp_path)
    client, calls, _ = make_client(store, _scoring_transport)
    records, _ = run_corpus(_one_trace(), client, created_utc="2026-10-21T00:00:00Z")
    events = store.load()["events"]
    ids = [e["call_id"] for e in events]
    assert len(ids) == len(set(ids)) == len(records) == 10
    assert all(e["state"] == "settled" and e["attempt"] == 0 for e in events)
    seed = _one_trace()[0].seed
    assert f"r1:{seed}:M1:0:p1" in ids and f"r1:{seed}:M3-ens:4:p1" in ids


def test_parse_retry_is_a_new_logical_call(tmp_path):
    from monitors.runner import _call_with_retries

    replies = [dict(OK, choices=[{"message": {"content": "not json"}}]), SCORE]
    store = make_store(tmp_path)
    client, calls, _ = make_client(store, lambda p, n: replies[n - 1], max_attempts=1)
    base = [{"role": "system", "content": "s"}, {"role": "user", "content": "CASE case-x"}]
    parsed, attempts, _, status = _call_with_retries(client, base, "M1", 0.0, 0, 250, call_prefix="r1:1:M1:0")
    assert status == "ok" and attempts == 2
    assert [e["call_id"] for e in store.load()["events"]] == ["r1:1:M1:0:p1", "r1:1:M1:0:p2"]


def test_ledger_failure_stops_the_corpus(tmp_path):
    from monitors.runner import run_corpus

    store = make_store(tmp_path, phase="smoke")      # 0.10 USD phase cap
    # no usage reported -> each event keeps its worst case, so the cap is reached mid-corpus
    client, calls, _ = make_client(store, lambda p, n: dict(_scoring_transport(p, n), usage=None))
    with pytest.raises(AdmissionRefused):
        run_corpus(_one_trace(), client, created_utc="2026-10-21T00:00:00Z")
    sent = len(calls)
    assert sent == len(store.load()["events"])      # every send had a reservation; none after the stop
    assert sent < 10


# ---------------------------------------------------------------- S2f legacy guard retired

def test_no_production_path_uses_the_legacy_spend_guard():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    production = [root / "scripts" / "run_monitors.py", root / "monitors" / "runner.py",
                  *sorted((root / "agents").glob("*.py")), *sorted((root / "extraction").glob("*.py")),
                  *sorted((root / "scripts").glob("*.py"))]
    for path in production:
        assert not re.search(r"\bSpendGuard\b", path.read_text()), path
    client_src = (root / "monitors" / "client.py").read_text()
    call_model = client_src[client_src.index("class ModelClient"):client_src.index("def _post_json")]
    assert "spend_guard" not in call_model and "LedgerGuard" in call_model


def test_v1_ledgers_are_readable_without_mutation(tmp_path):
    from monitors.client import read_v1_ledger

    v1 = {"version": 1, "phases": {"gate": {"requests": 60, "spent_usd": 0.192792}},
          "runs": {"run-x": {"phase": "gate", "requests": 60, "spent_usd": 0.192792}}}
    path = tmp_path / "spend_guard.json"
    path.write_text(json.dumps(v1))
    before = path.read_bytes()
    assert read_v1_ledger(path)["runs"]["run-x"]["spent_usd"] == 0.192792
    assert path.read_bytes() == before and sorted(p.name for p in tmp_path.iterdir()) == ["spend_guard.json"]
    with pytest.raises(SpendConfigError):
        read_v1_ledger(tmp_path / "missing.json")
    assert not (tmp_path / "missing.json").exists()
