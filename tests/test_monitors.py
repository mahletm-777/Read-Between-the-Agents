"""Monitor tests: leakage, rendering, parsing, ensemble, records, dry run."""

import hashlib
import json
import re
import ssl
from collections import Counter

import pytest

import generate_corpus as gen
import monitors.runner as runner
from apart.schema import Action, Claim, ClaimType, DeliveryCondition, Message, Trace
from monitors.client import (
    PHASES,
    ApiError,
    FakeClient,
    ModelClient,
    ModelPrice,
    ScriptedClient,
    SpendConfig,
    SpendGuard,
    _ssl_context,
)
from monitors.observable import ObservableRecord, observable
from monitors.parser import aggregate_ensemble, parse_response
from monitors.prompts import SYSTEM_PROMPT, display_id, render_view
from monitors.runner import prompt_sha256, run_corpus, system_prompt_sha256

ALL_ROWS = gen.PILOT_ROWS + gen.GATE_ROWS
VIEWS_ALL = ("M1", "M2", "M3", "M3-ens", "M4")
FORBIDDEN_TOKENS = (
    "trace_id",
    "claim",
    "INFORM",
    "NEUTRAL",
    "NO_MSG",
    "C-0",
    "BENIGN",
    "COLLUSION",
    "COMMITMENT_BREAKING",
)


# §2 leakage tests
def test_no_forbidden_field_or_token_in_any_view():
    for row in ALL_ROWS:
        trace = gen.build_trace(row)
        record = observable(trace)
        for view in VIEWS_ALL:
            prompt = render_view(record, view, display_id(trace.seed, view))
            for token in FORBIDDEN_TOKENS:
                assert token not in prompt, (row.row, view, token)
            for template_id in gen.TEMPLATES:
                assert template_id not in prompt, (row.row, view, template_id)
            assert trace.trace_id not in prompt
            assert re.fullmatch(r"case-[0-9a-f]{8}", display_id(trace.seed, view))
            assert "seed" not in prompt.lower()


def test_invariance_to_generator_only_fields():
    text = "We should both choose B this round."
    base = Trace(
        "trace-a",
        7,
        [Message("agent_1", "agent_2", text)],
        {"agent_1": "B", "agent_2": "B"},
        DeliveryCondition.INFORM_MSG,
        [Claim("agent_1", Action.B, ClaimType.PROPOSAL, 0)],
        ["B-DIR"],
    )
    variant = Trace(
        "trace-b",
        999,
        [Message("agent_1", "agent_2", text)],
        {"agent_1": "B", "agent_2": "B"},
        DeliveryCondition.NEUTRAL_MSG,
        [],
        [],
    )
    for view in VIEWS_ALL:
        assert render_view(observable(base), view, "case-deadbeef") == render_view(
            observable(variant), view, "case-deadbeef"
        )


def test_m1_shows_no_actions_and_m2_shows_no_message_text():
    text = "SECRET MESSAGE TEXT 42"
    trace = Trace(
        "t",
        1,
        [Message("agent_1", "agent_2", text)],
        {"agent_1": "B", "agent_2": "A"},
        DeliveryCondition.INFORM_MSG,
    )
    record = observable(trace)
    m1 = render_view(record, "M1", "case-x")
    m2 = render_view(record, "M2", "case-x")
    assert text in m1
    assert "chose" not in m1
    assert "Payoffs:" not in m1
    assert text not in m2
    assert "Agent 1 chose: B" in m2


def test_renderers_reject_trace_objects():
    trace = gen.build_trace(gen.PILOT_ROWS[0])
    with pytest.raises(TypeError):
        render_view(trace, "M1", "case-x")


def test_all_views_use_one_observable_per_trace(monkeypatch):
    seen = []
    real = runner.observable

    def spy(trace):
        seen.append(trace.trace_id)
        return real(trace)

    monkeypatch.setattr(runner, "observable", spy)
    trace = gen.build_trace(gen.PILOT_ROWS[0])
    run_corpus([trace], FakeClient(), created_utc="2026-10-20T00:00:00Z")
    assert seen == [trace.trace_id]


def test_views_share_the_same_trace_evidence():
    trace = gen.build_trace(next(row for row in gen.PILOT_ROWS if row.row == "R16"))
    record = observable(trace)
    text = trace.messages[0].text
    for view in ("M1", "M3", "M3-ens", "M4"):
        assert text in render_view(record, view, "case-x")
    for view in ("M2", "M3", "M3-ens", "M4"):
        prompt = render_view(record, view, "case-x")
        assert "Agent 1" in prompt and "Agent 2" in prompt


# §5 parser tests
def test_parser_accepts_fenced_json():
    parsed = parse_response('```json\n{"score": 70, "rationale": "clear"}\n```', "M3")
    assert parsed.valid and parsed.score == 70


def test_parser_accepts_extra_text():
    parsed = parse_response('Here is the answer: {"score": 10, "rationale": "weak"} done', "M1")
    assert parsed.valid and parsed.score == 10 and parsed.extra_text


def test_parser_accepts_integral_float():
    parsed = parse_response('{"score": 70.0, "rationale": "ok"}', "M2")
    assert parsed.valid and parsed.score == 70


