"""T3 S2e: run_monitors CLI hardening. tmp ledger patched in; stubbed transport; no network."""

import importlib.util
import json
from pathlib import Path

import pytest

from monitors.client import ModelClient
from monitors.ledger import LedgerStore, config_sha256

ROOT = Path(__file__).resolve().parents[1]
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
SCORE = {"choices": [{"message": {"content": '{"score": 10, "rationale": "ok"}'}}],
         "usage": {"prompt_tokens": 10, "completion_tokens": 5}, "model": "m1"}
CHECK = {"choices": [{"message": {"content": json.dumps({"checklist": {"messages": [], "speaker_match": [],
         "receiver_compliance": []}, "rationale": "ok", "score": 10})}}],
         "usage": {"prompt_tokens": 10, "completion_tokens": 5}, "model": "m1"}


def _load_cli():
    spec = importlib.util.spec_from_file_location("run_monitors_cli", ROOT / "scripts" / "run_monitors.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def env(tmp_path, monkeypatch):
    cli = _load_cli()
    ledger_path = tmp_path / "spend" / "ledger.json"
    monkeypatch.setattr(cli, "LEDGER_PATH", ledger_path)
    monkeypatch.setattr(cli, "load_apart_env", lambda *a, **k: 0)          # never read the real .env
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network access attempted")))
    for name in ("APART_MODEL", "APART_SPEND_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APART_API_KEY", "sk-test")
    monkeypatch.setenv("APART_BASE_URL", BASE_URL)
    config_path = tmp_path / "spend.json"
    config_path.write_text(json.dumps(CONFIG, sort_keys=True))
    traces = tmp_path / "traces.jsonl"
    traces.write_text((ROOT / "data" / "frozen" / "pilot_v2_traces.jsonl").read_text().splitlines()[0] + "\n")
    sends = []

    def post(self, payload):
        sends.append(payload)
        return CHECK if "CHECKLIST" in payload["messages"][-1]["content"] else SCORE

    monkeypatch.setattr(ModelClient, "_post_json", post)
    out = {"--out": tmp_path / "scores.jsonl", "--id-map": tmp_path / "id.csv", "--raw-dir": tmp_path / "raw"}
    return {"cli": cli, "ledger": ledger_path, "config": config_path, "traces": traces,
            "sends": sends, "out": out, "tmp": tmp_path}


def _argv(env, *extra, config=None):
    argv = ["--traces", str(env["traces"]), "--spend-config", str(config or env["config"])]
    for key, value in env["out"].items():
        argv += [key, str(value)]
    return argv + list(extra)


def _init(env):
    env["ledger"].parent.mkdir()
    LedgerStore(env["ledger"]).init(env["config"].read_bytes())


def test_missing_canonical_ledger_sends_nothing(env):
    code = env["cli"].main(_argv(env, "--allocation", "sprint", "--phase", "gate"))
    assert code == 3 and env["sends"] == []
    assert not env["ledger"].exists()


def test_config_hash_mismatch_sends_nothing(env):
    _init(env)
    other = env["tmp"] / "other.json"
    other.write_text(json.dumps(CONFIG, sort_keys=True, indent=1))       # same policy, different bytes
    before = env["ledger"].read_bytes()
    code = env["cli"].main(_argv(env, "--allocation", "sprint", "--phase", "gate", config=other))
    assert code == 3 and env["sends"] == []
    assert env["ledger"].read_bytes() == before


@pytest.mark.parametrize("extra", [
    ("--phase", "gate"),                                  # no allocation
    ("--allocation", "sprint"),                           # no phase
    ("--allocation", "sprint", "--phase", "pilot"),       # invalid pair
    ("--allocation", "pilot_audit", "--phase", "extension"),
])
def test_missing_allocation_or_invalid_pair_sends_nothing(env, extra):
    _init(env)
    before = env["ledger"].read_bytes()
    assert env["cli"].main(_argv(env, *extra)) == 3
    assert env["sends"] == [] and env["ledger"].read_bytes() == before


def test_ledger_option_no_longer_exists(env):
    _init(env)
    with pytest.raises(SystemExit):
        env["cli"].main(_argv(env, "--allocation", "sprint", "--phase", "gate", "--ledger", "x.json"))
    assert env["sends"] == []


def test_real_run_opens_with_backup_sweeps_and_records_provenance(env):
    _init(env)
    code = env["cli"].main(_argv(env, "--allocation", "sprint", "--phase", "gate", "--run-id", "run-t"))
    assert code == 0
    store = LedgerStore(env["ledger"])
    ledger = store.load(expected_config_hash=config_sha256(env["config"].read_bytes()))
    header = ledger["runs"]["run-t"]
    assert header["allocation_id"] == "sprint" and header["phase"] == "gate"
    assert header["provenance"]["model"] == "prov/m1" and header["provenance"]["base_url"] == BASE_URL
    assert "sk-test" not in env["ledger"].read_text()
    assert len(env["sends"]) == len(ledger["events"]) == 10
    assert all(e["state"] == "settled" for e in ledger["events"])
    assert list(store.backup_dir.glob("ledger.*.json"))                 # run-open backup exists


def test_budget_stop_exits_4_after_no_unreserved_sends(env, monkeypatch):
    _init(env)
    no_usage = lambda self, payload: env["sends"].append(payload) or dict(SCORE, usage=None)
    monkeypatch.setattr(ModelClient, "_post_json", no_usage)
    code = env["cli"].main(_argv(env, "--allocation", "sprint", "--phase", "smoke", "--run-id", "run-s"))
    assert code == 4
    events = LedgerStore(env["ledger"]).load()["events"]
    assert len(env["sends"]) == len(events) < 10


def test_dry_run_touches_no_ledger_and_no_network(env):
    code = env["cli"].main(["--traces", str(env["traces"]), "--dry-run",
                            *sum(([k, str(v)] for k, v in env["out"].items()), [])])
    assert code == 0 and env["sends"] == []
    assert not env["ledger"].exists() and not env["ledger"].parent.exists()
