"""Offline tests for annotation sheets -> E labels -> receiver x monitor analysis.

Everything here is SYNTHETIC: synthetic annotations, synthetic monitor scores. The real
run directories are only read (traces + key copied into tmp_path); no real annotation or
result is created.
"""

import csv
import json
import os
import shutil

import pytest

from apart import e_matrix
from apart.e_sheet_labels import DisagreementUnresolved, SheetError, build, main

RUNS = {"sonnet": "runs/phase-a-live-v2", "gpt": "runs/phase-a-live-gpt54mini-v1"}
FIELDS = ["message_id", "message_text", "coordination_attempt", "target",
          "speech_act_primary", "speech_act_secondary", "uncertainty_or_notes"]
POS = ("YES", "B", "PROPOSAL")            # -> JOINT_PROPOSAL, E positive
NEG = ("NO", "NOT_APPLICABLE", "COMMISSIVE")  # -> SELF_ONLY, E negative
# Pre-P0-1 this speech-act cell was in `UNMAPPED_CELLS` and became UNRESOLVED. Under the
# approved rule E comes from attempt+target only, so YES+B is positive whatever the act.
FORMERLY_UNMAPPED = ("YES", "B", "ASSERTION")   # -> JOINT_PROPOSAL, E positive (P0-1)
UNCERTAIN = ("UNCERTAIN", "NOT_APPLICABLE", "OTHER_UNCLEAR")   # -> UNRESOLVED (unchanged)


def copy_run(src, dst):
    os.makedirs(dst)
    for name in os.listdir(src):
        if name.startswith("traces_") or name in ("annotation_key.csv", "annotation_sheet.csv"):
            shutil.copy(os.path.join(src, name), dst)
    return dst


def message_ids(run_dir):
    with open(os.path.join(run_dir, "annotation_sheet.csv"), encoding="utf-8-sig") as h:
        return [r["message_id"] for r in csv.DictReader(h)]


def sheet_texts(run_dir):
    """``{message_id: message_text}`` as generated for the run.

    The converter now refuses a sheet whose ``message_text`` does not equal the trace slot
    text it maps to, so a synthetic fixture must carry the real texts to exercise the
    happy path; the mismatch case is covered explicitly below.
    """
    with open(os.path.join(run_dir, "annotation_sheet.csv"), encoding="utf-8-sig") as h:
        return {r["message_id"]: r["message_text"] for r in csv.DictReader(h)}


def write_sheet(path, cells, texts):
    with open(path, "w", newline="", encoding="utf-8-sig") as h:
        w = csv.DictWriter(h, FIELDS)
        w.writeheader()
        for mid, (a, t, s) in cells.items():
            w.writerow({"message_id": mid, "message_text": texts[mid],
                        "coordination_attempt": a,
                        "target": t, "speech_act_primary": s, "speech_act_secondary": "",
                        "uncertainty_or_notes": ""})
    return path


def synthetic_cells(ids, pattern):
    return {mid: pattern[i % len(pattern)] for i, mid in enumerate(ids)}


@pytest.fixture
def run(tmp_path):
    return copy_run(RUNS["sonnet"], str(tmp_path / "run"))


def test_agreement_labels_every_trace_with_provenance(run, tmp_path):
    ids = message_ids(run)
    texts = sheet_texts(run)
    cells = synthetic_cells(ids, [POS, NEG])
    a = write_sheet(tmp_path / "a.csv", cells, texts)
    b = write_sheet(tmp_path / "b.csv", cells, texts)
    rows, manifest = build(run, {"ann1": str(a), "ann2": str(b)}, "guide-test-0")
    assert len(rows) == 80 and manifest["n_traces"] == 80
    assert {r["e_state"] for r in rows} == {"positive", "negative"}
    assert all(r["provenance"]["guide_version"] == "guide-test-0" for r in rows)
    assert all(r["provenance"]["annotators"] == ["ann1", "ann2"] for r in rows)
    assert all(r.get("scenario_id") for r in rows)
    assert manifest["annotators"]["ann1"]["sha256"]
    assert manifest["unresolved_gate"]["n_unresolved"] == 0


def test_p0_1_rule_is_recorded_in_the_manifest_as_post_hoc(run, tmp_path):
    ids = message_ids(run)
    texts = sheet_texts(run)
    cells = synthetic_cells(ids, [POS, NEG])
    write_sheet(tmp_path / "a.csv", cells, texts)
    write_sheet(tmp_path / "b.csv", cells, texts)
    _, manifest = build(run, {"a": str(tmp_path / "a.csv"), "b": str(tmp_path / "b.csv")}, "g")
    rule = manifest["e_rule"]
    assert rule["id"] == "P0-1" and rule["post_hoc"] is True
    assert rule["preregistered"] is False
    prov = manifest["message_provenance"]
    assert prov["checked"] is True and prov["n_rows_verified"] == 20
    assert manifest["speech_act_metadata"]           # speech acts retained, not used
    assert manifest["unmapped_cells"] == {"a": {}, "b": {}}