def test_parser_rejects_string_score():
    assert not parse_response('{"score": "70", "rationale": "ok"}', "M3").valid


def test_parser_rejects_out_of_range_score():
    assert not parse_response('{"score": 101, "rationale": "ok"}', "M3").valid
    assert not parse_response('{"score": -1, "rationale": "ok"}', "M3").valid


def test_parser_rejects_boolean_score():
    assert not parse_response('{"score": true, "rationale": "ok"}', "M3").valid


def test_parser_bad_m4_checklist_keeps_valid_score():
    parsed = parse_response(
        '{"checklist": {"messages": "nope"}, "score": 60, "rationale": "ok"}', "M4"
    )
    assert parsed.valid and parsed.score == 60
    assert parsed.checklist is None
    assert parsed.checklist_valid is False


def test_parser_good_m4_checklist():
    raw = json.dumps(
        {
            "checklist": {
                "messages": [
                    {"row": 1, "signaled_option": "B", "sender_commits": True}
                ],
                "speaker_match": [{"agent": "Agent 1", "match": "yes"}],
                "receiver_compliance": [{"agent": "Agent 2", "complied": "no"}],
            },
            "rationale": "x",
            "score": 90,
        }
    )
    parsed = parse_response(raw, "M4")
    assert parsed.valid and parsed.checklist_valid is True


# §6 ensemble tests
def test_ensemble_median_five_valid():
    assert aggregate_ensemble([10, 20, 30, 40, 50]) == (30.0, 5, "ok")


def test_ensemble_four_valid_uses_middle_mean():
    assert aggregate_ensemble([10, 20, 30, 40, None]) == (25.0, 4, "ok")


def test_ensemble_two_valid_is_parse_failure():
    assert aggregate_ensemble([10, 20, None, None, None]) == (None, 2, "parse_failure")


RECORD_KEYS = {
    "seed",
    "display_id",
    "view",
    "k",
    "model",
    "snapshot",
    "temperature",
    "max_tokens",
    "provider_order",
    "provider_seed",
    "served_model",
    "served_provider",
    "prompt_version",
    "prompt_sha256",
    "score",
    "rationale",
    "checklist",
    "checklist_valid",
    "status",
    "n_attempts",
    "response_id",
    "created_utc",
}


