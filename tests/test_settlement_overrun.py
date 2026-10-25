"""Stage 4N: settlement-versus-reservation accounting (offline, no network).

Covers the settlement invariant added to ``monitors.ledger``: an amount above the
reservation becomes a distinct ``overrun`` state with a ``budget_incident`` audit
record, the real reported charge is kept in full, and no further request can be
authorized afterwards.

Every test is offline. ``urllib.request.urlopen`` is blocked as an autouse guard
and nothing writes inside the repository.
"""

import json
import threading

import pytest

from monitors.client import LedgerGuard, ModelClient
from monitors.ledger import (
    OVERRUN,
    RESERVED,
    SETTLED,
    UNKNOWN,
    BudgetIncident,
    LedgerError,
    LedgerStore,
    Policy,
    budget_incidents,
    commitments,
    config_sha256,
    contribution,
    headroom,
    make_correction,
    make_run_header,
    open_run,
    reserve,
    seal,
    settle_event,
    transition,
    validate,
)

PROVIDER, MODEL = "openrouter", "anthropic/claude-sonnet-4.6"
BOUND = f"{PROVIDER}/{MODEL}"
BASE_URL = "https://openrouter.ai/api/v1"
RUN_ID = "stage4n-overrun"
TS = "2026-10-23T00:00:00Z"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr("urllib.request.urlopen", refuse)


def policy_cfg(cap_usd=1.0):
    return {
        "provider": PROVIDER, "model": MODEL,
        "prices": {BOUND: {"input_per_million": 3.0, "output_per_million": 15.0}},
        "allocations": {"sprint": {"cap_usd": cap_usd,
                                   "phase_caps_usd": {"extension": cap_usd}}},
        "backstop_usd": cap_usd, "stale_after_s": 600,
    }


def make_store(tmp_path, cap_usd=1.0, run_id=RUN_ID):
    cfg = policy_cfg(cap_usd)
    payload = json.dumps(cfg).encode("utf-8")
    h = config_sha256(payload)
    policy = Policy.from_config(cfg)
    store = LedgerStore(tmp_path / "spend" / "ledger.json")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.init(payload)
    open_run(store, policy,
             make_run_header(run_id, "sprint", "extension", h, {}, TS,
                             {"model": BOUND, "base_url": BASE_URL}), h, "MM")
    return store, policy, h


def open_other_run(store, policy, h, run_id):
    open_run(store, policy,
             make_run_header(run_id, "sprint", "extension", h, {}, TS,
                             {"model": BOUND, "base_url": BASE_URL}), h, "MM")


def reserve_one(store, policy, h, call_id="c1", amount=1000, run_id=RUN_ID):
    return reserve(store, policy, h, run_id, call_id, 0, BOUND, amount)


def events(store, h):
    return store.load(expected_config_hash=h)["events"]


def incident_records(store, h):
    return [r for r in store.load(expected_config_hash=h)["audit"]
            if r["type"] == "budget_incident"]


# ================================================================ 1, 2. no false positives

def test_settlement_exactly_equal_to_reservation_is_an_ordinary_settlement(tmp_path):
    store, policy, h = make_store(tmp_path)
    event = reserve_one(store, policy, h, amount=1000)

    updated = settle_event(store, h, event["event_id"], 1000)

    assert updated["state"] == SETTLED
    assert updated["settled_micro"] == 1000
    assert incident_records(store, h) == []
    assert budget_incidents(store.load(expected_config_hash=h)) == []


def test_settlement_below_reservation_is_an_ordinary_settlement(tmp_path):
    store, policy, h = make_store(tmp_path)
    event = reserve_one(store, policy, h, amount=1000)

    updated = settle_event(store, h, event["event_id"], 400)

    assert updated["state"] == SETTLED
    assert updated["settled_micro"] == 400
    assert commitments(store.load())["total"] == 400, "a lower charge frees budget"
    assert incident_records(store, h) == []


def test_zero_settlement_is_allowed_and_counts_as_zero(tmp_path):
    store, policy, h = make_store(tmp_path)
    event = reserve_one(store, policy, h, amount=1000)
    assert settle_event(store, h, event["event_id"], 0)["state"] == SETTLED
    assert commitments(store.load())["total"] == 0


# ================================================================ 3, 4. the overrun

def test_settlement_above_reservation_is_recorded_as_an_overrun(tmp_path):
    store, policy, h = make_store(tmp_path)
    event = reserve_one(store, policy, h, amount=1000)

    updated = settle_event(store, h, event["event_id"], 1750)

    assert updated["state"] == OVERRUN, "an over-charge is never an ordinary settlement"
    assert updated["reserved_micro"] == 1000, "the reservation is preserved unchanged"
    assert updated["settled_micro"] == 1750, "the real reported charge is kept in full"

    incidents = incident_records(store, h)
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident["event_id"] == event["event_id"]
    assert incident["reserved_micro"] == 1000
    assert incident["settled_micro"] == 1750
    assert incident["overrun_micro"] == 750
    assert incident["run_id"] == RUN_ID and incident["call_id"] == "c1"


