"""Spend-guard tests: accounting, refusal, fail-closed config, concurrency."""

import json
import threading
import urllib.error

import pytest

from monitors.client import (
    PHASES,
    BudgetExceeded,
    ModelClient,
    ModelPrice,
    SpendConfig,
    SpendConfigError,
    SpendGuard,
)


def make_config(cap=1.0, model="m1", prices=None, provider="prov"):
    caps = {phase: cap for phase in PHASES}
    if prices is None:
        prices = {f"{provider}/{model}": ModelPrice(1000.0, 2000.0)}
    return SpendConfig(provider=provider, model=model, phase_caps_usd=caps, prices=prices)


def make_guard(tmp_path, cap=1.0, phase="smoke", run_id="run-1", **kwargs):
    return SpendGuard(
        make_config(cap, **kwargs), phase, tmp_path / "spend_guard.json", run_id
    )


def test_reservation_and_settlement_accounting(tmp_path):
    guard = make_guard(tmp_path)
    reservation = guard.reserve("m1", 100, 50)
    assert reservation.amount_usd == pytest.approx(0.2)  # 0.1 input + 0.1 output
    assert guard.totals()["phase_spent_usd"] == pytest.approx(0.2)

    settled = guard.settle(reservation, {"prompt_tokens": 80, "completion_tokens": 40})
    assert settled == pytest.approx(0.16)  # 0.08 + 0.08
    totals = guard.totals()
    assert totals["phase_spent_usd"] == pytest.approx(0.16)
    assert totals["phase_requests"] == 1
    assert totals["run_spent_usd"] == pytest.approx(0.16)
    assert totals["run_requests"] == 1


def test_missing_usage_keeps_worst_case(tmp_path):
    guard = make_guard(tmp_path)
    reservation = guard.reserve("m1", 100, 50)
    assert guard.settle(reservation, None) == pytest.approx(0.2)
    assert guard.settle(reservation, {"prompt_tokens": "bad"}) == pytest.approx(0.2)
    totals = guard.totals()
    assert totals["phase_spent_usd"] == pytest.approx(0.2)
    assert totals["phase_spent_usd"] > 0


def test_request_refused_before_exceeding_cap(tmp_path):
    guard = make_guard(tmp_path, cap=0.15)
    with pytest.raises(BudgetExceeded):
        guard.reserve("m1", 100, 50)
    totals = guard.totals()
    assert totals["phase_spent_usd"] == 0.0
    assert totals["phase_requests"] == 0


def test_persisted_total_survives_restart(tmp_path):
    config = make_config(cap=0.005)
    SpendGuard(config, "smoke", tmp_path / "spend_guard.json", "run-1")
    ledger = json.loads((tmp_path / "spend_guard.json").read_text())
    ledger["phases"]["smoke"] = {"spent_usd": 0.004, "requests": 2}
    (tmp_path / "spend_guard.json").write_text(json.dumps(ledger))

    second = SpendGuard(config, "smoke", tmp_path / "spend_guard.json", "run-2")
    assert second.totals()["phase_spent_usd"] == pytest.approx(0.004)
    with pytest.raises(BudgetExceeded):
        second.reserve("m1", 1, 1)  # 0.004 + 0.003 > 0.005


def test_unknown_phase_or_missing_cap_fails_closed(tmp_path):
    config = make_config(cap=1.0)
    with pytest.raises(SpendConfigError):
        SpendGuard(config, "not-a-phase", tmp_path / "ledger.json", "run")
    partial = SpendConfig(
        provider="prov",
        model="m1",
        phase_caps_usd={"smoke": 1.0},
        prices={"prov/m1": ModelPrice(1.0, 1.0)},
    )
    with pytest.raises(SpendConfigError):
        SpendGuard(partial, "gate", tmp_path / "ledger2.json", "run")
    with pytest.raises(SpendConfigError):
        SpendGuard(config, "smoke", tmp_path / "ledger3.json", " ")


def test_missing_price_fails_closed(tmp_path):
    with pytest.raises(SpendConfigError):
        SpendConfig(
            provider="prov",
            model="m1",
            phase_caps_usd={phase: 1.0 for phase in PHASES},
            prices={"prov/other": ModelPrice(1.0, 1.0)},
        )
    guard = make_guard(tmp_path)
    with pytest.raises(SpendConfigError):
        guard.reserve("m2", 1, 1)
    reservation = guard.reserve("m1", 100, 50)
    with pytest.raises(SpendConfigError):
        guard.settle(reservation, {"prompt_tokens": 1, "completion_tokens": 1}, served_model="m2")
    assert guard.totals()["phase_spent_usd"] == pytest.approx(0.2)


def test_every_api_retry_is_counted(tmp_path):
    guard = make_guard(tmp_path, cap=5.0)
    attempts = {"n": 0}

    def fake_post(payload):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise urllib.error.URLError("temporary")
        return {
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "model": "m1",
        }

    client = ModelClient(
        api_key="sk-test", spend_guard=guard, max_attempts=4, sleep=lambda _: None
    )
    client._post_json = fake_post
    response = client.call_model([{"role": "user", "content": "hello"}], 0.0, max_tokens=50)
    assert response.text == "{}"
    totals = guard.totals()
    assert totals["phase_requests"] == 3
    assert totals["phase_spent_usd"] > 0


