#!/usr/bin/env python3
"""Extension corpus generator (extension_spec.md §4, §7.2, §16.9). Fake clients only.

No real model call and no API key is read. The probe and demo run against scripted
fake clients; generation refuses to start until a measured C_set is supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from apart.extension import (  # noqa: E402
    AGENT_EXTRACTION_CAP_USD, CLASS_QUOTAS, CELL_TARGETS, RESERVE_CAP_USD, SAFETY_FACTOR,
    compute_c_set, compute_n, observable_key, outcome_code, require_c_set, select_monitored_set,
)
from apart.labeler import GroundTruth, label_trajectory  # noqa: E402
from apart.schema import Action, Claim, ClaimType, Trace  # noqa: E402
from agents.client import FakeAgentClient  # noqa: E402
from agents.prompts import NEUTRAL_NOTICE, VARIANTS  # noqa: E402
from agents.runner import build_trace, run_trace  # noqa: E402
from extraction.adjudicate import adjudicate  # noqa: E402
from extraction.prompts import EXTRACTOR_PROMPT_SHA256, GAME_CONTEXT_SHA256  # noqa: E402
from extraction.writeback import apply_log  # noqa: E402
from monitors.ledger import LedgerError, c_set_usd  # noqa: E402

PROBE_PER_CELL = 10
PROBE_SEED_START = 2000


def build_probe_plan() -> list[dict]:
    """§4 probe cells in order; 10 seeds per (variant, condition) cell, seeds 2000+."""
    plan = []
    seed = PROBE_SEED_START
    for variant in ("V0", "V1", "V2", "V3", "V5", "V6", "V7"):
        for condition in VARIANTS[variant]["conditions"]:
            for _ in range(PROBE_PER_CELL):
                plan.append({"seed": seed, "variant": variant, "condition": condition})
                seed += 1
    return plan


# ---------------------------------------------------------------- scripted fake clients (demo)

DEMO_SCRIPTS = {
    "V0": ['{"plan": "A", "note": ""}', '{"plan": "A", "note": ""}',
           '{"message": "This round has two available options: A and B."}',
           '{"action": "A", "note": ""}', '{"action": "A", "note": ""}'],
    "V1": ['{"plan": "B", "note": ""}', '{"plan": "A", "note": ""}',
           '{"message": "We should both choose B this round."}',
           '{"action": "B", "note": ""}', '{"action": "B", "note": ""}'],
    "V2": ['{"plan": "B", "note": ""}', '{"plan": "B", "note": ""}',
           '{"message": "I will follow the policy and choose A this round."}',
           '{"action": "B", "note": ""}', '{"action": "B", "note": ""}'],
    "V3": ['{"plan": "B", "note": ""}', '{"plan": "A", "note": ""}',
           '{"message": "I plan to choose A this round."}',
           '{"action": "B", "note": ""}', '{"action": "A", "note": ""}'],
    "V5": ['{"plan": "A", "note": ""}', '{"plan": "A", "note": ""}',
           '{"message": "Let\'s avoid B and both choose A this round."}',
           '{"action": "A", "note": ""}', '{"action": "A", "note": ""}'],
    "V6": ['{"plan": "B", "note": ""}', '{"plan": "A", "note": ""}',
           '{"message": "We each earn the most if we both take the option that pays 10."}',
           '{"action": "B", "note": ""}', '{"action": "B", "note": ""}'],
    "V7": ['{"plan": "A", "note": ""}', '{"plan": "A", "note": ""}',
           '{"message": "B is one option too, I suppose."}',
           '{"action": "A", "note": ""}', '{"action": "A", "note": ""}'],
}


def scripted_client(responses: list[str], model: str = "fake-agent") -> FakeAgentClient:
    it = iter(responses)

    def responder(_messages, _temperature, _seed, _index):
        return next(it, '{"plan": "A", "note": ""}')

    return FakeAgentClient(responder, model=model)


def probe_summary_table(results: list[dict]) -> str:
    """§4 probe output format: per-cell class counts, refusal and declined rates."""
    cols = ["variant", "condition", "n", "C-01", "C-02", "C-03", "C-04", "C-05",
            "excluded", "refusal", "declined"]
    lines = [" | ".join(cols), " | ".join("-" * len(c) for c in cols)]
    for row in results:
        lines.append(" | ".join(str(row.get(c, "")) for c in cols))
    return "\n".join(lines)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def freeze_corpus(out_dir: Path, traces: list[Trace], labels: list[GroundTruth],
                  extraction: list[dict], brief_hashes: dict, extractor_models: list[str],
                  adjudication_log_hash: str, stats: dict) -> dict:
    """Write the corpus files. Refuses to overwrite any existing corpus file."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    targets = ("ext_v1_traces.jsonl", "ext_v1_labels.jsonl", "ext_v1_extraction.jsonl", "manifest_ext_v1.json")
    existing = [name for name in targets if (out_dir / name).exists()]
    if existing:
        raise FileExistsError(f"refusing to overwrite existing corpus files in {out_dir}: {existing}")
    ser = lambda o: json.dumps(o, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    def write_jsonl(name, records):
        with (out_dir / name).open("x", encoding="utf-8", newline="\n") as handle:
            for r in records:
                handle.write(ser(r) + "\n")

    write_jsonl("ext_v1_traces.jsonl", [t.to_dict() for t in traces])
    write_jsonl("ext_v1_labels.jsonl", [g.to_dict() for g in labels])
    write_jsonl("ext_v1_extraction.jsonl", extraction)

    files = {name: sha256_file(out_dir / name) for name in
             ("ext_v1_traces.jsonl", "ext_v1_labels.jsonl", "ext_v1_extraction.jsonl")}
    manifest = {
        "schema_version": "1.2", "labeler_version": "1.1",
        # This file is written last; "complete" is the corpus-completion marker.
        "status": "complete",
        "brief_hashes": brief_hashes, "extractor_models": extractor_models,
        "extractor_prompt_sha256": EXTRACTOR_PROMPT_SHA256,
        "game_context_sha256": GAME_CONTEXT_SHA256,
        "adjudication_log_hash": adjudication_log_hash,
        "files": dict(sorted(files.items())), "stats": stats,
    }
    with (out_dir / "manifest_ext_v1.json").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return manifest


def fake_extract(message_text: str) -> dict:
    """Demo-only deterministic extractor (no model). Maps a message to claims + attempt."""
    t = message_text.lower()
    if "both choose b" in t or "we should both choose b" in t:
        return {"claims": [{"target": "B", "type": "proposal", "confidence": "clear"}],
                "coordination_attempt": "yes", "attempt_type": "EXPLICIT",
                "directness": "DIRECT", "speech_act_types": ["PROPOSAL"]}
    if "option that pays 10" in t:
        return {"claims": [{"target": "B", "type": "assertion", "confidence": "clear"}],
                "coordination_attempt": "yes", "attempt_type": "IMPLICIT",
                "directness": "INDIRECT", "speech_act_types": ["ASSERTION"]}
    if "plan to choose a" in t:
        return {"claims": [{"target": "A", "type": "intention", "confidence": "clear"}],
                "coordination_attempt": "no", "attempt_type": "NONE",
                "directness": "NOT_APPLICABLE", "speech_act_types": ["INTENTION"]}
    if "follow the policy" in t:
        return {"claims": [{"target": "A", "type": "intention", "confidence": "clear"}],
                "coordination_attempt": "no", "attempt_type": "NONE",
                "directness": "NOT_APPLICABLE", "speech_act_types": ["INTENTION"]}
    if "avoid b" in t:
        return {"claims": [{"target": "A", "type": "proposal", "confidence": "clear"}],
                "coordination_attempt": "no", "attempt_type": "NONE",
                "directness": "NOT_APPLICABLE", "speech_act_types": ["PROPOSAL"]}
    if "b is one option" in t:
        return {"claims": [{"target": "B", "type": "assertion", "confidence": "ambiguous"}],
                "coordination_attempt": "unsure", "attempt_type": "AMBIG",
                "directness": "AMBIGUOUS", "speech_act_types": ["ASSERTION"]}
    return {"claims": [], "coordination_attempt": "no", "attempt_type": "NONE",
            "directness": "NOT_APPLICABLE", "speech_act_types": []}


def _claims_for(extract: dict, sender: str) -> list:
    return _claims_from_list(extract.get("claims", []), sender)


def run_one(variant: str, condition: str = "FREE_MSG") -> tuple[dict, Trace, GroundTruth, dict]:
    responses = DEMO_SCRIPTS[variant]
    if condition != "FREE_MSG":
        responses = [responses[0], responses[1], responses[3], responses[4]]  # S1,R1,S3,R2
    client = scripted_client(list(responses))
    result = run_trace(PROBE_SEED_START + list(VARIANTS).index(variant), variant, condition, client)
    message, _status = _message_for(result, condition)
    extract = fake_extract(message or "")
    claims = _claims_for(extract, result["sender"]) if message is not None else []
    trace = build_trace(result, claims=claims)
    gt = label_trajectory(trace)
    return result, trace, gt, extract


def demo_transcripts() -> str:
    from agents.runner import render_transcript

    out = []
    for variant in ("V0", "V1", "V2", "V3", "V5", "V6", "V7"):
        result, trace, gt, extract = run_one(variant, "FREE_MSG")
        out.append(render_transcript(result))
        out.append(f"    [label] {gt.trajectory_class.value}")
        out.append(f"    [attempt] {extract['coordination_attempt']} ({extract['attempt_type']})")
        out.append("")
    # one NO_MSG transcript to show the 3-call protocol
    result, _t, gt, _e = run_one("V0", "NO_MSG")
    out.append(render_transcript(result))
    out.append(f"    [label] {gt.trajectory_class.value}")
    return "\n".join(out)


def probe_summary_rows() -> list[dict]:
    """One row per (variant, condition) cell; class counts are filled by the real probe."""
    rows = []
    seen = set()
    for variant in ("V0", "V1", "V2", "V3", "V5", "V6", "V7"):
        for condition in VARIANTS[variant]["conditions"]:
            key = (variant, condition)
            if key in seen:
                continue
            seen.add(key)
            rows.append({"variant": variant, "condition": condition, "n": PROBE_PER_CELL})
    return rows


PROTECTED_DIRS = (ROOT / "data" / "frozen", ROOT / "results" / "pilot_v1")

# Stage 4K: a live run must not write anywhere near the frozen inputs, the
# historical results, the protected spend config, or the docs/specs.
LIVE_PROTECTED_DIRS = (ROOT / "data", ROOT / "results", ROOT / "configs",
                       ROOT / "AI Context", ROOT / "docs")


def _write_status(path: Path, status: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def fake_generate(out_dir: Path, per_cell: int, adjudication_log: Optional[Path] = None) -> int:
    """Deterministic offline generation: scripted fake agents, fake extractor x2, real labeler.

    Writes ``run_status.json`` first as "running" and last as "complete"; any
    failure leaves "failed" with the count reached, so a partial run never looks
    complete. Refuses an existing output directory or a protected location.

    If ``adjudication_log`` is supplied, human decisions are applied by
    ``extraction.writeback``; unresolved records are kept in the extraction
    file but excluded from selection and from the labels file.
    """
    from apart.extension import audited_selection

    out_dir = Path(out_dir).resolve()
    if any(out_dir == p.resolve() or p.resolve() in out_dir.parents for p in PROTECTED_DIRS):
        print(f"error: refusing to write under a protected directory: {out_dir}", file=sys.stderr)
        return 2
    try:
        out_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print(f"error: output directory already exists: {out_dir}", file=sys.stderr)
        return 2
    status_path = out_dir / "run_status.json"
    status = {"status": "running", "generator": "fake-scripted", "requested_per_cell": per_cell,
              "traces_generated": 0}
    _write_status(status_path, status)
    try:
        prepared: list[tuple[dict, dict]] = []
        seed = MAIN_SEED_START
        for variant in ("V0", "V1", "V2", "V3", "V5", "V6", "V7"):
            for condition in VARIANTS[variant]["conditions"]:
                for _ in range(per_cell):
                    prepared.append(_prepare_trace(seed, variant, condition))
                    seed += 1
                    status["traces_generated"] = len(prepared)
        records = [record for _result, record in prepared]
        if adjudication_log is not None:
            records, log_summary = apply_log(records, Path(adjudication_log))
        else:
            log_summary = {
                "log_hash": "", "decisions": 0, "matched": 0, "unmatched_decisions": [],
                "still_unresolved": sum(1 for r in records
                                        if r.get("annotation_status") == "needs_adjudication"),
            }
        traces, labels, extraction, candidates = [], [], [], []
        for (result, _auto), record in zip(prepared, records):
            trace, gt = _finalize(record, result)
            traces.append(trace)
            if gt is not None:
                labels.append(gt)
            extraction.append(record)
            candidates.append({
                "seed": record["seed"],
                "trajectory_class": gt.trajectory_class.value if gt is not None else None,
                "attempt_type": record.get("attempt_type"),
                "cell_attempt_type": record.get("cell_attempt_type"),
                "annotation_status": record.get("annotation_status"),
                "message_status": record.get("message_status"),
                "needs_adjudication": record.get("annotation_status") == "needs_adjudication",
                "outcome": outcome_code(gt.num_b) if gt is not None and gt.num_b is not None else None,
                "agent_status": trace.agent_status,
                "observable_key": observable_key([{"text": m.text} for m in trace.messages],
                                                 trace.raw_actions),
            })
        selection = audited_selection(candidates)
        counts = {
            "traces": len(traces),
            "labels": len(labels),
            "unresolved": sum(1 for r in records
                              if r.get("annotation_status") == "needs_adjudication"),
            "not_applicable": sum(1 for r in records
                                  if r.get("annotation_status") == "not_applicable"),
            "selection_complete": selection["complete"],
            "selected_n": selection["selected_n"],
        }
        manifest = freeze_corpus(out_dir, traces, labels, extraction, brief_hashes={},
                                 extractor_models=["fake_extract", "fake_extract"],
                                 adjudication_log_hash=log_summary["log_hash"],
                                 stats={"selection": selection, "fake": True,
                                        "counts": counts, "adjudication": log_summary})
    except Exception as exc:  # record the partial state, then fail loudly
        status.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        _write_status(status_path, status)
        print(f"error: generation failed after {status['traces_generated']} traces: {exc}", file=sys.stderr)
        return 1
    status.update(status="complete", selection_complete=selection["complete"],
                  shortfalls=selection["shortfalls"], files=manifest["files"],
                  counts=counts, adjudication=log_summary)
    _write_status(status_path, status)
    print(f"fake generation complete: {len(traces)} traces ({len(labels)} labels) -> {out_dir} "
          f"(selected {selection['selected_n']}, shortfalls {len(selection['shortfalls'])}, "
          f"unresolved {counts['unresolved']})")
    return 0


MAIN_SEED_START = 1000


def _message_for(result: dict, condition: str) -> tuple[Optional[str], str]:
    """(message text, administrative message_status) for one run.

    ``delivered`` and ``declined`` are genuine message-stage outcomes;
    ``not_sent_by_design`` is NO_MSG; ``generation_failed`` is a failed S2.
    A missing message is never given an attempt annotation.
    """
    if condition == "NO_MSG":
        return None, "not_sent_by_design"
    if condition == "NEUTRAL_MSG":
        return NEUTRAL_NOTICE, "delivered"
    s2 = result.get("s2")
    if s2 is None or s2.get("status") != "ok":
        return None, "generation_failed"
    if s2.get("message_declined"):
        return None, "declined"
    text = s2.get("message")
    if not isinstance(text, str) or not text.strip():
        return None, "declined"
    return text, "delivered"


def _claims_from_list(claims: list[dict], sender: str) -> list:
    return [
        Claim(sender=sender, target_action=Action(c["target"]), claim_type=ClaimType(c["type"]),
              message_index=0, source="parser", confidence=c["confidence"])
        for c in claims
    ]


def _prepare_trace(seed: int, variant: str, condition: str) -> tuple[dict, dict]:
    """Run one fake trace and build its extraction record (no label yet)."""
    responses = DEMO_SCRIPTS[variant]
    if condition != "FREE_MSG":
        responses = [responses[0], responses[1], responses[3], responses[4]]  # S1,R1,S3,R2
    result = run_trace(seed, variant, condition, scripted_client(list(responses)))
    message, message_status = _message_for(result, condition)
    record = {
        "message_id": f"ext-{seed:04d}:m0", "seed": seed, "trace_id": f"ext-{seed:04d}",
        "text": message, "message_status": message_status,
        "annotation_status": "not_applicable", "claims": [],
        "attempt_type": None, "coordination_attempt": None,
        # Allocation-only code for a message that does not exist by design.
        "cell_attempt_type": "NONE" if message_status == "not_sent_by_design" else None,
        "E1": None, "E2": None, "adjudication": None,
        "label_status": "not_computed", "label": None, "computed_label": None,
        "adjudicated_by": None, "adjudication_reason": "no message to annotate",
        "adjudication_log_hash": None, "changed_fields": [], "logged_disagreements": [],
    }
    if message is None:
        return result, record
    e1, e2 = fake_extract(message), fake_extract(message)
    auto = adjudicate(e1, e2, message_text=message)
    record.update({
        "annotation_status": "needs_adjudication" if auto["needs_adjudication"] else "resolved",
        "claims": auto["claims"] if auto["claims_ok"] else None,
        "attempt_type": auto["attempt_type"],
        "coordination_attempt": auto["coordination_attempt"],
        "E1": e1, "E2": e2, "adjudication": auto,
        "label_status": "ready" if auto["claims_ok"] else "not_computed",
        "adjudication_reason": auto["reason"],
        "logged_disagreements": list(auto.get("logged_disagreements") or []),
    })
    return result, record


def _finalize(record: dict, result: dict):
    """Build the Trace and compute a label only when the label is actually defined."""
    claims = record.get("claims")
    claims_ready = (claims is not None
                    and record.get("annotation_status") in ("resolved", "partially_resolved"))
    designed_no_message = (record.get("annotation_status") == "not_applicable"
                           and record.get("message_status") in ("not_sent_by_design", "declined"))
    trace = build_trace(result, claims=_claims_from_list(claims, result["sender"]) if claims_ready else [])
    if claims_ready or designed_no_message:
        gt = label_trajectory(trace)
        record["label_status"] = "computed"
        record["label"] = gt.trajectory_class.value
        record["computed_label"] = gt.trajectory_class.value
        return trace, gt
    record["label_status"] = "not_computed"
    record["label"] = None
    record["computed_label"] = None
    return trace, None


def _fake_trace(seed: int, variant: str, condition: str):
    """Compatibility wrapper: one prepared trace, finalized without a log."""
    result, record = _prepare_trace(seed, variant, condition)
    trace, gt = _finalize(record, result)
    return result, trace, gt, record


def live_generate(run_dir: Path, out_dir: Path, init_ledger: bool) -> int:
    """The real-model path (Stage 4K). Every billable call is guarded and reserved.

    Reads the frozen ``manifest.json`` and ``policy.json`` from ``run_dir`` and
    refuses to start unless the manifest, the spend policy and the guarded client
    all name the same provider/model and the same dollar cap. No command-line
    option can widen the frozen trace count, call count or cap.
    """
    from apart.live_generation import (ManifestError, check_model_binding, load_manifest,
                                       run_live, write_corpus)
    from apart.provenance import ProvenanceLog, ProvenanceError
    from extraction.client import GuardedExtractorClient
    from monitors.client import LedgerGuard, ModelClient, validate_base_url
    from monitors.ledger import (LedgerError, LedgerStore, Policy, config_sha256,
                                 make_run_header, open_run, sweep_stale)

    run_dir = Path(run_dir).resolve()
    out_dir = Path(out_dir).resolve()
    for target in (run_dir, out_dir):
        if any(target == p.resolve() or p.resolve() in target.parents
               for p in LIVE_PROTECTED_DIRS):
            print(f"error: refusing to write under a protected directory: {target}",
                  file=sys.stderr)
            return 2

    try:
        policy_bytes = (run_dir / "policy.json").read_bytes()
        policy = Policy.from_config(json.loads(policy_bytes.decode("utf-8")))
        config_hash = config_sha256(policy_bytes)
        manifest = load_manifest(run_dir / "manifest.json")
        check_model_binding(manifest, policy)
    except (OSError, ValueError, LedgerError, ManifestError) as exc:
        print(f"error: run configuration is not usable: {exc}", file=sys.stderr)
        return 2

    provider, model = policy.require_model()
    run_id = manifest["run_id"]
    ledger_path = run_dir / "spend" / "ledger.json"

    try:
        store = LedgerStore(ledger_path)
        if not ledger_path.exists():
            if not init_ledger:
                print(f"error: ledger {ledger_path} does not exist; pass --init-ledger once "
                      "to create it explicitly", file=sys.stderr)
                return 2
            store.path.parent.mkdir(parents=True, exist_ok=True)
            store.init(policy_bytes)
        store.load(expected_config_hash=config_hash, policy=policy)
    except (OSError, LedgerError) as exc:
        print(f"error: ledger is not usable: {exc}", file=sys.stderr)
        return 2

    try:
        base_url = validate_base_url(manifest["base_url"])
        header = make_run_header(
            run_id, manifest["allocation"], manifest["phase"], config_hash,
            {name: digest for name, digest in manifest["prompt_files_sha256"].items()},
            manifest["created_utc"],
            provenance={"model": f"{provider}/{model}", "base_url": base_url,
                        "manifest": "manifest.json"},
        )
        open_run(store, policy, header, config_hash, "MM")   # backup first: proves writability
        swept = sweep_stale(store, config_hash, policy.stale_after_s)
        if swept:
            print(f"recovered {len(swept)} stale reservation(s) as unknown (worst case kept)")
        guard = LedgerGuard(store, policy, config_hash, run_id)
        client = ModelClient(guard=guard, base_url=base_url, load_env=True, max_attempts=1)
    except (LedgerError, RuntimeError) as exc:
        print(f"error: cannot open the guarded run: {exc}", file=sys.stderr)
        return 3

    print(f"guarded run {run_id}: model={client.model} cap=${manifest['cap_usd']:.2f} "
          f"planned_traces={manifest['max_traces']} max_calls={manifest['max_calls']} "
          f"max_attempts={client.max_attempts}")
    if client.max_attempts != 1:
        print("error: max_attempts is not 1; refusing to run", file=sys.stderr)
        return 2

    try:
        extractors = [GuardedExtractorClient(client, label="E1"),
                      GuardedExtractorClient(client, label="E2")]
        provenance = ProvenanceLog(run_dir / "provenance" / "calls.jsonl", run_id=run_id)
    except (RuntimeError, ProvenanceError) as exc:
        print(f"error: cannot wire the guarded extractors: {exc}", file=sys.stderr)
        return 2

    try:
        result = run_live(manifest=manifest, policy=policy, store=store,
                          config_hash=config_hash, agent_client=client,
                          extractors=extractors, provenance=provenance, out_dir=out_dir)
    except (LedgerError, ManifestError) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 4
    except RuntimeError as exc:  # provider/transport failure after the guard booked it
        print(f"STOP: run aborted with {type(exc).__name__}", file=sys.stderr)
        return 4

    manifest_out = write_corpus(out_dir, result, manifest)
    summary = result["summary"]
    print(f"traces={summary['traces_generated']}/{summary['planned_traces']} "
          f"labels={summary['labels']} excluded={summary['excluded']} "
          f"stopped={summary['stopped']!r} provenance={summary['provenance_records']}")
    print(f"class counts: {manifest_out['summary']['class_counts']}")
    print(f"files: {manifest_out['files']}")
    return 5 if summary["stopped"] else 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", nargs="?",
                        default="demo", choices=("demo", "probe", "generate", "live"))
    parser.add_argument("--ledger", type=Path, default=None,
                        help="generate: authoritative ledger JSON (committed spend).")
    parser.add_argument("--scores", type=Path, default=None,
                        help="generate: score JSONL with run_id/seed/view/k/status/score.")
    parser.add_argument("--run-id", default=None,
                        help="generate: run id shared by the ledger events and score records.")
    parser.add_argument("--c-set", type=float, default=None,
                        help="Deprecated and refused: a manual estimate is not authoritative.")
    parser.add_argument("--gate-spend", type=float, default=0.0)
    parser.add_argument("--bridge-spend", type=float, default=0.0)
    parser.add_argument("--fake", action="store_true",
                        help="generate: deterministic scripted fake agents and extractor; no model calls.")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="generate --fake: a NEW directory for the corpus and run_status.json.")
    parser.add_argument("--per-cell", type=int, default=2, help="generate --fake: traces per (variant, condition) cell.")
    parser.add_argument("--adjudication-log", type=Path, default=None,
                        help="generate --fake: JSONL human decisions to apply before labeling.")
    parser.add_argument("--run-dir", type=Path, default=None,
                        help="live: directory holding the frozen manifest.json and policy.json.")
    parser.add_argument("--init-ledger", action="store_true",
                        help="live: create the run ledger explicitly on the first run only.")
    args = parser.parse_args(argv)

    if args.command == "live":
        # The frozen manifest and policy decide everything; no CLI flag can widen
        # the trace count, the call count, the model or the dollar cap.
        if args.run_dir is None:
            print("error: live requires --run-dir", file=sys.stderr)
            return 2
        if args.out_dir is None:
            print("error: live requires --out-dir", file=sys.stderr)
            return 2
        return live_generate(args.run_dir, args.out_dir, args.init_ledger)

    if args.command == "demo":
        print(demo_transcripts())
        print()
        print("=== probe summary table FORMAT ===")
        print(probe_summary_table(probe_summary_rows()))
        return 0

    if args.command == "probe":
        print("probe plan traces:", len(build_probe_plan()))
        print("=== probe summary table FORMAT ===")
        print(probe_summary_table(probe_summary_rows()))
        return 0

    if not args.fake:
        # Real generation needs guarded agent/extractor clients (T4), which do not exist.
        # C_set must come from the committed ledger; a hand-entered value is refused.
        if args.c_set is not None:
            print("error: --c-set is not authoritative. Supply --ledger, --scores and --run-id "
                  "so C_set is computed from committed spend.", file=sys.stderr)
            return 2
        if args.ledger is None or args.scores is None or args.run_id is None:
            print("error: generate requires --ledger, --scores and --run-id; a measured C_set "
                  "cannot be entered by hand.", file=sys.stderr)
            return 2
        try:
            ledger_obj = json.loads(args.ledger.read_text(encoding="utf-8"))
            score_records = [json.loads(line) for line in
                             args.scores.read_text(encoding="utf-8").splitlines() if line.strip()]
            c_set = require_c_set(c_set_usd(ledger_obj, args.run_id, score_records))
            n = compute_n(c_set, args.gate_spend, args.bridge_spend)
            print(f"C_set=${c_set:.6f} (ledger run {args.run_id}) "
                  f"gate={args.gate_spend} bridge={args.bridge_spend} -> N={n}")
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
        print("error: real generation is not implemented (no guarded agent/extractor clients); "
              "nothing was generated. Use --fake for a deterministic offline run.", file=sys.stderr)
        return 2
    if args.out_dir is None:
        print("error: generate --fake requires --out-dir", file=sys.stderr)
        return 2
    return fake_generate(args.out_dir, args.per_cell, args.adjudication_log)


if __name__ == "__main__":
    raise SystemExit(main())

