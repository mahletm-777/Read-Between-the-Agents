"""Ledger primitives and store (T3 S1 + S1b): synthetic data, tmp dirs only, no network."""

import copy
import json
import multiprocessing
import os
import threading
from pathlib import Path

import pytest

import monitors.ledger as L
from monitors.ledger import (
    AdmissionRefused,
    LedgerError,
    LedgerStore,
    Policy,
    SETTLED,
    UNKNOWN,
    add_run,
    check_admission,
    commitments,
    config_sha256,
    empty_ledger,
    headroom,
    make_correction,
    make_event,
    make_run_header,
    open_run,
    reserve,
    seal,
    transition,
    usd_to_micro,
    validate,
    verify_backup,
)

CONFIG = {
    "allocations": {
        "pilot_audit": {"cap_usd": 8.40, "phase_caps_usd": {"smoke": 0.10, "gate": 1.00, "pilot": 8.90}},
        "sprint": {"cap_usd": 40.00, "phase_caps_usd": {"smoke": 0.10, "gate": 1.00, "extension": 30.00}},
    },
    "backstop_usd": 50.0,
    "stale_after_s": 600,
}
CONFIG_BYTES = json.dumps(CONFIG, sort_keys=True).encode()
H = config_sha256(CONFIG_BYTES)
TS = "2026-10-21T00:00:00Z"
M = 1_000_000


def policy(**overrides):
    cfg = copy.deepcopy(CONFIG)
    cfg.update(overrides)
    return Policy.from_config(cfg)


def sprint_policy(cap_usd):
    allocations = copy.deepcopy(CONFIG["allocations"])
    allocations["sprint"]["cap_usd"] = cap_usd
    return policy(allocations=allocations)


def ledger_with_run(run_id="r1", allocation="sprint", phase="extension"):
    led = empty_ledger(H)
    add_run(led, make_run_header(run_id, allocation, phase, H, {"traces": "abc"}, TS))
    return led


def reserve_mem(led, event_id, run_id, micro, attempt=0, call_id=None):
    led["events"].append(make_event(event_id, run_id, call_id or f"call-{event_id}", attempt, "m", micro, TS))
    return led["events"][-1]


def imported(allocation, phase, micro, run_id):
    return {"run_id": run_id, "allocation_id": allocation, "phase": phase,
            "requests": 1, "amount_micro": micro, "sources": []}


def unknown_event_ledger(micro=500_000):
    led = ledger_with_run()
    reserve_mem(led, "e1", "r1", micro)
    led["events"][0] = transition(led["events"][0], UNKNOWN, TS)
    return led


def new_store(tmp_path):
    store = LedgerStore(tmp_path / "spend" / "ledger.json")
    store.path.parent.mkdir()
    store.init(CONFIG_BYTES)
    return store


def header(run_id="r1", allocation="sprint", phase="extension"):
    return make_run_header(run_id, allocation, phase, H, {"traces": "abc"}, TS)


# ---------------------------------------------------------------- microdollars

def test_usd_to_micro_rounding_and_rejection():
    assert usd_to_micro(0) == 0
    assert usd_to_micro(1) == M
    assert usd_to_micro(0.1) == 100_000            # no binary-float artefact
    assert usd_to_micro("0.0000001") == 1          # rounds up
    assert usd_to_micro(0.0000011) == 2
    assert usd_to_micro("1.722750") == 1_722_750
    assert usd_to_micro("0.123456", exact=True) == 123_456
    with pytest.raises(LedgerError, match="precision"):
        usd_to_micro("0.1234567", exact=True)
    for bad in (-0.01, float("nan"), float("inf"), "abc", None, True, [1]):
        with pytest.raises(LedgerError):
            usd_to_micro(bad)


def test_stored_amounts_must_be_integers():
    led = ledger_with_run()
    reserve_mem(led, "e1", "r1", 5)
    for bad in (5.0, True, -1, 0):
        broken = copy.deepcopy(led)
        broken["events"][0]["reserved_micro"] = bad
        with pytest.raises(LedgerError):
            validate(seal(broken))


def test_policy_caps_are_exact_microdollars():
    assert policy().allocation_caps["pilot_audit"] == 8_400_000
    with pytest.raises(LedgerError, match="precision"):
        policy(backstop_usd=50.0000001)


# ---------------------------------------------------------------- policy and headers

