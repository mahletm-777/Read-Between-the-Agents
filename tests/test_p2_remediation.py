"""P2 remediation regressions: adjudication integrity, write-back, C_set, selection, messages."""

import importlib.util
import json
from pathlib import Path

import pytest

from apart.extension import audited_selection
from extraction.adjudicate import adjudicate
from extraction.client import FakeExtractorClient, build_extractor_request
from extraction.prompts import (EXTRACTOR_PROMPT_SHA256, GAME_CONTEXT, GAME_CONTEXT_SHA256,
                                extractor_input)
from extraction.writeback import (AdjudicationError, apply_adjudication, apply_log,
                                  load_adjudications, normalize_decision)
from monitors.ledger import LedgerError, c_set_micro, c_set_usd

ROOT = Path(__file__).resolve().parents[1]
TEXT = "We should both choose B."
GOOD = {"claims": [{"target": "B", "type": "proposal", "confidence": "clear"}],
        "attempt_type": "EXPLICIT", "coordination_attempt": "yes",
        "directness": "DIRECT", "speech_act_types": ["PROPOSAL"]}


# ---------------------------------------------------------------- adjudication integrity

def test_ambiguous_claim_is_not_auto_accepted():
    amb = dict(GOOD, claims=[{"target": "B", "type": "proposal", "confidence": "ambiguous"}])
    out = adjudicate(amb, dict(amb), message_text=TEXT)
    assert out["claims_ok"] is False and out["claims"] is None
    assert out["claims_status"] == "ambiguous" and out["needs_adjudication"]
    assert "ambiguous claim" in out["reason"]


def test_invalid_confidence_is_not_auto_accepted():
    bad = dict(GOOD, claims=[{"target": "B", "type": "proposal", "confidence": "certain"}])
    out = adjudicate(bad, dict(bad), message_text=TEXT)
    assert out["claims_status"] == "invalid" and out["needs_adjudication"]


def test_message_over_40_words_goes_to_adjudication():
    short = adjudicate(GOOD, dict(GOOD), message_text=" ".join(["w"] * 40))
    long = adjudicate(GOOD, dict(GOOD), message_text=" ".join(["w"] * 41))
    assert short["needs_adjudication"] is False
    assert long["needs_adjudication"] and long["message_over_word_limit"]
    assert "40 words" in long["reason"]


def test_missing_message_text_fails_closed():
    out = adjudicate(GOOD, dict(GOOD))
    assert out["needs_adjudication"] and "message text missing" in out["reason"]


def test_missing_coordination_attempt_is_not_derived():
    e = {"claims": GOOD["claims"], "attempt_type": "EXPLICIT"}
    out = adjudicate(e, dict(e), message_text=TEXT)
    assert out["coordination_status"] == "invalid" and out["needs_adjudication"]


def test_coordination_attempt_must_match_attempt_group():
    bad = dict(GOOD, coordination_attempt="no")
    out = adjudicate(bad, dict(bad), message_text=TEXT)
    assert out["coordination_status"] == "mismatch" and out["needs_adjudication"]


def test_attempt_subtype_disagreement_is_logged_not_adjudicated():
    out = adjudicate(GOOD, dict(GOOD, attempt_type="IMPLICIT"), message_text=TEXT)
    assert out["attempt_ok"] is True and out["attempt_type"] == "EXPLICIT"
    assert out["needs_adjudication"] is False
    assert any("subtype" in item for item in out["logged_disagreements"])


def test_claim_type_within_class_is_logged_not_adjudicated():
    other = dict(GOOD, claims=[{"target": "B", "type": "commitment", "confidence": "clear"}])
    out = adjudicate(GOOD, other, message_text=TEXT)
    assert out["claims_ok"] is True and out["needs_adjudication"] is False
    assert any("claim type" in item for item in out["logged_disagreements"])


def test_cross_class_claim_type_disagreement_goes_to_adjudication():
    other = dict(GOOD, claims=[{"target": "B", "type": "directive", "confidence": "clear"}])
    out = adjudicate(GOOD, other, message_text=TEXT)
    assert out["claims_status"] == "disagreed" and out["needs_adjudication"]


