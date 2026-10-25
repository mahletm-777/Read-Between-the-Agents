"""Tests for the monitor run status record (operational safeguard, offline).

Covers the guarantee that a run cannot be reported as completed before its
required outputs exist, that failed/stopped runs are visibly not completed, that
partial runs are distinguishable, that an existing status file is never
overwritten, and that the record carries the input/configuration metadata needed
to reproduce a run without any secret.

Offline only: `FakeClient`, temporary directories, monkeypatched collaborators,
and a blocked transport. No provider call, no ledger, no `.env` read.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import socket
import urllib.request
from pathlib import Path

import pytest

from monitors.run_status import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PLANNED,
    STATUS_RUNNING,
    STATUS_SCHEMA,
    STATUS_STOPPED,
    RunStatus,
    RunStatusError,
    build_metadata,
    default_status_path,
    read_status,
)

ROOT = Path(__file__).resolve().parents[1]
FROZEN_TRACE = ROOT / "data" / "frozen" / "pilot_v2_traces.jsonl"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No test in this module may reach the network."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """The monitor CLI module with the .env loader neutralised."""
    spec = importlib.util.spec_from_file_location(
        "run_monitors_run_status", ROOT / "scripts" / "run_monitors.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "load_apart_env", lambda *a, **k: 0)
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(FROZEN_TRACE.read_text().splitlines()[0] + "\n")
    return module, corpus


def _paths(tmp_path, name="out"):
    out = tmp_path / name / "scores.jsonl"
    return out, tmp_path / name / "id_map.csv", tmp_path / name / "raw"


def _argv(corpus, tmp_path, *extra, name="out", status=None):
    out, id_map, raw_dir = _paths(tmp_path, name)
    argv = [
        "--traces", str(corpus),
        "--out", str(out),
        "--id-map", str(id_map),
        "--raw-dir", str(raw_dir),
        "--views", "M1,M2,M3,M4",
        "--dry-run",
        *extra,
    ]
    if status is not None:
        argv += ["--status", str(status)]
    return argv


# ---- unit: state machine -----------------------------------------------------


def test_begin_creates_a_planned_record(tmp_path):
    path = tmp_path / "s.json"
    status = RunStatus.begin(path, run_id="run-1",
                             counts={"traces": 3, "records_expected": 12},
                             outputs={"scores": "results/scores.jsonl"})
    record = read_status(path)
    assert record["schema"] == STATUS_SCHEMA
    assert record["status"] == STATUS_PLANNED
    assert record["run_id"] == "run-1"
    assert record["partial"] is False
    assert record["counts"] == {"traces": 3, "records_expected": 12}
    assert record["attempt_policy"] == {"parse_attempts_max": 3, "http_attempts_max": 1}
    assert record["created_utc"] == record["updated_utc"]
    assert status.data == record


def test_begin_refuses_to_overwrite_an_existing_status_file(tmp_path):
    path = tmp_path / "s.json"
    RunStatus.begin(path, run_id="run-1")
    before = path.read_bytes()
    # same collision signal as the score/id-map/raw writers
    with pytest.raises(FileExistsError, match="already exists"):
        RunStatus.begin(path, run_id="run-2")
    assert path.read_bytes() == before
    assert read_status(path)["run_id"] == "run-1"


def test_running_requires_planned_and_happens_once(tmp_path):
    path = tmp_path / "s.json"
    status = RunStatus.begin(path, run_id="run-1")
    status.running()
    assert read_status(path)["status"] == STATUS_RUNNING
    with pytest.raises(RunStatusError, match="must be 'planned'"):
        status.running()


def test_completed_is_refused_while_a_required_output_is_missing(tmp_path):
    path = tmp_path / "s.json"
    scores = tmp_path / "scores.jsonl"
    id_map = tmp_path / "id_map.csv"
    scores.write_text("{}\n")
    status = RunStatus.begin(path, run_id="run-1",
                             counts={"traces": 1, "records_expected": 4})
    status.running()
    with pytest.raises(RunStatusError, match="required output\\(s\\) missing"):
        status.completed(records_written=4, required_outputs=[scores, id_map])
    assert read_status(path)["status"] == STATUS_RUNNING


def test_completed_is_refused_when_the_record_count_disagrees(tmp_path):
    path = tmp_path / "s.json"
    scores = tmp_path / "scores.jsonl"
    id_map = tmp_path / "id_map.csv"
    scores.write_text("{}\n")
    id_map.write_text("seed,view\n")
    status = RunStatus.begin(path, run_id="run-1",
                             counts={"traces": 1, "records_expected": 4})
    status.running()
    with pytest.raises(RunStatusError, match="expected 4"):
        status.completed(records_written=3, required_outputs=[scores, id_map])
    assert read_status(path)["status"] == STATUS_RUNNING


def test_completed_marks_a_complete_run(tmp_path):
    path = tmp_path / "s.json"
    scores = tmp_path / "scores.jsonl"
    id_map = tmp_path / "id_map.csv"
    scores.write_text("{}\n")
    id_map.write_text("seed,view\n")
    status = RunStatus.begin(path, run_id="run-1",
                             counts={"traces": 1, "records_expected": 4})
    status.running()
    status.completed(records_written=4, required_outputs=[scores, id_map])
    record = read_status(path)
    assert record["status"] == STATUS_COMPLETED
    assert record["partial"] is False
    assert record["exit_code"] == 0
    assert record["counts"]["records_written"] == 4


def test_terminal_states_are_final(tmp_path):
    for terminator in ("completed", "stopped", "failed"):
        path = tmp_path / f"{terminator}.json"
        scores = tmp_path / f"{terminator}.jsonl"
        scores.write_text("{}\n")
        status = RunStatus.begin(path, run_id="run-1")
        status.running()
        if terminator == "completed":
            status.completed(records_written=0, required_outputs=[scores])
        elif terminator == "stopped":
            status.stopped(reason="sanity rule")
        else:
            status.failed(error="boom")
        with pytest.raises(RunStatusError, match="already"):
            status.failed(error="second terminal write")


def test_failed_and_stopped_runs_are_not_completed(tmp_path):
    failed = RunStatus.begin(tmp_path / "f.json", run_id="run-1")
    failed.running()
    failed.failed(error="LedgerError: allocation exhausted", exit_code=4)
    record = read_status(tmp_path / "f.json")
    assert record["status"] == STATUS_FAILED
    assert record["status"] != STATUS_COMPLETED
    assert record["partial"] is True
    assert record["exit_code"] == 4
    assert "allocation exhausted" in record["error"]

    stopped = RunStatus.begin(tmp_path / "s.json", run_id="run-2")
    stopped.running()
    stopped.stopped(reason="non-ok rate above 10%", records_written=7)
    record = read_status(tmp_path / "s.json")
    assert record["status"] == STATUS_STOPPED
    assert record["partial"] is True
    assert record["counts"]["records_written"] == 7


def test_terminal_records_require_a_reason(tmp_path):
    status = RunStatus.begin(tmp_path / "s.json", run_id="run-1")
    status.running()
    with pytest.raises(RunStatusError, match="non-empty reason"):
        status.stopped(reason="   ")
    with pytest.raises(RunStatusError, match="non-empty error"):
        status.failed(error="")


def test_malformed_status_files_are_refused(tmp_path):
    junk = tmp_path / "junk.json"
    junk.write_text("not json at all")
    with pytest.raises(RunStatusError, match="not valid JSON"):
        read_status(junk)

    wrong_schema = tmp_path / "schema.json"
    wrong_schema.write_text(json.dumps({"schema": "other/9", "status": "completed"}))
    with pytest.raises(RunStatusError, match="unexpected schema"):
        read_status(wrong_schema)

    unknown_status = tmp_path / "status.json"
    unknown_status.write_text(json.dumps({"schema": STATUS_SCHEMA, "status": "finished"}))
    with pytest.raises(RunStatusError, match="unknown status"):
        read_status(unknown_status)

    with pytest.raises(RunStatusError, match="does not exist"):
        read_status(tmp_path / "absent.json")


def test_status_path_is_derived_from_the_run_output():
    assert default_status_path(Path("results/scores_v1.jsonl")) == Path(
        "results/scores_v1.jsonl.status.json"
    )
    assert default_status_path(Path("/tmp/x/a.jsonl")).name == "a.jsonl.status.json"


# ---- unit: metadata ---------------------------------------------------------


def test_metadata_records_the_inputs_without_secrets(tmp_path):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text('{"a": 1}\n')
    meta = build_metadata(traces_path=corpus, prompt_version="mp_v1",
                          system_prompt_sha256="ab" * 32, views=["M1", "M3"],
                          dry_run=True)
    assert meta["traces_sha256"] == hashlib.sha256(corpus.read_bytes()).hexdigest()
    assert meta["prompt_version"] == "mp_v1"
    assert meta["system_prompt_sha256"] == "ab" * 32
    assert meta["views"] == ["M1", "M3"]
    assert meta["dry_run"] is True
    assert meta["model"] == {"mode": "fake-client"}
    assert "budget" not in meta  # a dry run is not ledger-bound
    blob = json.dumps({k: v for k, v in meta.items() if "path" not in k}).lower()
    for banned in ("api_key", "apikey", "authorization", "bearer", "secret", "token", "sk-"):
        assert banned not in blob


def test_metadata_for_a_live_run_states_no_cap(tmp_path):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text('{"a": 1}\n')
    meta = build_metadata(traces_path=corpus, prompt_version="mp_v1",
                          system_prompt_sha256="ab" * 32, views=["M1"], dry_run=False,
                          provider="openai", model="m-1", base_url="https://example.invalid",
                          config_sha256="cd" * 32, allocation="phase_b", phase="phase_b")
    assert meta["model"] == {"provider": "openai", "model": "m-1",
                             "base_url": "https://example.invalid"}
    assert set(meta["budget"]) == {"config_sha256", "allocation", "phase", "note"}
    for absent in ("cap", "cap_usd", "limit", "budget_usd", "spend", "total"):
        assert absent not in meta["budget"]


# ---- CLI: the status record reflects what actually happened ------------------


def test_cli_dry_run_writes_a_completed_status(cli, tmp_path, capsys):
    module, corpus = cli
    out, id_map, _ = _paths(tmp_path)
    assert module.main(_argv(corpus, tmp_path)) == 0
    capsys.readouterr()
    record = read_status(default_status_path(out))
    assert record["status"] == STATUS_COMPLETED
    assert record["partial"] is False
    assert record["exit_code"] == 0
    assert record["run_id"] == "dry-run"
    assert record["counts"] == {"traces": 1, "records_expected": 4, "records_written": 4}
    assert record["metadata"]["dry_run"] is True
    assert record["metadata"]["model"] == {"mode": "fake-client"}
    assert record["metadata"]["prompt_version"]
    assert record["outputs"]["scores"] == str(out)
    assert record["outputs"]["id_map"] == str(id_map)
    assert out.exists() and id_map.exists()


def test_cli_records_a_failed_run_as_failed(cli, tmp_path, monkeypatch, capsys):
    module, corpus = cli
    out, id_map, _ = _paths(tmp_path)

    def boom(*_args, **_kwargs):
        raise module.LedgerError("allocation exhausted")

    monkeypatch.setattr(module, "run_corpus", boom)
    assert module.main(_argv(corpus, tmp_path)) == 4
    capsys.readouterr()
    record = read_status(default_status_path(out))
    assert record["status"] == STATUS_FAILED
    assert record["status"] != STATUS_COMPLETED
    assert record["partial"] is True
    assert record["exit_code"] == 4
    assert "allocation exhausted" in record["error"]
    # a run that produced no records writes no outputs
    assert not out.exists() and not id_map.exists()


def test_cli_records_a_stopped_run_as_not_completed(cli, tmp_path, monkeypatch, capsys):
    module, corpus = cli
    out, _, _ = _paths(tmp_path)
    monkeypatch.setattr(module, "should_stop", lambda records: True)
    assert module.main(_argv(corpus, tmp_path)) == 2
    capsys.readouterr()
    record = read_status(default_status_path(out))
    assert record["status"] == STATUS_STOPPED
    assert record["status"] != STATUS_COMPLETED
    assert record["partial"] is True
    assert record["exit_code"] == 2
    assert record["stop_reason"]
    assert record["counts"]["records_written"] == 4  # partial, but visibly so


def test_cli_refuses_an_existing_status_file_before_running(cli, tmp_path, capsys):
    module, corpus = cli
    out, id_map, _ = _paths(tmp_path)
    status_path = default_status_path(out)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text('{"kept": "from a previous run"}\n')
    before = status_path.read_bytes()
    with pytest.raises(FileExistsError, match="already exists"):
        module.main(_argv(corpus, tmp_path))
    assert status_path.read_bytes() == before      # previous run's record intact
    assert not out.exists() and not id_map.exists()  # nothing was executed
    assert not (tmp_path / "out" / "raw").exists()


def test_cli_raw_collision_is_recorded_and_still_refused(cli, tmp_path):
    """A fresh status path must not hide the raw-dir guard, and vice versa."""
    module, corpus = cli
    out, id_map, raw_dir = _paths(tmp_path)
    first = tmp_path / "first.status.json"
    second = tmp_path / "second.status.json"
    assert module.main(_argv(corpus, tmp_path, status=first)) == 0
    raw_before = sorted(p.name for p in raw_dir.iterdir())
    with pytest.raises(FileExistsError):
        module.main(_argv(corpus, tmp_path, status=second))
    record = read_status(second)
    assert record["status"] == STATUS_FAILED
    assert record["partial"] is True
    assert "raw output collision" in record["error"]
    # the first run's evidence is untouched, and its record still says completed
    assert sorted(p.name for p in raw_dir.iterdir()) == raw_before
    assert read_status(first)["status"] == STATUS_COMPLETED
    assert id_map.exists()


def test_cli_output_collision_fails_the_run_and_keeps_the_old_file(cli, tmp_path):
    module, corpus = cli
    out, id_map, _ = _paths(tmp_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('{"existing": true}\n')
    before = out.read_bytes()
    with pytest.raises(FileExistsError):
        module.main(_argv(corpus, tmp_path))
    record = read_status(default_status_path(out))
    assert record["status"] == STATUS_FAILED
    assert record["partial"] is True
    assert "collision" in record["error"]
    assert record["counts"]["records_written"] == 4
    assert out.read_bytes() == before   # no silent overwrite
    assert not id_map.exists()


def test_cli_writes_the_status_record_beside_explicit_output_paths(cli, tmp_path):
    module, corpus = cli
    status_path = tmp_path / "elsewhere" / "run.json"
    assert module.main(_argv(corpus, tmp_path, status=status_path)) == 0
    assert read_status(status_path)["status"] == STATUS_COMPLETED