def test_policy_rejects_invalid_pair_and_missing_fields():
    bad = copy.deepcopy(CONFIG)
    bad["allocations"]["sprint"]["phase_caps_usd"]["pilot"] = 1.0
    with pytest.raises(LedgerError, match="invalid pair"):
        Policy.from_config(bad)
    for key in ("backstop_usd", "stale_after_s"):
        cfg = copy.deepcopy(CONFIG)
        del cfg[key]
        with pytest.raises(LedgerError):
            Policy.from_config(cfg)
    cfg = copy.deepcopy(CONFIG)
    cfg["allocations"]["unknown"] = {"cap_usd": 1, "phase_caps_usd": {"smoke": 1}}
    with pytest.raises(LedgerError, match="unknown allocation"):
        Policy.from_config(cfg)


def test_missing_pair_configuration_fails_closed():
    cfg = copy.deepcopy(CONFIG)
    del cfg["allocations"]["sprint"]["phase_caps_usd"]["gate"]
    with pytest.raises(LedgerError, match="no cap configured"):
        Policy.from_config(cfg).pair_cap("sprint", "gate")


def test_invalid_pairs_and_missing_allocation_rejected_at_run_open():
    for allocation, phase in (("sprint", "pilot"), ("pilot_audit", "extension"), (None, "gate"), ("", "gate")):
        with pytest.raises(LedgerError):
            make_run_header("r", allocation, phase, H, {}, TS)


def test_run_header_is_immutable():
    led = ledger_with_run()
    with pytest.raises(LedgerError, match="immutable"):
        add_run(led, make_run_header("r1", "sprint", "gate", H, {"traces": "abc"}, TS))
    add_run(led, header())  # identical: no-op


# ---------------------------------------------------------------- transitions

def test_legal_and_illegal_transitions():
    led = ledger_with_run()
    event = reserve_mem(led, "e1", "r1", 500_000)
    settled = transition(event, SETTLED, TS, settled_micro=200_000)
    assert settled["state"] == SETTLED and event["state"] == "reserved"
    unknown = transition(event, UNKNOWN, TS)
    for frm, to in ((settled, UNKNOWN), (settled, SETTLED), (unknown, SETTLED), (unknown, "reserved"), (event, "released")):
        with pytest.raises(LedgerError, match="illegal transition"):
            transition(frm, to, TS, settled_micro=1 if to == SETTLED else None)


# ---------------------------------------------------------------- validation basics

def test_validate_requires_matching_checksum_and_config_hash():
    led = seal(ledger_with_run())
    validate(led, expected_config_hash=H)
    tampered = copy.deepcopy(led)
    tampered["runs"]["r1"]["phase"] = "gate"
    with pytest.raises(LedgerError, match="checksum"):
        validate(tampered)
    with pytest.raises(LedgerError, match="does not match the ledger binding"):
        validate(led, expected_config_hash=config_sha256(b"other"))


def test_validate_rejects_duplicates_and_inconsistent_events():
    led = ledger_with_run()
    reserve_mem(led, "e1", "r1", 5, call_id="c1")
    validate(seal(led))
    for mutate, msg in (
        (lambda l: reserve_mem(l, "e1", "r1", 5, call_id="c2"), "duplicate event_id"),
        (lambda l: reserve_mem(l, "e2", "r1", 5, call_id="c1"), "call_id, attempt"),
        (lambda l: reserve_mem(l, "e3", "ghost", 5), "unknown run"),
        (lambda l: l["events"][0].update(settled_micro=1), "carries settled_micro"),
    ):
        broken = copy.deepcopy(led)
        mutate(broken)
        with pytest.raises(LedgerError, match=msg):
            validate(seal(broken))


def test_import_run_id_counted_once():
    led = ledger_with_run()
    led["imports"].append(imported("pilot_audit", "gate", 192_792, "run-20261020T190546Z"))
    validate(seal(led))
    dup = copy.deepcopy(led)
    dup["imports"].append(imported("pilot_audit", "gate", 192_792, "run-20261020T190546Z"))
    with pytest.raises(LedgerError, match="counted twice"):
        validate(seal(dup))


# ---------------------------------------------------------------- validator fixes (S1b)