def test_overrun_is_durable_and_the_ledger_still_validates(tmp_path):
    store, policy, h = make_store(tmp_path)
    event = reserve_one(store, policy, h)
    settle_event(store, h, event["event_id"], 1_000_000)

    reloaded = store.load(expected_config_hash=h, policy=policy)  # validates
    assert [e["state"] for e in reloaded["events"]] == [OVERRUN]
    events_ = budget_incidents(reloaded)
    assert events_[0]["settled_micro"] - events_[0]["reserved_micro"] == 999_000
    assert incident_records(store, h)[0]["overrun_micro"] == 999_000


def test_a_settlement_above_reservation_cannot_be_stored_as_settled(tmp_path):
    """The state pair is the invariant: hand-editing one into the other is refused."""
    store, policy, h = make_store(tmp_path)
    reserve_one(store, policy, h)
    bad = json.loads(json.dumps(store.load()))
    bad["events"][0].update(state=SETTLED, settled_micro=999_999)

    with pytest.raises(LedgerError, match="must be recorded as an overrun"):
        validate(seal(bad))


def test_an_overrun_state_without_a_real_overrun_is_refused(tmp_path):
    store, policy, h = make_store(tmp_path)
    reserve_one(store, policy, h, amount=1000)
    bad = json.loads(json.dumps(store.load()))
    bad["events"][0].update(state=OVERRUN, settled_micro=1000)  # equal, not above

    with pytest.raises(LedgerError, match="does not settle above its reservation"):
        validate(seal(bad))


def test_an_overrun_without_its_incident_record_is_refused(tmp_path):
    store, policy, h = make_store(tmp_path)
    event = reserve_one(store, policy, h)
    settle_event(store, h, event["event_id"], 5000)

    bad = json.loads(json.dumps(store.load()))
    bad["audit"] = []
    with pytest.raises(LedgerError, match="without a budget incident"):
        validate(seal(bad))


def test_a_tampered_budget_incident_is_refused(tmp_path):
    store, policy, h = make_store(tmp_path)
    event = reserve_one(store, policy, h)
    settle_event(store, h, event["event_id"], 5000)

    bad = json.loads(json.dumps(store.load()))
    bad["audit"][0]["settled_micro"] = 1000          # understate the real charge
    with pytest.raises(LedgerError, match="real reserved and settled amounts"):
        validate(seal(bad))


# ================================================================ 5. budget honesty

def test_available_budget_cannot_increase_after_an_overrun(tmp_path):
    store, policy, h = make_store(tmp_path)
    event = reserve_one(store, policy, h, amount=1000)
    settle_event(store, h, event["event_id"], 5000)

    ledger = store.load()
    assert commitments(ledger)["total"] == 5000, "the full charge is committed"
    assert headroom(ledger, policy, "sprint", "extension")["headroom"] == 1_000_000 - 5000
    assert contribution(ledger["events"][0], {}) == 5000


def test_an_overrun_cannot_be_corrected_away(tmp_path):
    """Corrections apply only to unknown events, so an overrun charge stands."""
    store, policy, h = make_store(tmp_path)
    event = reserve_one(store, policy, h)
    settle_event(store, h, event["event_id"], 9000)

    correction = make_correction(store.load(), event["event_id"], 1, "hide it", "none", "MM")
    bad = json.loads(json.dumps(store.load()))
    bad["audit"].append(correction)

    with pytest.raises(LedgerError, match="only an unknown event can be corrected"):
        validate(seal(bad))
    # and the real charge is untouched by the attempt
    assert commitments(store.load())["total"] == 9000


# ================================================================ 6. no further dispatch

def test_no_further_reservation_is_authorized_after_an_overrun(tmp_path):
    store, policy, h = make_store(tmp_path)
    first = reserve_one(store, policy, h, call_id="c1", amount=1000)
    settle_event(store, h, first["event_id"], 1500)

    before = len(events(store, h))
    with pytest.raises(BudgetIncident) as excinfo:
        reserve_one(store, policy, h, call_id="c2", amount=1)

    assert excinfo.value.event_id == first["event_id"]
    assert excinfo.value.overrun_micro == 500
    assert len(events(store, h)) == before, "the refused reservation is not written"