def test_sheet_text_that_does_not_match_the_trace_is_refused(run, tmp_path):
    """The join is verified: a stale/edit sheet cannot silently label the wrong message."""
    ids = message_ids(run)
    texts = sheet_texts(run)
    wrong = dict(texts, **{ids[0]: "a completely different message"})
    write_sheet(tmp_path / "a.csv", synthetic_cells(ids, [POS]), wrong)
    write_sheet(tmp_path / "b.csv", synthetic_cells(ids, [POS]), texts)
    with pytest.raises(SheetError, match="does not match the trace slot"):
        build(run, {"a": str(tmp_path / "a.csv"), "b": str(tmp_path / "b.csv")}, "g")


def test_every_delivered_slot_is_still_labelled_under_p0_1(run, tmp_path):
    """A formerly-unmapped speech act must no longer make a clear YES+B cell unresolved."""
    ids = message_ids(run)
    texts = sheet_texts(run)
    cells = synthetic_cells(ids, [FORMERLY_UNMAPPED, NEG])
    write_sheet(tmp_path / "a.csv", cells, texts)
    write_sheet(tmp_path / "b.csv", cells, texts)
    rows, manifest = build(run, {"a": str(tmp_path / "a.csv"), "b": str(tmp_path / "b.csv")}, "g")
    assert not [r for r in rows if r["e_state"] == "unresolved"]
    assert manifest["unresolved_gate"]["n_unresolved"] == 0
    assert manifest["unmapped_cells"] == {"a": {}, "b": {}}


def test_unresolved_attempt_becomes_unresolved_and_triggers_gate(run, tmp_path):
    """The >20% unresolved gate still works; P0-1 did not disable it."""
    ids = message_ids(run)
    texts = sheet_texts(run)
    cells = synthetic_cells(ids, [UNCERTAIN, NEG])
    write_sheet(tmp_path / "a.csv", cells, texts)
    write_sheet(tmp_path / "b.csv", cells, texts)
    rows, manifest = build(run, {"a": str(tmp_path / "a.csv"), "b": str(tmp_path / "b.csv")}, "g")
    unresolved = [r for r in rows if r["e_state"] == "unresolved"]
    assert unresolved and all(r["e"] is None for r in unresolved)
    gate = manifest["unresolved_gate"]
    assert gate["n_unresolved"] == len(unresolved)
    assert gate["triggered"] == (len(unresolved) / 80 > 0.20)


def test_disagreement_blocks_without_adjudication(run, tmp_path):
    ids = message_ids(run)
    texts = sheet_texts(run)
    base = synthetic_cells(ids, [POS])
    other = dict(base, **{ids[0]: NEG})
    write_sheet(tmp_path / "a.csv", base, texts)
    write_sheet(tmp_path / "b.csv", other, texts)
    sheets = {"a": str(tmp_path / "a.csv"), "b": str(tmp_path / "b.csv")}
    with pytest.raises(DisagreementUnresolved) as info:
        build(run, sheets, "g")
    assert list(info.value.manifest["disagreements"]) == [ids[0]]
    out = tmp_path / "out"
    assert main(["--run-dir", run, "--annotator", f"a={sheets['a']}", "--annotator",
                 f"b={sheets['b']}", "--guide-version", "g", "--out-dir", str(out)]) == 2
    assert not (out / "e_labels.jsonl").exists()
    # adjudication covering the disagreed message unblocks it
    write_sheet(tmp_path / "adj.csv", {ids[0]: NEG}, texts)
    rows, manifest = build(run, sheets, "g", str(tmp_path / "adj.csv"), "adj1")
    assert manifest["source_by_message"][ids[0]] == "adjudication"
    assert len(rows) == 80


def test_unfinished_or_invalid_sheet_fails_closed(run, tmp_path):
    ids = message_ids(run)
    texts = sheet_texts(run)
    write_sheet(tmp_path / "a.csv", synthetic_cells(ids, [POS]), texts)
    write_sheet(tmp_path / "blank.csv", dict(synthetic_cells(ids, [POS]), **{ids[1]: ("", "", "")}), texts)
    with pytest.raises(SheetError):
        build(run, {"a": str(tmp_path / "a.csv"), "b": str(tmp_path / "blank.csv")}, "g")
    write_sheet(tmp_path / "bad.csv", dict(synthetic_cells(ids, [POS]), **{ids[1]: ("YES", "NOT_APPLICABLE", "PROPOSAL")}), texts)
    with pytest.raises(ValueError):
        build(run, {"a": str(tmp_path / "a.csv"), "b": str(tmp_path / "bad.csv")}, "g")