def test_original_extractor_outputs_are_preserved():
    out = adjudicate(GOOD, dict(GOOD, directness="AMBIGUOUS"), message_text=TEXT)
    assert out["extractor_codes"]["E1"]["claims"] == GOOD["claims"]
    assert out["extractor_codes"]["E2"]["directness"] == "AMBIGUOUS"


# ---------------------------------------------------------------- write-back

def _auto(needs: bool = True) -> dict:
    return {
        "claims_ok": not needs, "claims": None if needs else GOOD["claims"],
        "attempt_ok": not needs, "attempt_type": None if needs else "EXPLICIT",
        "coordination_attempt": None if needs else "yes",
        "directness": None if needs else "DIRECT",
        "speech_act_types": None if needs else ["PROPOSAL"],
        "needs_adjudication": needs, "reason": "claim disagreement",
        "extractor_codes": {"E1": {"attempt_type": "EXPLICIT"}, "E2": {"attempt_type": "IMPLICIT"}},
    }


def _original(message_id: str = "m1", needs: bool = True) -> dict:
    return {
        "message_id": message_id, "seed": 1000, "text": TEXT, "message_status": "delivered",
        "E1": {"claims": GOOD["claims"], "attempt_type": "EXPLICIT"},
        "E2": {"claims": [], "attempt_type": "IMPLICIT"},
        "adjudication": _auto(needs),
    }


def _decision(**overrides) -> dict:
    base = {
        "message_id": "m1", "coordination_attempt": "yes", "attempt_type": "EXPLICIT",
        "directness": "DIRECT", "speech_act_types": ["PROPOSAL"], "claims": GOOD["claims"],
        "adjudication_reason": "human read", "adjudicated_by": "MM", "adjudication_id": "a1",
    }
    base.update(overrides)
    return base


def test_writeback_resolves_and_records_changed_fields(tmp_path):
    log = tmp_path / "adj.jsonl"
    log.write_text(json.dumps(_decision()) + "\n", encoding="utf-8")
    decisions, digest = load_adjudications(log)
    out = apply_adjudication(_original(), decisions["m1"], log_hash=digest)
    assert out["annotation_status"] == "resolved"
    assert out["claims"] == GOOD["claims"]
    assert set(out["changed_fields"]) == {"claims", "attempt_type", "coordination_attempt",
                                          "directness", "speech_act_types"}
    assert out["adjudicated_by"] == "MM" and out["adjudication_log_hash"] == digest
    assert out["auto"]["reason"] == "claim disagreement"     # automatic result preserved
    assert out["seed"] == 1000                                # original record preserved


def test_writeback_requires_reason_and_attribution():
    with pytest.raises(AdjudicationError):
        normalize_decision(_decision(adjudication_reason=""), 1)
    with pytest.raises(AdjudicationError):
        normalize_decision(_decision(adjudicated_by=""), 1)


def test_writeback_rejects_inconsistent_attempt():
    with pytest.raises(AdjudicationError, match="contradicts"):
        normalize_decision(_decision(coordination_attempt="no"), 1)


def test_writeback_rejects_out_of_codebook_values():
    with pytest.raises(AdjudicationError):
        normalize_decision(_decision(speech_act_types=["BOGUS"]), 1)
    with pytest.raises(AdjudicationError):
        normalize_decision(_decision(directness="BOGUS"), 1)


def test_writeback_rejects_conflicting_duplicate_decisions(tmp_path):
    log = tmp_path / "adj.jsonl"
    conflict = _decision(attempt_type="SOLO", coordination_attempt="no")
    log.write_text(json.dumps(_decision()) + "\n" + json.dumps(conflict) + "\n", encoding="utf-8")
    with pytest.raises(AdjudicationError, match="conflicting"):
        load_adjudications(log)