def test_fix_a_corrections_above_and_below_reservation():
    for corrected in (100_000, 5_000_000):        # below and above the 0.5 USD reservation
        led = unknown_event_ledger()
        original = copy.deepcopy(led["events"][0])
        led["audit"].append(make_correction(led, "e1", corrected, "billing", "dashboard ref", "MM", TS))
        validate(seal(led))
        assert led["events"][0] == original
        assert led["audit"][0]["prior_micro"] == 500_000
        assert commitments(led)["total"] == corrected
    # a raised correction reduces headroom and can refuse admission
    led = unknown_event_ledger()
    led["audit"].append(make_correction(led, "e1", 29_999_999, "billing", "ref", "MM", TS))
    with pytest.raises(AdmissionRefused, match="phase cap"):
        check_admission(led, policy(), "r1", 2)


def test_fix_a_correction_chain_and_required_fields():
    led = unknown_event_ledger()
    led["audit"].append(make_correction(led, "e1", 300_000, "r", "x", "MM", TS))
    led["audit"].append(make_correction(led, "e1", 700_000, "r", "x", "MM", TS))
    validate(seal(led))
    assert led["audit"][1]["prior_micro"] == 300_000
    assert commitments(led)["total"] == 700_000
    for field in ("reason", "evidence", "by"):
        broken = copy.deepcopy(led)
        del broken["audit"][1][field]
        with pytest.raises(LedgerError):
            validate(seal(broken))
    wrong_prior = copy.deepcopy(led)
    wrong_prior["audit"][1]["prior_micro"] = 500_000
    with pytest.raises(LedgerError, match="prior_micro"):
        validate(seal(wrong_prior))
    negative = copy.deepcopy(led)
    negative["audit"][1]["corrected_micro"] = -1
    with pytest.raises(LedgerError):
        validate(seal(negative))
    settled = ledger_with_run()
    reserve_mem(settled, "e1", "r1", 5)
    settled["events"][0] = transition(settled["events"][0], SETTLED, TS, settled_micro=3)
    settled["audit"].append({"audit_id": "a", "type": "correction", "event_id": "e1", "prior_micro": 3,
                             "corrected_micro": 1, "reason": "r", "evidence": "x", "by": "MM"})
    with pytest.raises(LedgerError, match="only an unknown event"):
        validate(seal(settled))


def test_fix_b_replay_approval_requires_fields():
    led = unknown_event_ledger()
    ok = {"audit_id": "a", "type": "replay_approval", "original_event_id": "e1",
          "record_key": {"seed": 1, "view": "M3", "k": 0}, "reason": "r", "by": "MM"}
    led["audit"].append(ok)
    validate(seal(led))
    for mutate in (lambda r: r.pop("original_event_id"), lambda r: r.pop("record_key"),
                   lambda r: r.update(record_key={"seed": 1}), lambda r: r.pop("reason"),
                   lambda r: r.update(original_event_id="nope"), lambda r: r.update(new_event_id="e1")):
        broken = copy.deepcopy(led)
        mutate(broken["audit"][0])
        with pytest.raises(LedgerError):
            validate(seal(broken))
    empty = unknown_event_ledger()
    empty["audit"].append({"audit_id": "a", "type": "replay_approval", "by": "MM"})
    with pytest.raises(LedgerError):
        validate(seal(empty))


def test_fix_c_late_response_must_reference_unknown_event():
    led = unknown_event_ledger()
    led["audit"].append({"audit_id": "a", "type": "late_response", "event_id": "e1", "by": "MM"})
    validate(seal(led))
    led["audit"][0]["event_id"] = "nope"
    with pytest.raises(LedgerError, match="late response"):
        validate(seal(led))


def test_fix_d_run_bound_to_foreign_config_fails_validate():
    led = ledger_with_run()
    led["runs"]["r1"]["config_sha256"] = "never-used"
    with pytest.raises(LedgerError, match="never used"):
        validate(seal(led))