def test_the_stop_survives_a_reload_and_applies_to_a_second_run(tmp_path):
    store, policy, h = make_store(tmp_path)
    first = reserve_one(store, policy, h, amount=1000)
    settle_event(store, h, first["event_id"], 1200)

    # a different run opened later in the same ledger is still blocked
    open_other_run(store, policy, h, "another-run")
    with pytest.raises(BudgetIncident):
        reserve_one(store, policy, h, call_id="c9", amount=1, run_id="another-run")


def test_a_guarded_client_sends_nothing_after_an_overrun(tmp_path):
    store, policy, h = make_store(tmp_path)
    first = reserve_one(store, policy, h, amount=1000)
    settle_event(store, h, first["event_id"], 5000)

    guard = LedgerGuard(store, policy, h, RUN_ID)
    client = ModelClient(guard=guard, api_key="sk-test-value", base_url=BASE_URL,
                         load_env=False, max_attempts=1, sleep=lambda _s: None)
    sent = []
    client._post_json = lambda payload: sent.append(payload)

    with pytest.raises(BudgetIncident):
        client.call_model([{"role": "user", "content": "x"}], 0.0, max_tokens=8,
                          call_id="stage4n:c2")

    assert sent == [], "no transport call may follow an over-run settlement"
    assert len(events(store, h)) == 1


def test_the_guard_reports_overrun_not_settled(tmp_path):
    store, policy, h = make_store(tmp_path)
    guard = LedgerGuard(store, policy, h, RUN_ID)
    event = guard.reserve("c1", 0, 10, 8)
    guard.dispatched(event["event_id"])

    # a charge far above the reservation, as if the provider reported more tokens
    state = guard.settle(event["event_id"],
                         {"prompt_tokens": 10 ** 6, "completion_tokens": 0}, MODEL, None)

    assert state == OVERRUN, "the guard must not report an over-run charge as 'settled'"


# ================================================================ 7. failure safety

def test_a_failed_settlement_update_cannot_erase_the_reservation(tmp_path):
    store, policy, h = make_store(tmp_path)
    event = reserve_one(store, policy, h, amount=1000)

    with pytest.raises(LedgerError):
        settle_event(store, h, event["event_id"], -5)        # invalid amount

    current = events(store, h)[0]
    assert current["state"] == RESERVED
    assert current["reserved_micro"] == 1000
    assert current["settled_micro"] is None
    assert commitments(store.load())["total"] == 1000


def test_a_duplicate_settlement_is_refused_and_changes_nothing(tmp_path):
    store, policy, h = make_store(tmp_path)
    event = reserve_one(store, policy, h, amount=1000)
    settle_event(store, h, event["event_id"], 1000)

    with pytest.raises(LedgerError, match="illegal transition"):
        settle_event(store, h, event["event_id"], 2000)

    assert events(store, h)[0]["settled_micro"] == 1000


def test_transition_rejects_an_unknown_state_and_keeps_reserved_reachable():
    event = {"event_id": "e", "state": RESERVED, "reserved_micro": 10}
    with pytest.raises(LedgerError, match="illegal transition"):
        transition(event, "released", TS)
    assert transition(event, UNKNOWN, TS)["state"] == UNKNOWN


# ================================================================ 8. concurrency

def test_concurrent_reservations_still_respect_the_cap(tmp_path):
    """Many threads race for a cap that fits exactly ten reservations."""
    store, policy, h = make_store(tmp_path, cap_usd=1.0)
    per_call = 100_000          # cap 1_000_000 -> exactly 10 fit
    ok, refused = [], []

    def worker(n):
        try:
            reserve_one(store, policy, h, call_id=f"c{n}", amount=per_call)
            ok.append(n)
        except LedgerError as exc:
            refused.append(type(exc).__name__)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(25)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(ok) == 10, f"exactly ten reservations fit, got {len(ok)}"
    assert len(refused) == 15 and set(refused) == {"AdmissionRefused"}
    ledger = store.load()
    assert commitments(ledger)["total"] == 1_000_000
    assert commitments(ledger)["total"] == policy.pair_cap("sprint", "extension")
    assert len(ledger["events"]) == 10
    validate(ledger, expected_config_hash=h, policy=policy)


def test_an_overrun_written_under_concurrency_still_stops_later_threads(tmp_path):
    store, policy, h = make_store(tmp_path)
    first = reserve_one(store, policy, h, call_id="c1", amount=1000)
    settle_event(store, h, first["event_id"], 100_000)
    results = []

    def worker(n):
        try:
            reserve_one(store, policy, h, call_id=f"z{n}", amount=1)
            results.append("sent")
        except BudgetIncident:
            results.append("stopped")
        except LedgerError as exc:
            results.append(type(exc).__name__)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == ["stopped"] * 12
    assert len(events(store, h)) == 1