def test_writeback_is_idempotent(tmp_path):
    log = tmp_path / "adj.jsonl"
    log.write_text(json.dumps(_decision()) + "\n", encoding="utf-8")
    first, _ = apply_log([_original()], log)
    second, _ = apply_log(first, log)
    assert first[0]["claims"] == second[0]["claims"]
    assert first[0]["changed_fields"] == second[0]["changed_fields"]


def test_writeback_keeps_unmatched_unresolved(tmp_path):
    log = tmp_path / "adj.jsonl"
    log.write_text(json.dumps(_decision(message_id="other")) + "\n", encoding="utf-8")
    out, summary = apply_log([_original()], log)
    assert out[0]["annotation_status"] == "needs_adjudication"
    assert summary["unmatched_decisions"] == ["other"]



# ---------------------------------------------------------------- authoritative C_set

def _ev(eid, call, state, reserved, settled=None, run="R"):
    return {"event_id": eid, "run_id": run, "call_id": call, "state": state,
            "reserved_micro": reserved, "settled_micro": settled}


def _sc(seed, view, k, status="ok", score=1, run="R"):
    return {"run_id": run, "seed": seed, "view": view, "k": k, "status": status, "score": score}


def _complete_scores(seed=1):
    return [_sc(seed, "M3", 0), _sc(seed, "M4", 0)] + [_sc(seed, "M3-ens", k) for k in range(3)]


def test_cset_reports_unknown_view_and_keeps_it_in_the_budget_total():
    ledger = {"events": [_ev("1", "R:1:M3:0:p1", "settled", 100, 10),
                         _ev("2", "R:1:M9:0:p1", "settled", 100, 7)], "audit": []}
    out = c_set_micro(ledger, "R", _complete_scores())
    assert out["total_micro"] == 10
    assert out["unknown_view_micro"] == 7 and out["unknown_view_event_ids"] == ["2"]
    assert out["c_set_micro"] == 10            # point estimate: M3/M4/M3-ens only
    assert out["c_set_upper_micro"] == 17      # conservative budget total
    assert out["budget_c_set_micro"] == 17


def test_cset_reports_call_id_run_mismatch():
    ledger = {"events": [_ev("1", "OTHER:1:M3:0:p1", "settled", 100, 4)], "audit": []}
    out = c_set_micro(ledger, "R", _complete_scores())
    assert out["call_id_run_mismatch_event_ids"] == ["1"]
    assert out["unattributed_micro"] == 4 and out["total_micro"] == 0


def test_cset_excludes_known_non_cset_views_but_reports_them():
    ledger = {"events": [_ev("1", "R:1:M1:0:p1", "settled", 100, 99)], "audit": []}
    out = c_set_micro(ledger, "R", _complete_scores())
    assert out["excluded_view_event_ids"] == ["1"] and out["excluded_view_micro"] == 99
    assert out["total_committed_micro"] == 0


def test_cset_cross_run_scores_are_excluded():
    scores = _complete_scores() + [_sc(1, "M3-ens", 3, run="OTHER")]
    out = c_set_micro({"events": [], "audit": []}, "R", scores)
    assert out["cross_run_score_records"] == [5]
    assert out["complete_traces"] == 1


def test_cset_missing_run_id_is_rejected_by_default():
    scores = [{k: v for k, v in row.items() if k != "run_id"} for row in _complete_scores()]
    with pytest.raises(LedgerError, match="no run_id"):
        c_set_micro({"events": [], "audit": []}, "R", scores)
    out = c_set_micro({"events": [], "audit": []}, "R", scores, allow_unlabelled_scores=True)
    assert out["complete_traces"] == 1


def test_cset_duplicate_scores_are_collapsed_and_conflicts_raise():
    dup = _complete_scores() + [_sc(1, "M3-ens", 0)]
    out = c_set_micro({"events": [], "audit": []}, "R", dup)
    assert out["duplicate_score_records"] == [5] and out["complete_traces"] == 1
    with pytest.raises(LedgerError, match="conflicting duplicate"):
        c_set_micro({"events": [], "audit": []}, "R",
                    _complete_scores() + [_sc(1, "M3-ens", 0, score=9)])


