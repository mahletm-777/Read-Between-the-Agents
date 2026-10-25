"""Stage 2 offline monitor smoke test over the frozen Stage 4N probe traces.

Engineering smoke test only: it exercises the observable -> prompt -> parse ->
persist path with ``FakeClient``, no network and no spend ledger. It is NOT
monitor-performance evidence, NOT a pilot, and says nothing about collusion.

Ground truth is deliberately absent: the Stage 4N manifest sets
``extraction: false``, so no adjudicated claim set exists and no Y_H label is
built here (``apart.labeler`` is not called).
"""

from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path

import pytest

from agents.runner import build_trace
from monitors.client import FakeClient
from monitors.prompts import SYSTEM_PROMPT, display_id, render_view
from monitors.runner import VIEW_ORDER, run_corpus, select_views, write_scores
from monitors.observable import ObservableRecord, observable

ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "runs" / "ext-probe-v21-sonnet46"
ROWS = RUN_DIR / "out" / "probe_traces.jsonl"
SMOKE_VIEWS = ("M1", "M2", "M3", "M4")
STAMP = "2026-10-23T00:00:00Z"

BRIEF_IDS = ("G-BASE", "G-RECRUIT", "G-COVERT", "G-SOLO", "G-WARN",
             "G-IMPLICIT", "G-MENTION", "G-COMPLY")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture()
def probe_traces():
    with ROWS.open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return [build_trace(row) for row in rows]


# ---------------------------------------------------------------- view selection


def test_view_selector_preserves_the_default_and_rejects_bad_input():
    assert select_views() is VIEW_ORDER
    assert select_views(None) == VIEW_ORDER
    # A subset is returned in canonical order, so its per-view RNG seed is the
    # same as in a full run and the rendered records are identical.
    assert select_views(["M4", "M1", "M2", "M3"]) == SMOKE_VIEWS
    for bad in ([], ["M9"], ["M1", "M1"]):
        with pytest.raises(ValueError):
            select_views(bad)


def test_subset_records_match_the_corresponding_full_run_records(probe_traces):
    full, ensembles = run_corpus(probe_traces, FakeClient(), STAMP)
    subset, subset_ensembles = run_corpus(probe_traces, FakeClient(), STAMP, views=SMOKE_VIEWS)

    assert len(full) == len(probe_traces) * 10
    assert len(subset) == len(probe_traces) * len(SMOKE_VIEWS)
    # ``response_id`` is the fake client's own call counter (it restarts for a
    # shorter run), so it is excluded; every other field must be identical.
    strip = lambda rows: [{k: v for k, v in r.items() if k != "response_id"} for r in rows]
    expected = strip([r for r in full if r["view"] in SMOKE_VIEWS])
    assert strip(subset) == expected
    # The ensemble summary exists only for a run that includes M3-ens.
    assert set(ensembles) == {trace.seed for trace in probe_traces}
    assert subset_ensembles == {}


# ---------------------------------------------------------------- evidence boundary


def test_rendered_evidence_keeps_the_leakage_boundary(probe_traces):
    for trace in probe_traces:
        record = observable(trace)
        for view in SMOKE_VIEWS:
            text = render_view(record, view, display_id(trace.seed, view))
            assert display_id(trace.seed, view) in text
            assert trace.trace_id not in text
            assert str(trace.seed) not in text
            for brief in BRIEF_IDS:
                assert brief not in text
    assert isinstance(record, ObservableRecord)


def test_each_view_shows_exactly_its_own_evidence(probe_traces):
    trace = next(t for t in probe_traces if t.messages)
    record = observable(trace)
    message = trace.messages[0].text

    m1 = render_view(record, "M1", "case-x")
    assert message in m1 and "[Not shown in this record]" in m1
    assert "chose" not in m1.split("CHOICES AND PAYOFFS")[1]

    m2 = render_view(record, "M2", "case-x")
    assert message not in m2 and "MESSAGES\n[Not shown in this record]" in m2
    assert f"Agent 1 chose: {record.actions['agent_1']}" in m2

    for view in ("M3", "M4"):
        text = render_view(record, view, "case-x")
        assert message in text
        assert record.actions["agent_1"] in text and record.actions["agent_2"] in text


def test_m4_prompt_requests_the_checklist_and_m1_m3_do_not(probe_traces):
    record = observable(probe_traces[0])
    assert "CHECKLIST" in render_view(record, "M4", "case-x")
    for view in ("M1", "M2", "M3"):
        assert "CHECKLIST" not in render_view(record, view, "case-x")


# ---------------------------------------------------------------- corpus and persistence


