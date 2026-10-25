"""Regression guards for the frozen pilot (T1, risk register 2026-10-21).

Offline only: hashes, re-analysis and in-memory regeneration. Nothing in the
frozen tree is written; the reproduction test writes only to pytest's tmp_path.
Reference hashes are copied from docs/pilot_parameters.md; a test checks the doc
still lists them.

The pilot_v1 result files are untracked, so the reproduction test skips explicitly
when the authoritative score file is absent (Option 2, 2026-10-21).
"""

import hashlib
import json
from pathlib import Path

import pytest

import generate_corpus as gen
from apart import analysis as an

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "pilot_parameters.md"
SCORES = ROOT / "results" / "pilot_v1" / "scores_v1.jsonl"

FROZEN_INPUTS = {
    "data/frozen/pilot_v2_traces.jsonl": "cf7615e107b1be03e9ddd23e1b280a611b30fc0489945412d321e736dee201ac",
    "data/frozen/pilot_v2_labels.jsonl": "22e6457632051f5d749d2409fe53d72d1b96da5fee248e9b495c983b22e1008c",
    "data/frozen/gate_v2_traces.jsonl": "0d2db6bb629733d45f960731f7750ace7a34f2c2db928a2a865f7262b6aa7059",
    "data/frozen/gate_v2_labels.jsonl": "e3879004ccf4b995e823f3823951460b85a3f4cf96d1d50b29a2219082185354",
}

PILOT_RESULTS = {
    "results/pilot_v1/scores_v1.jsonl": "26241e384bc564ff596948f1f0144be60c5b11a496d6ceb9eb37325afcc41dc5",
    "results/pilot_v1/analysis_v1.json": "cd3485dc5ef09a6d38134e7a560b9fe0a880ca14f07bb5c9f8630e5fd1833f33",
    "results/pilot_v1/id_map_v1.csv": "c59c3830cf9f528152727d034741dcf3eb3fb66a916cc9fe0b49a1f0f60d9b8e",
    "results/pilot_v1/spend_guard.json": "c71ead6beeab33caacf3c11bbee386268c72e1aa0c3c2200e715169e624c5fc5",
}

FROZEN_SCHEMA_VERSION = "1.1"


def _sha256(rel: str) -> str:
    path = ROOT / rel
    assert path.is_file(), f"missing frozen artifact: {rel}"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _lines(rel: str) -> list[str]:
    return (ROOT / rel).read_text(encoding="utf-8").splitlines()


def test_reference_hashes_match_documentation():
    doc = DOC.read_text(encoding="utf-8")
    for rel, digest in {**FROZEN_INPUTS, **PILOT_RESULTS}.items():
        assert f"`{rel}` | `{digest}`" in doc, f"{rel} hash not recorded in {DOC.name}"


def test_frozen_input_hashes():
    for rel, digest in FROZEN_INPUTS.items():
        assert _sha256(rel) == digest, rel


def test_generator_reproduces_frozen_labels():
    built = gen.build_corpus(gen.PILOT_ROWS)
    assert [gen.json_line(gt.to_dict()) for _row, _trace, gt in built] == _lines(
        "data/frozen/pilot_v2_labels.jsonl"
    )


def test_generator_reproduces_frozen_traces_except_schema_version():
    """Only schema_version may differ (current code may be at a newer schema)."""
    built = gen.build_corpus(gen.PILOT_ROWS)
    frozen = [json.loads(line) for line in _lines("data/frozen/pilot_v2_traces.jsonl")]
    assert len(built) == len(frozen)
    for (_row, trace, _gt), stored in zip(built, frozen):
        regenerated = trace.to_dict()
        assert stored["schema_version"] == FROZEN_SCHEMA_VERSION
        regenerated.pop("schema_version")
        stored = dict(stored)
        stored.pop("schema_version")
        assert regenerated == stored, stored["trace_id"]


def test_frozen_files_carry_frozen_schema_version():
    for rel in ("data/frozen/pilot_v2_traces.jsonl", "data/frozen/gate_v2_traces.jsonl"):
        versions = {json.loads(line)["schema_version"] for line in _lines(rel)}
        assert versions == {FROZEN_SCHEMA_VERSION}, rel


@pytest.mark.skipif(not SCORES.is_file(), reason="authoritative pilot scores are untracked")
def test_frozen_analysis_reproduces_pinned_hash(tmp_path):
    out = tmp_path / "analysis.json"
    an.main([
        "--labels", str(ROOT / "data/frozen/pilot_v2_labels.jsonl"),
        "--scores", str(SCORES),
        "--out", str(out),
        # The frozen pilot is template-generated: it never had an adjudication
        # stage, so no label in it can be pending. Its rows predate y_h_state.
        "--legacy-labels", "assume_resolved",
    ])
    assert hashlib.sha256(out.read_bytes()).hexdigest() == (
        PILOT_RESULTS["results/pilot_v1/analysis_v1.json"]
    )