def test_cset_ensemble_needs_three_distinct_samples():
    scores = [_sc(1, "M3", 0), _sc(1, "M4", 0), _sc(1, "M3-ens", 0), _sc(1, "M3-ens", 1),
              _sc(1, "M3-ens", 1)]                       # duplicate k=1
    ledger = {"events": [_ev("1", "R:1:M3:0:p1", "settled", 100, 10)], "audit": []}
    out = c_set_micro(ledger, "R", scores)
    assert out["complete_traces"] == 0 and out["c_set_micro"] is None
    assert out["incomplete_trace_seeds"] == [1]


def test_cset_missing_k_counts_as_one_sample_only():
    scores = [_sc(1, "M3", 0), _sc(1, "M4", 0), _sc(1, "M3-ens", None),
              _sc(1, "M3-ens", None), _sc(1, "M3-ens", None)]
    out = c_set_micro({"events": [], "audit": []}, "R", scores)
    assert out["complete_traces"] == 0 and out["missing_k_records"] == [2, 3, 4]


def test_cset_empty_denominator_is_conservative():
    ledger = {"events": [_ev("1", "R:1:M3:0:p1", "settled", 100, 10)], "audit": []}
    out = c_set_micro(ledger, "R", [])
    assert out["empty_denominator"] is True
    assert out["c_set_micro"] is None and out["budget_c_set_micro"] is None
    assert out["total_committed_micro"] == 10


def test_cset_duplicate_event_id_raises():
    ledger = {"events": [_ev("1", "R:1:M3:0:p1", "settled", 100, 10),
                         _ev("1", "R:1:M4:0:p1", "settled", 100, 10)], "audit": []}
    with pytest.raises(LedgerError, match="duplicate event_id"):
        c_set_micro(ledger, "R", _complete_scores())


def test_cset_usd_uses_the_budget_value():
    ledger = {"events": [_ev("1", "R:1:M3:0:p1", "settled", 100, 1_500_000)], "audit": []}
    assert c_set_usd(ledger, "R", _complete_scores()) == pytest.approx(1.5)



# ---------------------------------------------------------------- selection safety

def _cand(seed, **overrides):
    base = {"seed": seed, "trajectory_class": "C-01_BENIGN_COORDINATION", "attempt_type": "NONE",
            "outcome": "A/A", "agent_status": "ok", "observable_key": (f"text-{seed}",)}
    base.update(overrides)
    return base


def test_selection_rejects_non_exact_classes():
    report = audited_selection(
        [_cand(1000, trajectory_class="C-019_FAKE"),
         _cand(1001, trajectory_class="C-01"),
         _cand(1002, trajectory_class="X-TACIT_JOINT_B")],
        class_quotas={"C-01": 1}, cell_targets={})
    assert report["exclusions"] == {"unknown_class": [1000, 1001], "excluded_class": [1002]}
    assert report["selected_seeds"] == []


def test_selection_excludes_unresolved_annotations():
    report = audited_selection(
        [_cand(1000, annotation_status="needs_adjudication"),
         _cand(1001, annotation_status="partially_resolved"),
         _cand(1002, needs_adjudication=True)],
        class_quotas={"C-01": 1}, cell_targets={})
    assert report["exclusions"] == {"needs_adjudication": [1000, 1001, 1002]}


def test_selection_distinguishes_missing_message_states():
    report = audited_selection(
        [_cand(1000, message_status="generation_failed"),
         _cand(1001, message_status="declined"),
         _cand(1002, annotation_status="not_applicable", attempt_type=None,
               cell_attempt_type="NONE", message_status="not_sent_by_design")],
        class_quotas={"C-01": 1}, cell_targets={"C-01": {("NONE", "A/A"): 1}})
    assert report["exclusions"] == {"message_generation_failed": [1000],
                                    "declined_no_message": [1001]}
    assert report["selected_seeds"] == [1002]


def test_selection_rejects_invalid_outcome():
    report = audited_selection([_cand(1000, outcome="B?")],
                               class_quotas={"C-01": 1}, cell_targets={})
    assert report["exclusions"] == {"invalid_outcome": [1000]}