def test_fix_e_rebind_chain_is_validated_end_to_end():
    led = ledger_with_run()
    h2, h3 = config_sha256(b"v2"), config_sha256(b"v3")
    rb = lambda old, new: {"audit_id": f"rb-{new[:6]}", "type": "rebind", "old_config_sha256": old,
                           "new_config_sha256": new, "reason": "r", "by": "MM"}
    led["audit"] += [rb(H, h2), rb(h2, h3)]
    led["config_sha256"] = h3
    validate(seal(led))                       # old run under H is still a known binding
    with pytest.raises(LedgerError, match="config hash"):
        check_admission(led, policy(), "r1", 1)   # but cannot spend after rebind
    broken_link = copy.deepcopy(led)
    broken_link["audit"][1]["old_config_sha256"] = H
    with pytest.raises(LedgerError, match="chain is broken"):
        validate(seal(broken_link))
    wrong_end = copy.deepcopy(led)
    wrong_end["config_sha256"] = h2
    with pytest.raises(LedgerError, match="end of the rebind chain"):
        validate(seal(wrong_end))
    no_rebind = ledger_with_run()
    no_rebind["config_sha256"] = h2
    with pytest.raises(LedgerError, match="end of the rebind chain"):
        validate(seal(no_rebind))


# ---------------------------------------------------------------- commitments and admission

def test_commitment_counts_each_state_correctly():
    led = ledger_with_run()
    reserve_mem(led, "e1", "r1", 500_000, call_id="c1")
    reserve_mem(led, "e2", "r1", 400_000, call_id="c2")
    reserve_mem(led, "e3", "r1", 300_000, attempt=1, call_id="c2")
    led["events"][1] = transition(led["events"][1], UNKNOWN, TS)
    led["events"][2] = transition(led["events"][2], SETTLED, TS, settled_micro=50_000)
    led["imports"].append(imported("pilot_audit", "pilot", 1_281_306, "run-p"))
    totals = commitments(led)
    assert totals["pair"][("sprint", "extension")] == 950_000
    assert totals["allocation"]["pilot_audit"] == 1_281_306
    assert totals["total"] == 2_231_306


def test_historical_dedup_total():
    led = empty_ledger(H)
    for run_id, phase, micro in (("run-20261020T190546Z", "gate", 192_792),
                                 ("run-20261020T191553Z", "gate", 248_652),
                                 ("run-20261020T192256Z", "pilot", 1_281_306)):
        led["imports"].append(imported("pilot_audit", phase, micro, run_id))
    validate(seal(led))
    assert commitments(led)["allocation"]["pilot_audit"] == 1_722_750


def test_pilot_audit_cap_binds_before_pilot_phase_cap():
    led = ledger_with_run("p", "pilot_audit", "pilot")
    led["imports"].append(imported("pilot_audit", "gate", 7_390_000, "imp-g"))
    reserve_mem(led, "e1", "p", M)
    with pytest.raises(AdmissionRefused) as exc:
        check_admission(led, policy(), "p", 20_000)
    assert exc.value.control == "allocation cap"
    assert check_admission(led, policy(), "p", 10_000)["binding"] == "allocation cap"


def test_sprint_cap_phase_cap_backstop_and_no_transfer():
    led = ledger_with_run("s", "sprint", "extension")
    led["imports"].append(imported("sprint", "gate", 990_000, "imp-g"))
    reserve_mem(led, "e1", "s", 29_000_000)
    with pytest.raises(AdmissionRefused, match="allocation cap"):
        check_admission(led, sprint_policy(30.0), "s", 20_000)       # pilot_audit headroom never helps
    g = ledger_with_run("g", "sprint", "gate")
    g["imports"].append(imported("pilot_audit", "gate", 990_000, "pa-gate"))
    reserve_mem(g, "e1", "g", 990_000)
    with pytest.raises(AdmissionRefused, match="phase cap"):
        check_admission(g, policy(), "g", 20_000)
    assert headroom(g, policy(), "sprint", "gate")["controls"]["phase cap"] == 10_000
    b = ledger_with_run("b", "sprint", "extension")
    b["imports"].append(imported("pilot_audit", "pilot", 8_000_000, "i1"))
    reserve_mem(b, "e1", "b", 20_000_000)
    with pytest.raises(AdmissionRefused, match="local backstop"):
        check_admission(b, policy(backstop_usd=28.01), "b", 20_000)


def test_negative_headroom_refuses_everything():
    led = ledger_with_run("s", "sprint", "extension")
    led["imports"].append(imported("sprint", "gate", 1_500_000, "over"))   # already above the gate cap
    add_run(led, make_run_header("g", "sprint", "gate", H, {}, TS))
    result = headroom(led, policy(), "sprint", "gate")
    assert result["headroom"] < 0
    with pytest.raises(AdmissionRefused, match="phase cap"):
        check_admission(led, policy(), "g", 1)


