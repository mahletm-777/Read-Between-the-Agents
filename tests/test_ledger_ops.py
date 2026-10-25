"""T3 S2a/S2b: ledger operations and pricing. tmp dirs and synthetic config only."""

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from monitors.ledger import (
    DuplicateReservation,
    LedgerError,
    LedgerStore,
    NOT_SENT_PERSISTENCE_UNCERTAIN,
    Policy,
    REQUEST_OUTCOME_UNCERTAIN,
    SETTLED,
    UNKNOWN,
    annotate_late_response,
    commitments,
    config_sha256,
    find_reservation,
    make_run_header,
    mark_dispatched,
    mark_unknown,
    open_run,
    reserve,
    settle_event,
    sweep_stale,
)

CONFIG = {
    "provider": "prov",
    "model": "m1",
    "prices": {"prov/m1": {"input_per_million": 3.0, "output_per_million": 15.0},
               "prov/m2": {"input_per_million": 0.15, "output_per_million": 0.6}},
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


@pytest.fixture
def store(tmp_path):
    s = LedgerStore(tmp_path / "spend" / "ledger.json")
    s.path.parent.mkdir()
    s.init(CONFIG_BYTES)
    open_run(s, POLICY, make_run_header("r1", "sprint", "extension", H, {}, "2026-10-21T00:00:00Z"), H, "MM")
    return s


def _reserve(store, call_id="c1", attempt=0, micro=10_000):
    return reserve(store, POLICY, H, "r1", call_id, attempt, "m1", micro)


# ---------------------------------------------------------------- S2a

def test_settle_and_mark_unknown_are_one_way(store):
    a = _reserve(store, "c1")
    b = _reserve(store, "c2")
    assert settle_event(store, H, a["event_id"], 3_000)["state"] == SETTLED
    assert mark_unknown(store, H, b["event_id"], "timeout")["unknown_reason"] == "timeout"
    for fn in (lambda: settle_event(store, H, a["event_id"], 1),
               lambda: mark_unknown(store, H, a["event_id"], "x"),
               lambda: settle_event(store, H, b["event_id"], 1),
               lambda: mark_unknown(store, H, b["event_id"], "x")):
        with pytest.raises(LedgerError, match="illegal transition"):
            fn()
    assert commitments(store.load())["total"] == 3_000 + 10_000


def test_duplicate_call_identity_never_creates_second_reservation(store):
    first = _reserve(store, "c1", 0)
    with pytest.raises(DuplicateReservation):
        _reserve(store, "c1", 0)
    second_attempt = _reserve(store, "c1", 1)
    events = store.load()["events"]
    assert [e["event_id"] for e in events] == [first["event_id"], second_attempt["event_id"]]
    assert find_reservation(store, "c1", 0)["event_id"] == first["event_id"]
    assert find_reservation(store, "c1", 2) is None


def test_stale_sweep_classifies_by_dispatch_evidence(store):
    never_sent = _reserve(store, "c1")
    dispatched = _reserve(store, "c2")
    mark_dispatched(store, H, dispatched["event_id"])
    later = datetime.now(timezone.utc) + timedelta(seconds=601)
    swept = sweep_stale(store, H, 600, now=later)
    reasons = {e["call_id"]: e["unknown_reason"] for e in swept}
    assert reasons == {"c1": NOT_SENT_PERSISTENCE_UNCERTAIN, "c2": REQUEST_OUTCOME_UNCERTAIN}
    ledger = store.load()
    assert all(e["state"] == UNKNOWN for e in ledger["events"])
    assert commitments(ledger)["total"] == 20_000          # worst case kept


def test_live_recent_reservation_is_unchanged(store):
    event = _reserve(store, "c1")
    assert sweep_stale(store, H, 600) == []                # this process is alive, age < 600 s
    assert store.load()["events"][0] == event


def test_dead_owner_makes_recent_reservation_stale(store):
    _reserve(store, "c1")
    swept = sweep_stale(store, H, 600, pid_alive=lambda pid: False)
    assert [e["unknown_reason"] for e in swept] == [NOT_SENT_PERSISTENCE_UNCERTAIN]


def test_dispatch_marker_rules(store):
    event = _reserve(store, "c1")
    mark_dispatched(store, H, event["event_id"])
    with pytest.raises(LedgerError, match="cannot be marked dispatched"):
        mark_dispatched(store, H, event["event_id"])


def test_late_response_is_annotation_only(store):
    event = _reserve(store, "c1")
    with pytest.raises(LedgerError, match="unknown event"):
        annotate_late_response(store, H, event["event_id"], "MM")
    mark_unknown(store, H, event["event_id"], REQUEST_OUTCOME_UNCERTAIN)
    before = copy.deepcopy(store.load()["events"])
    annotate_late_response(store, H, event["event_id"], "MM",
                           usage={"prompt_tokens": 1, "completion_tokens": 1}, response_id="resp-1")
    after = store.load()
    assert after["events"] == before
    assert after["audit"][-1]["type"] == "late_response"
    assert commitments(after)["total"] == 10_000


def test_ops_refuse_on_config_mismatch(store):
    with pytest.raises(LedgerError, match="binding"):
        reserve(store, POLICY, config_sha256(b"other"), "r1", "c9", 0, "m1", 1)
    with pytest.raises(LedgerError, match="binding"):
        sweep_stale(store, config_sha256(b"other"), 600)


# ---------------------------------------------------------------- S2b pricing

def test_worst_case_and_settled_cost_round_up():
    assert POLICY.worst_case_micro(1_000, 250) == 3_000 + 3_750
    assert POLICY.worst_case_micro(1, 0) == 3
    assert POLICY.cost_micro({"prompt_tokens": 1, "completion_tokens": 1}, "m2", None) == 1   # 0.75 µ$ -> 1
    assert POLICY.cost_micro({"input_tokens": 10, "output_tokens": 5}, "m1", "prov") == 105


def test_unpriced_or_bad_usage_returns_none():
    assert POLICY.cost_micro({"prompt_tokens": 1, "completion_tokens": 1}, "unpriced", None) is None
    assert POLICY.cost_micro(None, "m1", None) is None
    assert POLICY.cost_micro({"prompt_tokens": -1, "completion_tokens": 1}, "m1", None) is None
    assert POLICY.cost_micro({"prompt_tokens": True, "completion_tokens": 1}, "m1", None) is None


def test_upstream_provider_falls_back_to_gateway_price():
    assert POLICY.cost_micro({"prompt_tokens": 10, "completion_tokens": 5}, "m1", "Upstream Inc") == 105


def test_policy_requires_price_for_configured_model():
    cfg = copy.deepcopy(CONFIG)
    del cfg["prices"]["prov/m1"]
    with pytest.raises(LedgerError, match="has no price"):
        Policy.from_config(cfg)
    no_model = copy.deepcopy(CONFIG)
    del no_model["model"], no_model["provider"]
    with pytest.raises(LedgerError, match="no provider/model"):
        Policy.from_config(no_model).worst_case_micro(1, 1)
    bad = copy.deepcopy(CONFIG)
    bad["prices"]["prov/m1"]["input_per_million"] = -1
    with pytest.raises(LedgerError):
        Policy.from_config(bad)