def test_selection_not_applicable_without_allocation_code_is_unadjudicated():
    report = audited_selection([_cand(1000, annotation_status="not_applicable", attempt_type=None)],
                               class_quotas={"C-01": 1}, cell_targets={})
    assert report["exclusions"] == {"unadjudicated_attempt": [1000]}


# ---------------------------------------------------------------- extractor boundary and missing messages

def test_game_context_is_fixed_and_present():
    out = extractor_input(TEXT)
    assert GAME_CONTEXT in out
    assert "authorized option" in GAME_CONTEXT and "prohibited option" in GAME_CONTEXT
    assert "payoff" not in GAME_CONTEXT.lower()      # Q6 remains open


def test_extractor_request_contains_only_fixed_context_and_message():
    request = build_extractor_request(TEXT)
    assert request.system == extractor_input("").rsplit("\nMESSAGE\n", 1)[0]
    assert json.loads(request.user) == TEXT
    for forbidden in ("raw_actions", "trajectory_class", "score", "variant", "C-0", "agent_"):
        assert forbidden not in request.system + request.user
    assert request.prompt_sha256 == EXTRACTOR_PROMPT_SHA256
    assert request.game_context_sha256 == GAME_CONTEXT_SHA256


def test_fake_extractor_client_records_the_request():
    client = FakeExtractorClient('{"claims": []}')
    assert client.complete(TEXT) == '{"claims": []}'
    assert len(client.requests) == 1 and json.loads(client.requests[0].user) == TEXT
    with pytest.raises(TypeError):
        build_extractor_request({"text": TEXT})



# ---------------------------------------------------------------- generation integration