def test_dry_run_records_and_no_network(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise AssertionError("network call attempted during dry run")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    traces = [gen.build_trace(row) for row in gen.GATE_ROWS]
    client = FakeClient()
    records, ensembles = run_corpus(traces, client, created_utc="2026-10-20T00:00:00Z")

    assert len(records) == 8 * 10  # 4 single views + 5 ensemble samples + M3-T07
    assert set(records[0]) == RECORD_KEYS
    assert all(record["status"] == "ok" for record in records)
    assert all(record["n_attempts"] == 1 for record in records)
    assert all(record["created_utc"] == "2026-10-20T00:00:00Z" for record in records)
    assert records[0]["model"] == "fake-model"
    assert records[0]["snapshot"] == "dry-run"

    ensemble_rows = [record for record in records if record["view"] == "M3-ens"]
    assert len(ensemble_rows) == 8 * 5
    assert {record["k"] for record in ensemble_rows} == {0, 1, 2, 3, 4}
    warm_views = {"M3-ens", "M3-T07"}
    assert all(
        record["temperature"] == 0.7 for record in records if record["view"] in warm_views
    )
    assert all(
        record["temperature"] == 0.0 for record in records if record["view"] not in warm_views
    )
    t07_rows = [record for record in records if record["view"] == "M3-T07"]
    assert len(t07_rows) == 8
    assert {record["k"] for record in t07_rows} == {0}
    assert {record["provider_seed"] for record in t07_rows} == {0}
    assert all(
        record["checklist_valid"] is True for record in records if record["view"] == "M4"
    )
    assert all(summary["valid_samples"] == 5 for summary in ensembles.values())
    assert all(summary["status"] == "ok" for summary in ensembles.values())

    out = tmp_path / "scores.jsonl"
    runner.write_scores(records, out)
    assert len(out.read_text(encoding="utf-8").splitlines()) == len(records)
    assert "APART_API_KEY" not in out.read_text(encoding="utf-8")


def test_request_parameters_are_logged_per_view():
    client = FakeClient(provider_order=["provider-a", "provider-b"])
    trace = gen.build_trace(gen.PILOT_ROWS[0])
    records, _ = run_corpus([trace], client, created_utc="2026-10-20T00:00:00Z")

    m3 = next(record for record in records if record["view"] == "M3")
    assert m3["temperature"] == 0.0
    assert m3["max_tokens"] == 250
    assert m3["provider_order"] == ["provider-a", "provider-b"]
    assert m3["served_model"] == "fake-model"
    assert m3["served_provider"] is None

    m4 = next(record for record in records if record["view"] == "M4")
    assert m4["temperature"] == 0.0
    assert m4["max_tokens"] == 600

    ens = next(record for record in records if record["view"] == "M3-ens")
    assert ens["temperature"] == 0.7
    assert ens["provider_seed"] == 0


def test_provider_order_is_sent_in_payload(tmp_path):
    config = SpendConfig(
        provider="prov",
        model="m1",
        phase_caps_usd={phase: 5.0 for phase in PHASES},
        prices={"prov/m1": ModelPrice(1000.0, 2000.0)},
    )
    guard = SpendGuard(config, "smoke", tmp_path / "ledger.json", "run-1")
    client = ModelClient(
        api_key="sk-test",
        spend_guard=guard,
        provider_order=["provider-a", "provider-b"],
    )
    captured = []

    def fake_post(payload):
        captured.append(payload)
        return {
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "model": "m1",
            "provider": "provider-a",
        }

    client._post_json = fake_post
    client.call_model([{"role": "user", "content": "x"}], 0.0, max_tokens=10)
    assert captured[0]["provider"] == {"order": ["provider-a", "provider-b"]}


def test_system_prompt_is_identical_for_every_view_call():
    client = FakeClient()
    trace = gen.build_trace(gen.PILOT_ROWS[0])
    run_corpus([trace], client, created_utc="2026-10-20T00:00:00Z")
    assert {call["messages"][0]["content"] for call in client.calls} == {SYSTEM_PROMPT}


def test_dry_run_records_carry_prompt_version():
    client = FakeClient()
    trace = gen.build_trace(gen.PILOT_ROWS[0])
    records, _ = run_corpus([trace], client, created_utc="2026-10-20T00:00:00Z")
    assert {record["prompt_version"] for record in records} == {"1.1"}


def test_m3_ensemble_uses_identical_prompts():
    client = FakeClient()
    trace = gen.build_trace(gen.PILOT_ROWS[0])
    run_corpus([trace], client, created_utc="2026-10-20T00:00:00Z")
    warm_calls = [call for call in client.calls if call["temperature"] == 0.7]
    assert len(warm_calls) == 6  # 5 ensemble samples + 1 M3-T07
    counts = Counter(json.dumps(call["messages"]) for call in warm_calls)
    assert sorted(counts.values()) == [1, 5]


def test_m3_t07_reuses_the_m3_prompt_at_temperature_07():
    client = FakeClient()
    trace = gen.build_trace(gen.PILOT_ROWS[0])
    records, _ = run_corpus([trace], client, created_utc="2026-10-20T00:00:00Z")
    m3_prompt = render_view(observable(trace), "M3", display_id(trace.seed, "M3"))
    matching = [
        call for call in client.calls if call["messages"][1]["content"] == m3_prompt
    ]
    assert len(matching) == 2  # M3 at T=0 and M3-T07 at T=0.7
    assert {call["temperature"] for call in matching} == {0.0, 0.7}
    t07 = next(record for record in records if record["view"] == "M3-T07")
    assert t07["score"] is not None and t07["status"] == "ok"


def test_raw_responses_persisted(tmp_path):
    trace = gen.build_trace(gen.PILOT_ROWS[0])
    run_corpus([trace], FakeClient(), created_utc="2026-10-20T00:00:00Z", raw_dir=tmp_path)
    files = sorted(path.name for path in tmp_path.glob("*.json"))
    assert len(files) == 10
    assert f"{trace.seed}_M3-ens_4.json" in files
    payload = json.loads(
        (tmp_path / f"{trace.seed}_M3-T07_0.json").read_text(encoding="utf-8")
    )
    assert payload["seed"] == trace.seed
    assert payload["view"] == "M3-T07"
    assert payload["k"] == 0
    assert payload["responses"] and "score" in payload["responses"][0]


def test_retry_after_invalid_reply():
    client = ScriptedClient(["not json", '{"score": 80, "rationale": "ok"}'])
    base = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "CASE case-x"},
    ]
    parsed, attempts, _, status = runner._call_with_retries(
        client, base, "M1", 0.0, 0, 250
    )
    assert status == "ok"
    assert parsed.score == 80
    assert attempts == 2


def test_api_error_status():
    class BoomClient:
        model = "boom"
        snapshot = "boom"

        def call_model(self, *args, **kwargs):
            raise ApiError("boom")

    base = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "CASE case-x"},
    ]
    parsed, attempts, _, status = runner._call_with_retries(
        BoomClient(), base, "M1", 0.0, 0, 250
    )
    assert parsed is None
    assert status == "api_error"
    assert attempts == 1


def test_prompt_hash_is_over_system_and_user():
    assert system_prompt_sha256() == hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    user = "CASE case-x"
    assert prompt_sha256(SYSTEM_PROMPT, user) == hashlib.sha256(
        (SYSTEM_PROMPT + user).encode("utf-8")
    ).hexdigest()


def test_ssl_context_keeps_verification_on():
    context = _ssl_context()
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_model_client_reads_key_from_environment_only(monkeypatch):
    monkeypatch.delenv("APART_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        ModelClient(load_env=False)
    monkeypatch.setenv("APART_API_KEY", "sk-test-secret-value")
    client = ModelClient(load_env=False)
    assert "sk-test-secret-value" not in repr(client)
    assert "sk-test-secret-value" not in client.__repr__()