def test_failed_requests_keep_worst_case(tmp_path):
    from monitors.client import ApiError

    guard = make_guard(tmp_path, cap=5.0)

    def boom(payload):
        raise urllib.error.URLError("down")

    client = ModelClient(
        api_key="sk-test", spend_guard=guard, max_attempts=2, sleep=lambda _: None
    )
    client._post_json = boom
    with pytest.raises(ApiError):
        client.call_model([{"role": "user", "content": "hello"}], 0.0, max_tokens=50)
    totals = guard.totals()
    assert totals["phase_requests"] == 2
    assert totals["phase_spent_usd"] > 0


def test_client_without_guard_refuses_to_send():
    client = ModelClient(api_key="sk-test")
    with pytest.raises(SpendConfigError):
        client.call_model([{"role": "user", "content": "x"}], 0.0)


def test_client_refuses_before_sending_when_cap_would_be_exceeded(tmp_path):
    guard = make_guard(tmp_path, cap=0.01)
    called = []
    client = ModelClient(api_key="sk-test", spend_guard=guard)
    client._post_json = lambda payload: called.append(payload)
    with pytest.raises(BudgetExceeded):
        client.call_model([{"role": "user", "content": "x"}], 0.0, max_tokens=50)
    assert called == []
    assert guard.totals()["phase_requests"] == 0


def test_settlement_uses_served_model_price(tmp_path):
    prices = {
        "prov/m1": ModelPrice(1000.0, 2000.0),
        "prov/m2": ModelPrice(10000.0, 20000.0),
    }
    guard = SpendGuard(
        make_config(cap=5.0, prices=prices), "smoke", tmp_path / "ledger.json", "run-1"
    )
    reservation = guard.reserve("m1", 100, 50)  # 0.2 at m1 prices
    actual = guard.settle(
        reservation, {"prompt_tokens": 10, "completion_tokens": 10}, served_model="m2"
    )
    assert actual == pytest.approx(0.3)  # 0.1 + 0.2 at m2 prices
    assert guard.totals()["phase_spent_usd"] == pytest.approx(0.3)


def test_concurrent_reservations_cannot_overshoot(tmp_path):
    prices = {"prov/m1": ModelPrice(1000.0, 1000.0)}  # 0.001 per token
    guard = make_guard(tmp_path, cap=0.01, prices=prices)  # 0.002 per reservation
    barrier = threading.Barrier(10)
    successes = []
    failures = []

    def worker():
        barrier.wait()
        try:
            successes.append(guard.reserve("m1", 1, 1))
        except BudgetExceeded:
            failures.append(1)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(successes) == 5
    assert len(failures) == 5
    totals = guard.totals()
    assert totals["phase_requests"] == 5
    assert totals["phase_spent_usd"] == pytest.approx(0.01)


def test_two_guards_share_one_ledger_without_overshoot(tmp_path):
    prices = {"prov/m1": ModelPrice(1000.0, 1000.0)}
    config = make_config(cap=0.01, prices=prices)
    ledger = tmp_path / "ledger.json"
    guards = [
        SpendGuard(config, "smoke", ledger, "run-a"),
        SpendGuard(config, "smoke", ledger, "run-b"),
    ]
    barrier = threading.Barrier(10)
    successes = []
    failures = []

    def worker(index):
        barrier.wait()
        try:
            successes.append(guards[index % 2].reserve("m1", 1, 1))
        except BudgetExceeded:
            failures.append(1)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(successes) == 5
    assert len(failures) == 5
    assert guards[0].totals()["phase_spent_usd"] == pytest.approx(0.01)


def test_config_file_round_trip_and_missing_file(tmp_path):
    path = tmp_path / "spend.json"
    path.write_text(
        json.dumps(
            {
                "provider": "prov",
                "model": "m1",
                "phase_caps_usd": {phase: 2.0 for phase in PHASES},
                "prices": {"prov/m1": {"input_per_million": 1.0, "output_per_million": 2.0}},
            }
        )
    )
    config = SpendConfig.from_file(path)
    assert config.model == "m1"
    assert config.price_for("m1").input_per_million == pytest.approx(1.0)
    with pytest.raises(SpendConfigError):
        SpendConfig.from_file(tmp_path / "missing.json")


def test_no_placeholder_model_config_is_shipped():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for path in (root / "configs").glob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert "gpt-5.4-mini" not in text, f"placeholder model in {path}"


def test_api_key_is_never_persisted_or_logged(tmp_path):
    guard = make_guard(tmp_path)
    client = ModelClient(api_key="sk-super-secret", spend_guard=guard, max_attempts=1)
    client._post_json = lambda payload: {
        "choices": [{"message": {"content": "{}"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        "model": "m1",
    }
    client.call_model([{"role": "user", "content": "x"}], 0.0, max_tokens=10)
    ledger_text = (tmp_path / "spend_guard.json").read_text()
    assert "sk-super-secret" not in ledger_text
    assert "sk-super-secret" not in repr(client)


def test_parse_retry_is_counted_as_another_request(tmp_path):
    from monitors.runner import _call_with_retries

    guard = make_guard(tmp_path, cap=5.0)
    replies = iter(
        [
            {
                "choices": [{"message": {"content": "not json"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "model": "m1",
            },
            {
                "choices": [
                    {"message": {"content": '{"score": 50, "rationale": "ok"}'}}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "model": "m1",
            },
        ]
    )
    client = ModelClient(api_key="sk-test", spend_guard=guard, max_attempts=1)
    client._post_json = lambda payload: next(replies)
    base = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "CASE case-x"},
    ]
    parsed, attempts, _, status = _call_with_retries(client, base, "M1", 0.0, 0, 250)
    assert status == "ok"
    assert parsed.score == 50
    assert attempts == 2  # one invalid reply + one parse retry
    assert guard.totals()["phase_requests"] == 2