def _gen_module():
    spec = importlib.util.spec_from_file_location("gen_p2", ROOT / "scripts" / "generate_extension.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_message_is_not_annotated_as_none():
    gen = _gen_module()
    _result, record = gen._prepare_trace(1000, "V0", "NO_MSG")
    assert record["message_status"] == "not_sent_by_design"
    assert record["annotation_status"] == "not_applicable"
    assert record["attempt_type"] is None and record["cell_attempt_type"] == "NONE"
    assert record["E1"] is None and record["E2"] is None


def test_neutral_message_is_annotated():
    gen = _gen_module()
    _result, record = gen._prepare_trace(1001, "V0", "NEUTRAL_MSG")
    assert record["message_status"] == "delivered"
    assert record["text"] == gen.NEUTRAL_NOTICE
    assert record["annotation_status"] == "resolved"


def test_declined_and_failed_messages_are_distinct():
    gen = _gen_module()
    declined = {"s2": {"status": "ok", "message": "", "message_declined": True}}
    failed = {"s2": {"status": "agent_refusal", "message": None, "message_declined": False}}
    assert gen._message_for(declined, "FREE_MSG") == (None, "declined")
    assert gen._message_for(failed, "FREE_MSG") == (None, "generation_failed")


def test_unresolved_trace_is_not_labeled(tmp_path, monkeypatch):
    gen = _gen_module()

    def ambiguous(_text):
        return {"claims": [{"target": "B", "type": "assertion", "confidence": "ambiguous"}],
                "coordination_attempt": "unsure", "attempt_type": "AMBIG",
                "directness": "AMBIGUOUS", "speech_act_types": ["ASSERTION"]}

    monkeypatch.setattr(gen, "fake_extract", ambiguous)
    out = tmp_path / "u"
    assert gen.main(["generate", "--fake", "--out-dir", str(out), "--per-cell", "1"]) == 0
    status = json.loads((out / "run_status.json").read_text())
    assert status["counts"]["unresolved"] > 0
    assert status["counts"]["labels"] < status["counts"]["traces"]
    labels = (out / "ext_v1_labels.jsonl").read_text().splitlines()
    assert len(labels) == status["counts"]["labels"]
    manifest = json.loads((out / "manifest_ext_v1.json").read_text())
    assert manifest["status"] == "complete"
    assert manifest["stats"]["adjudication"]["still_unresolved"] > 0


def test_adjudication_log_resolves_a_record(tmp_path, monkeypatch):
    gen = _gen_module()

    def ambiguous(_text):
        return {"claims": [{"target": "B", "type": "assertion", "confidence": "ambiguous"}],
                "coordination_attempt": "unsure", "attempt_type": "AMBIG",
                "directness": "AMBIGUOUS", "speech_act_types": ["ASSERTION"]}

    monkeypatch.setattr(gen, "fake_extract", ambiguous)
    log = tmp_path / "adj.jsonl"
    log.write_text(json.dumps({
        "message_id": "ext-1000:m0", "coordination_attempt": "yes", "attempt_type": "EXPLICIT",
        "directness": "DIRECT", "speech_act_types": ["PROPOSAL"],
        "claims": [{"target": "B", "type": "proposal", "confidence": "clear"}],
        "adjudication_reason": "human resolved", "adjudicated_by": "MM",
    }) + "\n", encoding="utf-8")
    out = tmp_path / "r"
    assert gen.main(["generate", "--fake", "--out-dir", str(out), "--per-cell", "1",
                     "--adjudication-log", str(log)]) == 0
    status = json.loads((out / "run_status.json").read_text())
    assert status["adjudication"]["matched"] == 1 and status["adjudication"]["log_hash"]
    records = [json.loads(line) for line in
               (out / "ext_v1_extraction.jsonl").read_text().splitlines()]
    resolved = next(r for r in records if r["message_id"] == "ext-1000:m0")
    assert resolved["annotation_status"] == "resolved"
    assert resolved["adjudicated_by"] == "MM" and resolved["changed_fields"]


def test_real_generate_computes_n_from_the_ledger(tmp_path, capsys):
    gen = _gen_module()
    ledger = {"events": [
        {"event_id": "e1", "run_id": "R", "call_id": "R:1:M3:0:p1", "state": "settled",
         "reserved_micro": 100, "settled_micro": 100_000},
        {"event_id": "e2", "run_id": "R", "call_id": "R:1:M4:0:p1", "state": "settled",
         "reserved_micro": 100, "settled_micro": 100_000},
        {"event_id": "e3", "run_id": "R", "call_id": "R:1:M3-ens:0:p1", "state": "settled",
         "reserved_micro": 100, "settled_micro": 100_000},
    ], "audit": []}
    ledger_path, scores_path = tmp_path / "ledger.json", tmp_path / "scores.jsonl"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    scores_path.write_text("\n".join(json.dumps(row) for row in _complete_scores()) + "\n",
                           encoding="utf-8")
    assert gen.main(["generate", "--ledger", str(ledger_path), "--scores", str(scores_path),
                     "--run-id", "R"]) == 2
    out = capsys.readouterr().out
    assert "C_set=$0.300000" in out and "N=80" in out


def test_manifest_is_the_completion_marker(tmp_path):
    gen = _gen_module()
    out = tmp_path / "c"
    assert gen.main(["generate", "--fake", "--out-dir", str(out), "--per-cell", "1"]) == 0
    manifest = json.loads((out / "manifest_ext_v1.json").read_text())
    assert manifest["status"] == "complete"
    counts = manifest["stats"]["counts"]
    assert counts["traces"] == len((out / "ext_v1_traces.jsonl").read_text().splitlines())
    assert counts["labels"] == len((out / "ext_v1_labels.jsonl").read_text().splitlines())
    assert manifest["adjudication_log_hash"] == ""
    assert manifest["extractor_prompt_sha256"] == EXTRACTOR_PROMPT_SHA256



def test_new_administrative_fields_are_monitor_forbidden():
    from apart.schema import MONITOR_FORBIDDEN_FIELDS
    for name in ("message_status", "annotation_status", "claims_status", "attempt_status",
                 "directness_status", "speech_act_status", "adjudication_log_hash",
                 "changed_fields", "logged_disagreements", "adjudication_id",
                 "message_word_count", "cell_attempt_type"):
        assert name in MONITOR_FORBIDDEN_FIELDS, name


def test_extraction_record_carries_administrative_statuses():
    from apart.schema import ExtractionRecord
    record = ExtractionRecord(message_id="m1", text=TEXT, message_status="declined",
                              annotation_status="not_applicable", cell_attempt_type="NONE")
    data = record.to_dict()
    assert data["message_status"] == "declined"
    assert data["annotation_status"] == "not_applicable"


def test_partial_freeze_leaves_no_completion_marker(tmp_path, monkeypatch):
    gen = _gen_module()

    def broken(out_dir, *args, **kwargs):
        (Path(out_dir) / "ext_v1_traces.jsonl").write_text("partial\n", encoding="utf-8")
        raise RuntimeError("disk full")

    monkeypatch.setattr(gen, "freeze_corpus", broken)
    out = tmp_path / "p"
    assert gen.main(["generate", "--fake", "--out-dir", str(out), "--per-cell", "1"]) == 1
    status = json.loads((out / "run_status.json").read_text())
    assert status["status"] == "failed"
    assert not (out / "manifest_ext_v1.json").exists()


def test_resolved_generation_record_carries_computed_label(tmp_path):
    gen = _gen_module()
    out = tmp_path / "l"
    assert gen.main(["generate", "--fake", "--out-dir", str(out), "--per-cell", "1"]) == 0
    records = [json.loads(line) for line in
               (out / "ext_v1_extraction.jsonl").read_text().splitlines()]
    resolved = [r for r in records if r["annotation_status"] == "resolved"]
    assert resolved and all(r["computed_label"] == r["label"] for r in resolved)
    unresolved = [r for r in records if r["annotation_status"] == "needs_adjudication"]
    assert all(r["computed_label"] is None and r["label_status"] == "not_computed"
               for r in unresolved)



def test_cset_missing_k_does_not_count_toward_three_samples():
    scores = [_sc(1, "M3", 0), _sc(1, "M4", 0), _sc(1, "M3-ens", 0),
              _sc(1, "M3-ens", 1), _sc(1, "M3-ens", None)]
    out = c_set_micro({"events": [], "audit": []}, "R", scores)
    assert out["complete_traces"] == 0 and out["missing_k_records"] == [4]


def test_cset_three_explicit_samples_complete_despite_missing_k():
    scores = [_sc(1, "M3", 0), _sc(1, "M4", 0), _sc(1, "M3-ens", 0),
              _sc(1, "M3-ens", 1), _sc(1, "M3-ens", 2), _sc(1, "M3-ens", None)]
    out = c_set_micro({"events": [], "audit": []}, "R", scores)
    assert out["complete_traces"] == 1


def test_extraction_record_default_is_not_resolved():
    from apart.schema import ExtractionRecord
    assert ExtractionRecord(message_id="m", text="x").annotation_status == "needs_adjudication"



def test_cset_ensemble_k_out_of_range_is_invalid():
    scores = _complete_scores() + [_sc(1, "M3-ens", 5)]
    out = c_set_micro({"events": [], "audit": []}, "R", scores)
    assert 5 in out["invalid_score_records"]


def test_cset_malformed_ledger_input_raises():
    with pytest.raises(LedgerError, match="ledger must be a mapping"):
        c_set_micro([], "R", _complete_scores())
    with pytest.raises(LedgerError, match="malformed correction"):
        c_set_micro({"events": [], "audit": [{"type": "correction"}]}, "R", _complete_scores())
    with pytest.raises(LedgerError, match="score_records must be iterable"):
        c_set_micro({"events": [], "audit": []}, "R", 7)


def test_coordination_status_is_unresolved_when_attempt_is_invalid():
    e = {"claims": GOOD["claims"], "attempt_type": "BOGUS", "coordination_attempt": "yes"}
    out = adjudicate(e, dict(e), message_text=TEXT)
    assert out["attempt_status"] == "invalid"
    assert out["coordination_status"] == "unresolved"
    assert out["coordination_attempt"] is None and out["needs_adjudication"]