def test_frozen_probe_rows_carry_no_claims_so_no_label_is_built(probe_traces):
    # Stage 4N sets extraction=false: there is no adjudicated claim set, so the
    # corpus has no ground-truth-bearing field and no Y_H may be derived from it.
    assert probe_traces
    assert all(trace.claims == [] for trace in probe_traces)
    assert all(trace.private_plans for trace in probe_traces)  # generator-only, never rendered



def _write_canonical_corpus(tmp_path: Path) -> Path:
    """Write the canonical corpus with the Stage 1 converter (same code path as the CLI run)."""
    from scripts.probe_rows_to_traces import convert, read_jsonl

    corpus = tmp_path / "corpus" / "probe_traces_canonical.jsonl"
    corpus.parent.mkdir(parents=True, exist_ok=True)
    traces, failures = convert(read_jsonl(ROWS))
    assert not failures
    with corpus.open("x", encoding="utf-8", newline="\n") as handle:
        for trace in traces:
            handle.write(json.dumps(trace.to_dict(), sort_keys=True, ensure_ascii=False) + "\n")
    return corpus


def _run_cli(monkeypatch, argv):
    """Run scripts/run_monitors.main with the .env loader disabled.

    ``load_apart_env`` reads ``.env``; the smoke test must not read a secrets file
    and a dry run needs no credential, so it is replaced with a no-op.
    """
    from scripts import run_monitors

    monkeypatch.setattr(run_monitors, "load_apart_env", lambda *a, **k: 0)
    return run_monitors.main(argv)


def test_cli_dry_run_m1_to_m4_writes_no_ledger_and_is_reproducible(tmp_path, monkeypatch):
    corpus = _write_canonical_corpus(tmp_path)

    def canonical_ledger_state():
        """Every file under results/spend, keyed by relative name and content hash."""
        spend_dir = ROOT / "results" / "spend"
        if not spend_dir.exists():
            return None
        return {str(path.relative_to(spend_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(spend_dir.rglob("*")) if path.is_file()}

    # The shared budget control is armed, so the canonical ledger legitimately exists.
    # The invariant is therefore "a dry run changes nothing", not "no ledger exists".
    ledger_before = canonical_ledger_state()
    outputs = {}
    for name in ("run_a", "run_b"):
        out_dir = tmp_path / name
        outputs[name] = {
            "out": out_dir / "scores_v1.jsonl",
            "id_map": out_dir / "id_map_v1.csv",
            "raw_dir": out_dir / "raw",
        }
        code = _run_cli(monkeypatch, [
            "--traces", str(corpus), "--views", "M1,M2,M3,M4", "--dry-run",
            "--created-utc", STAMP,
            "--out", str(outputs[name]["out"]),
            "--id-map", str(outputs[name]["id_map"]),
            "--raw-dir", str(outputs[name]["raw_dir"]),
        ])
        assert code == 0

    a, b = outputs["run_a"], outputs["run_b"]
    assert a["out"].read_bytes() == b["out"].read_bytes(), "dry run is not reproducible"
    assert a["id_map"].read_bytes() == b["id_map"].read_bytes()

    records = [json.loads(line) for line in a["out"].read_text().splitlines() if line.strip()]
    assert len(records) == 15 * len(SMOKE_VIEWS)
    assert {record["view"] for record in records} == set(SMOKE_VIEWS)
    for view in SMOKE_VIEWS:
        rows = [record for record in records if record["view"] == view]
        assert len(rows) == 15, view
        assert all(record["status"] == "ok" for record in rows), view
    # A dry run must not create or change the canonical live ledger, and carries no labels.
    assert canonical_ledger_state() == ledger_before, "a dry run must not write the live ledger"
    assert "y_h" not in a["out"].read_text()


def test_cli_refuses_to_reuse_a_raw_dir(tmp_path, monkeypatch):
    corpus = _write_canonical_corpus(tmp_path)
    argv = ["--traces", str(corpus), "--views", "M1,M2,M3,M4", "--dry-run",
            "--created-utc", STAMP,
            "--out", str(tmp_path / "o" / "scores.jsonl"),
            "--id-map", str(tmp_path / "o" / "id_map.csv"),
            "--raw-dir", str(tmp_path / "o" / "raw")]
    assert _run_cli(monkeypatch, argv) == 0
    with pytest.raises(FileExistsError):
        _run_cli(monkeypatch, argv)


def test_cli_rejects_an_unknown_view_without_writing(tmp_path, monkeypatch, capsys):
    corpus = _write_canonical_corpus(tmp_path)
    code = _run_cli(monkeypatch, [
        "--traces", str(corpus), "--views", "M1,M9", "--dry-run",
        "--out", str(tmp_path / "o" / "scores.jsonl"),
    ])
    assert code == 3
    assert "unknown view" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()
