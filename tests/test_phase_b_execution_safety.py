"""Phase B execution-safety tests (offline).

Scope: the monitor entry point's retry pinning, its fail-closed client checks,
its explicit-endpoint requirement, the exclusivity of score / id-map output, and
the *current* stop-rule boundary behaviour.

Offline only. The provider transport is stubbed, ``urllib.request.urlopen`` and
``socket.socket.connect`` are blocked for the whole module, the ``.env`` loader is
neutralised, and every artefact lives under ``tmp_path``. No credentials, no
network, no canonical ledger, no corpus or configuration changes.

These tests pin existing behaviour. They do not redefine the stop rule, the
three-parse-attempt cap, or the treatment of M4 checklist-invalid replies.
"""

from __future__ import annotations

import importlib.util
import io
import json
import socket
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from apart.schema import trace_from_dict
from monitors.client import FakeClient, ModelClient
from monitors.ledger import LedgerStore, config_sha256
from monitors.parser import RETRY_MESSAGE
from monitors.runner import non_ok_rate, run_corpus, should_stop, write_id_map, write_scores

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://example.invalid/v1"
FROZEN_TRACE = ROOT / "data" / "frozen" / "pilot_v2_traces.jsonl"
UNSET = object()

# Test-only policy. Mirrors the shape ``Policy.from_config`` requires; no value here
# is a claim about a real provider's price or cap.
CONFIG = {
    "provider": "prov",
    "model": "m1",
    "prices": {"prov/m1": {"input_per_million": 3.0, "output_per_million": 15.0}},
    "allocations": {
        "pilot_audit": {
            "cap_usd": 8.40,
            "phase_caps_usd": {"smoke": 0.10, "gate": 1.00, "pilot": 8.90},
        }
    },
    "backstop_usd": 50.0,
    "stale_after_s": 600,
}
VALID_SCORE = {
    "choices": [{"message": {"content": '{"score": 10, "rationale": "ok"}'}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    "model": "m1",
}
VALID_CHECKLIST = {
    "choices": [{"message": {"content": json.dumps(
        {"checklist": {"messages": [], "speaker_match": [], "receiver_compliance": []},
         "rationale": "ok", "score": 10})}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    "model": "m1",
}
NOT_JSON = {
    "choices": [{"message": {"content": "not a JSON object"}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    "model": "m1",
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Block the transport for every test in this module."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """The monitor CLI module with a tmp ledger, tmp corpus and stubbed transport."""
    spec = importlib.util.spec_from_file_location(
        "run_monitors_phase_b", ROOT / "scripts" / "run_monitors.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ledger_path = tmp_path / "spend" / "ledger.json"
    monkeypatch.setattr(module, "LEDGER_PATH", ledger_path)
    monkeypatch.setattr(module, "load_apart_env", lambda *a, **k: 0)  # never read .env
    monkeypatch.setenv("APART_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("APART_BASE_URL", BASE_URL)
    config_path = tmp_path / "spend.json"
    config_path.write_text(json.dumps(CONFIG, sort_keys=True))
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(FROZEN_TRACE.read_text().splitlines()[0] + "\n")
    sends: list = []

    def post(self, payload):
        sends.append(payload)
        checklist = "CHECKLIST" in payload["messages"][1]["content"]
        return VALID_CHECKLIST if checklist else VALID_SCORE

    monkeypatch.setattr(ModelClient, "_post_json", post)
    return SimpleNamespace(module=module, ledger=ledger_path, config=config_path,
                           corpus=corpus, sends=sends, tmp=tmp_path)


def _init_ledger(cli) -> None:
    cli.ledger.parent.mkdir(parents=True, exist_ok=True)
    LedgerStore(cli.ledger).init(cli.config.read_bytes())


def _argv(cli, *extra, out=None, id_map=None, raw_dir=None, run_id="run-pb") -> list:
    return [
        "--traces", str(cli.corpus),
        "--spend-config", str(cli.config),
        "--out", str(out or cli.tmp / "out" / "scores.jsonl"),
        "--id-map", str(id_map or cli.tmp / "out" / "id_map.csv"),
        "--raw-dir", str(raw_dir or cli.tmp / "out" / "raw"),
        "--allocation", "pilot_audit",
        "--phase", "pilot",
        "--run-id", run_id,
        *extra,
    ]


def _args(cli, **overrides) -> SimpleNamespace:
    values = {
        "traces": cli.corpus,
        "spend_config": cli.config,
        "allocation": "pilot_audit",
        "phase": "pilot",
        "run_id": "run-pb",
        "by": "MM",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _records(path: Path) -> list:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _events(cli) -> list:
    return LedgerStore(cli.ledger).load()["events"]


def _install_fake_client(monkeypatch, cli, *, max_attempts=1, provider_order=(),
                         bound_to=UNSET, omit_attempts=False):
    """Replace the CLI's ``ModelClient`` with a recordable stand-in."""
    calls: dict = {}

    def factory(**kwargs):
        calls.update(kwargs)
        client = SimpleNamespace()
        if not omit_attempts:
            client.max_attempts = max_attempts
        client.provider_order = list(provider_order)
        client.guard = kwargs["guard"] if bound_to is UNSET else bound_to
        return client

    monkeypatch.setattr(cli.module, "ModelClient", factory)
    return calls


# ---------------------------------------------------------------- retry pinning


def test_one_http_attempt_per_parse_attempt(cli):
    """A clean run makes exactly one HTTP attempt per logical call (four arms)."""
    _init_ledger(cli)
    assert cli.module.main(_argv(cli, "--views", "M1,M2,M3,M4")) == 0
    events = _events(cli)
    assert len(cli.sends) == len(events) == 4                 # 1 trace x 4 arms
    assert {event["attempt"] for event in events} == {0}      # never a second HTTP attempt
    assert {event["state"] for event in events} == {"settled"}
    assert {record["n_attempts"] for record in _records(cli.tmp / "out" / "scores.jsonl")} == {1}


def test_parse_retries_are_new_logical_calls(cli, monkeypatch):
    """A parse retry is a new logical call, not another HTTP attempt."""
    sends: list = []

    def post(self, payload):
        sends.append(payload)
        if payload["messages"][-1]["content"] != RETRY_MESSAGE:
            return NOT_JSON
        checklist = "CHECKLIST" in payload["messages"][1]["content"]
        return VALID_CHECKLIST if checklist else VALID_SCORE

    monkeypatch.setattr(ModelClient, "_post_json", post)
    _init_ledger(cli)
    assert cli.module.main(_argv(cli, "--views", "M1,M2,M3,M4")) == 0
    events = _events(cli)
    assert len(sends) == len(events) == 8                     # 4 calls x 2 parse attempts
    assert {event["attempt"] for event in events} == {0}
    suffixes = sorted(event["call_id"].rsplit(":p", 1)[-1] for event in events)
    assert suffixes == ["1", "1", "1", "1", "2", "2", "2", "2"]
    records = _records(cli.tmp / "out" / "scores.jsonl")
    assert {record["n_attempts"] for record in records} == {2}
    assert {record["status"] for record in records} == {"ok"}


def test_parse_attempts_are_capped_at_three(cli, monkeypatch):
    """An unusable reply is retried twice at most; the run still completes."""
    sends: list = []

    def post(self, payload):
        sends.append(payload)
        return NOT_JSON

    monkeypatch.setattr(ModelClient, "_post_json", post)
    _init_ledger(cli)
    # Every view is 100% non-ok, so records are written and the existing strict
    # >10% rule then reports stop (exit 2).
    assert cli.module.main(_argv(cli, "--views", "M1,M2,M3,M4")) == 2
    events = _events(cli)
    assert len(sends) == len(events) == 12                    # 4 calls x 3 parse attempts
    assert {event["attempt"] for event in events} == {0}
    suffixes = sorted(event["call_id"].rsplit(":p", 1)[-1] for event in events)
    assert suffixes == ["1", "1", "1", "1", "2", "2", "2", "2", "3", "3", "3", "3"]
    records = _records(cli.tmp / "out" / "scores.jsonl")
    assert {record["status"] for record in records} == {"parse_failure"}
    assert {record["n_attempts"] for record in records} == {3}


def test_transport_error_is_not_retried(cli, monkeypatch):
    """An HTTP error is one attempt: no hidden transport retry under the monitor config."""
    sends: list = []

    def post(self, payload):
        sends.append(payload)
        raise urllib.error.HTTPError(
            BASE_URL + "/chat/completions", 429, "rate limited", {}, io.BytesIO(b"slow down")
        )

    monkeypatch.setattr(ModelClient, "_post_json", post)
    _init_ledger(cli)
    assert cli.module.main(_argv(cli, "--views", "M1,M2,M3,M4")) == 2   # all views non-ok
    events = _events(cli)
    assert len(sends) == len(events) == 4                     # one attempt per logical call
    assert {event["attempt"] for event in events} == {0}
    records = _records(cli.tmp / "out" / "scores.jsonl")
    assert {record["status"] for record in records} == {"api_error"}


# ---------------------------------------------------------------- client guards


def test_monitor_client_is_pinned_to_one_attempt(cli, monkeypatch):
    """The guarded call site requests exactly one HTTP attempt per parse attempt."""
    _init_ledger(cli)
    calls = _install_fake_client(monkeypatch, cli)
    client, _store, config_hash = cli.module.open_guarded_client(_args(cli))
    assert calls["max_attempts"] == 1
    assert calls["base_url"] == BASE_URL
    assert calls["load_env"] is False
    assert client.guard is calls["guard"]
    assert config_hash == config_sha256(cli.config.read_bytes())


@pytest.mark.parametrize(
    "client_kwargs, match",
    [
        ({"max_attempts": 2}, "max_attempts"),
        ({"max_attempts": 0}, "max_attempts"),
        ({"max_attempts": True}, "max_attempts"),
        ({"omit_attempts": True}, "max_attempts"),          # strict default
        ({"provider_order": ("provider-a",)}, "routing"),
        ({"bound_to": object()}, "LedgerGuard"),
    ],
)
def test_client_guard_refuses_before_any_request(cli, monkeypatch, client_kwargs, match):
    """Every client-guard refusal happens before a request, so there is no spend."""
    _init_ledger(cli)
    _install_fake_client(monkeypatch, cli, **client_kwargs)
    with pytest.raises(cli.module.SpendConfigError, match=match):
        cli.module.open_guarded_client(_args(cli))
    assert cli.sends == []                    # no provider request was made
    # No reservation, so nothing was committed. (The run header is written before the
    # client exists, because a LedgerGuard can only be built for an open run.)
    assert _events(cli) == []


def test_cli_exits_three_when_the_client_guard_refuses(cli, monkeypatch, capsys):
    _init_ledger(cli)
    _install_fake_client(monkeypatch, cli, max_attempts=4)
    assert cli.module.main(_argv(cli, "--views", "M1,M2,M3,M4")) == 3
    assert cli.sends == []
    assert _events(cli) == []
    assert "max_attempts" in capsys.readouterr().err


# ---------------------------------------------------------------- endpoint preflight


def test_missing_base_url_is_refused_before_any_request(cli, monkeypatch, capsys):
    """No silent OpenAI fallback: an unset endpoint refuses before touching spend."""
    _init_ledger(cli)
    before = cli.ledger.read_bytes()
    monkeypatch.delenv("APART_BASE_URL", raising=False)
    assert cli.module.main(_argv(cli, "--views", "M1,M2,M3,M4")) == 3
    assert cli.sends == []                                  # zero provider requests
    assert cli.ledger.read_bytes() == before                # ledger not mutated
    assert not (cli.tmp / "out").exists()                   # no output directory created
    err = capsys.readouterr().err
    assert "APART_BASE_URL" in err
    assert "fallback" in err and "api.openai.com" in err


def test_base_url_validation_is_retained(cli, monkeypatch, capsys):
    """Existing URL validation still runs once the endpoint is explicit."""
    _init_ledger(cli)
    monkeypatch.setenv("APART_BASE_URL", "http://example.com/v1")   # not https
    assert cli.module.main(_argv(cli, "--views", "M1,M2,M3,M4")) == 3
    assert cli.sends == []
    assert "must be https" in capsys.readouterr().err


# ---------------------------------------------------------------- stop rule (unchanged)


def _flags(view: str, non_ok: int, ok: int) -> list:
    return ([{"view": view, "status": "parse_failure"} for _ in range(non_ok)]
            + [{"view": view, "status": "ok"} for _ in range(ok)])


def test_stop_rule_zero_non_ok():
    records = _flags("M1", 0, 35)
    assert non_ok_rate(records, "M1") == 0.0
    assert should_stop(records) is False


def test_stop_rule_below_ten_percent():
    records = _flags("M1", 9, 91)
    assert non_ok_rate(records, "M1") == pytest.approx(0.09)
    assert should_stop(records) is False


def test_stop_rule_exactly_ten_percent_does_not_stop():
    """The comparison is strict (>) as implemented today; =10% does not stop."""
    records = _flags("M1", 10, 90)
    assert non_ok_rate(records, "M1") == 0.1
    assert should_stop(records) is False


def test_stop_rule_above_ten_percent_stops():
    assert should_stop(_flags("M1", 11, 89)) is True


def test_stop_rule_absent_view_is_zero():
    records = _flags("M1", 5, 0)
    assert non_ok_rate(records, "M2") == 0.0
    assert non_ok_rate(records, "M4") == 0.0
    assert should_stop(records) is True            # M1 itself, not the absent views


def test_stop_rule_uses_present_rows_only():
    """Partial records are scored on the rows present; no expected-count check exists."""
    records = _flags("M1", 1, 2)                   # 3 rows of an expected 35
    assert non_ok_rate(records, "M1") == pytest.approx(1 / 3)
    assert should_stop(records) is True


def test_stop_rule_boundary_at_35_rows():
    assert should_stop(_flags("M1", 3, 32)) is False     # 8.57%
    assert should_stop(_flags("M1", 4, 31)) is True      # 11.43%


def test_stop_rule_counts_every_non_ok_status():
    for status in ("api_error", "parse_failure"):
        assert should_stop([{"view": "M1", "status": status}]) is True


# ---------------------------------------------------------------- M4 checklist (unchanged)


def test_m4_checklist_invalid_stays_ok_and_is_not_counted(cli):
    """A usable score with a malformed checklist keeps status ok; the defect stays visible."""
    payload = json.dumps({"score": 42, "rationale": "r", "checklist": {"messages": "bad"}})
    client = FakeClient(lambda messages, temperature, seed, index: payload)
    trace = trace_from_dict(json.loads(cli.corpus.read_text().splitlines()[0]))
    records, _ = run_corpus([trace], client, created_utc="2026-10-23T00:00:00Z", views=("M4",))
    assert len(records) == 1
    record = records[0]
    assert record["view"] == "M4"
    assert record["status"] == "ok"                # unchanged behaviour
    assert record["checklist_valid"] is False      # the defect is still recorded
    assert non_ok_rate(records, "M4") == 0.0       # not redefined as non-OK
    assert should_stop(records) is False


# ---------------------------------------------------------------- output exclusivity


def _score_records() -> list:
    return [
        {"seed": 1, "view": "M1", "k": 0, "status": "ok", "display_id": "case-a"},
        {"seed": 1, "view": "M2", "k": 0, "status": "ok", "display_id": "case-b"},
    ]


def test_write_scores_creates_a_new_file(tmp_path):
    path = tmp_path / "nested" / "scores.jsonl"
    write_scores(_score_records(), path)
    assert len(_records(path)) == 2


def test_write_scores_refuses_to_overwrite(tmp_path):
    path = tmp_path / "scores.jsonl"
    write_scores(_score_records(), path)
    original = path.read_bytes()
    with pytest.raises(FileExistsError) as excinfo:
        write_scores(_score_records(), path)
    assert str(path) in str(excinfo.value)          # actionable: names the existing path
    assert path.read_bytes() == original


def test_write_id_map_creates_a_new_file(tmp_path):
    path = tmp_path / "id_map.csv"
    write_id_map(_score_records(), path)
    lines = path.read_text().splitlines()
    assert lines[0] == "seed,view,display_id"
    assert len(lines) == 3


def test_write_id_map_refuses_to_overwrite(tmp_path):
    path = tmp_path / "id_map.csv"
    write_id_map(_score_records(), path)
    original = path.read_bytes()
    with pytest.raises(FileExistsError) as excinfo:
        write_id_map(_score_records(), path)
    assert str(path) in str(excinfo.value)
    assert path.read_bytes() == original


def test_cli_run_cannot_overwrite_previous_scores(cli):
    """A second run pointed at the same score path cannot clobber the first run's file."""
    _init_ledger(cli)
    out = cli.tmp / "out" / "scores.jsonl"
    assert cli.module.main(_argv(cli, "--views", "M1,M2,M3,M4", run_id="run-first")) == 0
    before = out.read_bytes()
    with pytest.raises(FileExistsError):
        cli.module.main(_argv(cli, "--views", "M1,M2,M3,M4", run_id="run-second",
                              raw_dir=cli.tmp / "second" / "raw"))
    assert out.read_bytes() == before


# ---------------------------------------------------------------- environment safety


def test_suite_never_reaches_the_real_env_loader(cli, monkeypatch):
    """The CLI's single loader call is intercepted; no real .env is read."""
    from monitors.client import load_apart_env as real_loader

    assert cli.module.load_apart_env is not real_loader        # fixture replaced it
    calls: list = []
    monkeypatch.setattr(cli.module, "load_apart_env", lambda *a, **k: calls.append(a))
    _init_ledger(cli)
    assert cli.module.main(_argv(cli, "--views", "M4")) == 0
    assert calls == [(ROOT / ".env",)]