def test_new_run_id_does_not_reset_commitments():
    led = ledger_with_run("r1", "sprint", "extension")
    reserve_mem(led, "e1", "r1", 29_990_000)
    add_run(led, make_run_header("r2", "sprint", "extension", H, {}, TS))
    with pytest.raises(AdmissionRefused):
        check_admission(led, policy(), "r2", 20_000)


# ---------------------------------------------------------------- store: init and load

def test_missing_ledger_and_double_init(tmp_path):
    store = LedgerStore(tmp_path / "ledger.json")
    with pytest.raises(LedgerError, match="initialise it explicitly"):
        store.load()
    with pytest.raises(LedgerError, match="initialise it explicitly"):
        store.transact(lambda led: None)
    store.init(CONFIG_BYTES)
    assert store.load(expected_config_hash=H)["config_sha256"] == H
    with pytest.raises(LedgerError, match="already exists"):
        store.init(CONFIG_BYTES)


def test_missing_directory_is_not_created(tmp_path):
    store = LedgerStore(tmp_path / "absent" / "ledger.json")
    with pytest.raises(LedgerError, match="does not exist"):
        store.init(CONFIG_BYTES)
    assert not (tmp_path / "absent").exists()


def test_corrupt_ledger_refused(tmp_path):
    store = new_store(tmp_path)
    good = store.path.read_bytes()
    for corrupt in (b"", b"{not json", good.replace(b'"version": 2', b'"version": 3'),
                    good.replace(b'"runs": {}', b'"runs": {"x": 1}')):
        store.path.write_bytes(corrupt)
        with pytest.raises(LedgerError):
            store.load()
        with pytest.raises(LedgerError):
            reserve(store, policy(), H, "r1", "c", 0, "m", 1)
    store.path.write_bytes(good)
    store.load()


def test_config_mismatch_refused(tmp_path):
    store = new_store(tmp_path)
    with pytest.raises(LedgerError, match="binding"):
        store.load(expected_config_hash=config_sha256(b"other"))


# ---------------------------------------------------------------- store: transactions and durability

def test_reservation_is_durable_before_return(tmp_path):
    store = new_store(tmp_path)
    open_run(store, policy(), header(), H, "MM")
    event = reserve(store, policy(), H, "r1", "c1", 0, "m", 250_000)
    on_disk = json.loads(store.path.read_text())
    assert [e["event_id"] for e in on_disk["events"]] == [event["event_id"]]
    validate(on_disk, expected_config_hash=H)


def test_failed_transaction_preserves_old_state(tmp_path):
    store = new_store(tmp_path)
    open_run(store, policy(), header(), H, "MM")
    before = store.path.read_bytes()

    def boom(led):
        led["events"].append("half-done")
        raise RuntimeError("operation failed")

    with pytest.raises(RuntimeError):
        store.transact(boom)
    with pytest.raises(LedgerError):   # result fails validation -> nothing written
        store.transact(lambda led: led["events"].append({"event_id": "bad"}))
    with pytest.raises(AdmissionRefused):
        reserve(store, policy(), H, "r1", "c1", 0, "m", 31 * M)
    assert store.path.read_bytes() == before
    assert not list(store.path.parent.glob("ledger.json.tmp.*"))


@pytest.mark.parametrize("failing", ["os.write", "os.fsync", "os.replace"])
def test_atomic_write_failure_records_nothing(tmp_path, monkeypatch, failing):
    store = new_store(tmp_path)
    open_run(store, policy(), header(), H, "MM")
    before = store.path.read_bytes()
    module, name = failing.split(".")
    real = getattr(os, name)
    calls = {"n": 0}

    def fail(*args, **kwargs):
        calls["n"] += 1
        raise OSError(f"simulated {name} failure")

    monkeypatch.setattr(L.os, name, fail)
    with pytest.raises(LedgerError, match="write failed"):
        reserve(store, policy(), H, "r1", "c1", 0, "m", 1_000)
    monkeypatch.setattr(L.os, name, real)
    assert calls["n"] >= 1
    assert store.path.read_bytes() == before
    assert not list(store.path.parent.glob("ledger.json.tmp.*"))
    validate(json.loads(before))