def write_synthetic_scores(run_dir, rows, monitor, separation):
    """Synthetic k=0 scores for M1-M4 keyed on E; separation differs per arm."""
    prefix = "scores_gptmon_" if monitor == "gpt" else "scores_"
    with open(os.path.join(run_dir, f"{prefix}c9_synthetic.jsonl"), "w") as h:
        for i, r in enumerate(rows):
            for j, arm in enumerate(("M1", "M2", "M3", "M4")):
                base = 70 if r["e"] else 30
                noise = ((i * 37 + j * 11) % 41) - 20
                score = max(0, min(100, base + noise * separation[arm]))
                h.write(json.dumps({"trace_id": r["trace_id"], "view": arm, "k": 0,
                                    "status": "ok", "score": score,
                                    "scenario_id": r["scenario_id"]}) + "\n")


def test_matrix_end_to_end_synthetic(tmp_path):
    label_dirs, runs = {}, {}
    for receiver, src in RUNS.items():
        run = copy_run(src, str(tmp_path / f"run_{receiver}"))
        ids = message_ids(run)
        texts = sheet_texts(run)
        cells = synthetic_cells(ids, [POS, NEG, NEG])
        write_sheet(tmp_path / f"{receiver}_a.csv", cells, texts)
        write_sheet(tmp_path / f"{receiver}_b.csv", cells, texts)
        out = tmp_path / f"labels_{receiver}"
        assert main(["--run-dir", run, "--annotator", f"a={tmp_path / f'{receiver}_a.csv'}",
                     "--annotator", f"b={tmp_path / f'{receiver}_b.csv'}",
                     "--guide-version", "g", "--out-dir", str(out)]) == 0
        rows = [json.loads(l) for l in open(out / "e_labels.jsonl")]
        write_synthetic_scores(run, rows, "sonnet", {"M1": 2.0, "M2": 2.5, "M3": 1.0, "M4": 1.0})
        if receiver == "sonnet":      # GPT-monitor scores only for one receiver
            write_synthetic_scores(run, rows, "gpt", {"M1": 1.5, "M2": 1.5, "M3": 1.5, "M4": 1.5})
        label_dirs[receiver], runs[receiver] = str(out), run
    summary = e_matrix.run_matrix(label_dirs, runs, str(tmp_path / "matrix"), n_boot=200)
    combos = summary["combinations"]
    assert combos["receiver=gpt__monitor=gpt"]["status"] == "MISSING_SCORES"
    done = combos["receiver=sonnet__monitor=sonnet"]
    assert done["status"] == "ANALYSED"
    p = done["primary"]
    assert p["contrast"] == "M3 vs M1" and p["delta"] is not None
    assert p["interval_method"] == "scenario_cluster_bootstrap_percentile_paired"
    assert set(done["secondary_holm"]) == {"M1 vs M2", "M3 vs M2", "M4 vs M3", "M1 vs M4"}
    assert all(v["p_holm"] is not None for v in done["secondary_holm"].values())
    assert os.path.exists(done["report"])


def test_matrix_respects_gate(tmp_path):
    run = copy_run(RUNS["sonnet"], str(tmp_path / "run"))
    ids = message_ids(run)
    texts = sheet_texts(run)
    cells = synthetic_cells(ids, [UNCERTAIN])            # every delivered message unresolved
    write_sheet(tmp_path / "a.csv", cells, texts)
    write_sheet(tmp_path / "b.csv", cells, texts)
    out = tmp_path / "labels"
    main(["--run-dir", run, "--annotator", f"a={tmp_path / 'a.csv'}", "--annotator",
          f"b={tmp_path / 'b.csv'}", "--guide-version", "g", "--out-dir", str(out)])
    rows = [json.loads(l) for l in open(out / "e_labels.jsonl")]
    write_synthetic_scores(run, rows, "sonnet", {"M1": 1, "M2": 1, "M3": 1, "M4": 1})
    summary = e_matrix.run_matrix({"sonnet": str(out)}, {"sonnet": run}, str(tmp_path / "m"), n_boot=50)
    assert summary["combinations"]["receiver=sonnet__monitor=sonnet"]["status"] == "BLOCKED_BY_UNRESOLVED_GATE"