def test_concurrent_reservations_threads(tmp_path):
    store = new_store(tmp_path)
    open_run(store, policy(), header("t", "sprint", "gate"), H, "MM")
    admitted, refused = [], []

    def worker(i):
        try:
            admitted.append(reserve(LedgerStore(store.path), policy(), H, "t", f"c{i}", 0, "m", 100_000))
        except AdmissionRefused:
            refused.append(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(admitted) == 10 and len(refused) == 15        # gate cap 1.00 / 0.10
    final = store.load()
    assert len(final["events"]) == 10
    assert commitments(final)["pair"][("sprint", "gate")] == M


def _process_worker(path, n, out):
    import monitors.ledger as ledger_mod

    store = ledger_mod.LedgerStore(Path(path))
    pol = ledger_mod.Policy.from_config(CONFIG)
    ok = 0
    for i in range(n):
        try:
            ledger_mod.reserve(store, pol, H, "p", f"{os.getpid()}-{i}", 0, "m", 100_000)
            ok += 1
        except ledger_mod.AdmissionRefused:
            pass
    out.put(ok)


def test_concurrent_reservations_processes(tmp_path):
    store = new_store(tmp_path)
    open_run(store, policy(), header("p", "sprint", "gate"), H, "MM")
    ctx = multiprocessing.get_context("spawn")
    out = ctx.Queue()
    procs = [ctx.Process(target=_process_worker, args=(str(store.path), 6, out)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    assert sum(out.get(timeout=5) for _ in procs) == 10
    final = store.load()
    assert len(final["events"]) == 10
    assert len({e["event_id"] for e in final["events"]}) == 10


# ---------------------------------------------------------------- backups and restore

def test_backups_are_unique_verified_and_named(tmp_path):
    store = new_store(tmp_path)
    paths = [store.backup("test", "MM") for _ in range(3)]
    assert len(set(paths)) == 3
    for path in paths:
        assert path.name.startswith("ledger.") and path.suffix == ".json"
        assert path.with_name(path.name + ".sha256").exists()
        verify_backup(path)
    assert len({path.name for path in paths}) == 3
    assert len({path.read_bytes() for path in paths}) == 1       # same validated content each time


def test_backup_failure_aborts_run_open(tmp_path, monkeypatch):
    store = new_store(tmp_path)
    before = store.path.read_bytes()
    monkeypatch.setattr(L, "_write_new_file", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(LedgerError, match="backup failed"):
        open_run(store, policy(), header(), H, "MM")
    monkeypatch.undo()
    assert store.path.read_bytes() == before
    assert store.load()["runs"] == {}


def test_backup_of_corrupt_ledger_refused(tmp_path):
    store = new_store(tmp_path)
    store.path.write_bytes(b"{broken")
    with pytest.raises(LedgerError):
        store.backup("test", "MM")


def test_restore_valid_backup(tmp_path):
    store = new_store(tmp_path)
    open_run(store, policy(), header(), H, "MM")
    reserve(store, policy(), H, "r1", "c1", 0, "m", 1_000)
    good = store.backup("before damage", "MM")
    store.path.write_bytes(b"garbage")
    with pytest.raises(LedgerError):
        reserve(store, policy(), H, "r1", "c2", 0, "m", 1_000)
    restored = store.restore(good, "disk error", "MM")
    assert restored["audit"][-1]["type"] == "restore"
    assert len(store.load()["events"]) == 1
    assert list(store.backup_dir.glob("corrupt.*.json"))       # damaged file kept as evidence
    reserve(store, policy(), H, "r1", "c2", 0, "m", 1_000)    # spending resumes only after a valid restore


def test_restore_refuses_tampered_backup(tmp_path):
    store = new_store(tmp_path)
    open_run(store, policy(), header(), H, "MM")
    backup = store.backup("b", "MM")
    current = store.path.read_bytes()
    data = json.loads(backup.read_text())
    data["runs"]["r1"]["phase"] = "gate"
    backup.write_text(json.dumps(data))
    with pytest.raises(LedgerError, match="sha256"):
        store.restore(backup, "r", "MM")
    # matching sidecar but invalid internal checksum is also refused
    backup.with_name(backup.name + ".sha256").write_text(
        __import__("hashlib").sha256(backup.read_bytes()).hexdigest()
    )
    with pytest.raises(LedgerError, match="checksum"):
        store.restore(backup, "r", "MM")
    assert store.path.read_bytes() == current
