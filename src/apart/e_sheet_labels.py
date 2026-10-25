"""Finished annotation sheets -> per-trace E label rows (offline, standard library only).

Inputs, per receiver run directory (e.g. ``runs/phase-a-live-v2``):

* ``annotation_key.csv``   message_id -> (trace_id, slot_id); one message text may be
  shared by several traces;
* ``traces_*.jsonl``       every trace of the run (all four conditions);
* two completed copies of ``annotation_sheet.csv`` (one per annotator), and optionally
  an adjudication sheet in the same format.

Output: one contract-shaped E record per trace (``e_label.ERecord.to_dict`` plus
provenance fields), which ``apart.analysis_e.parse_e_label`` reads directly.

Rules (all fail closed):

* The approved **P0-1** E rule is applied: E is a function of ``coordination_attempt`` and
  ``target`` only, and speech acts are metadata that can never change E
  (``docs/decision_log.md``, "Approved: P0-1 E-label rule", post-hoc). ``UNMAPPED_CELLS``
  is therefore empty; the pre-P0-1 unmapped-cell path is retained only as dead defensive
  code so a future cell can never be silently dropped into UNRESOLVED.
* **Message provenance is verified before labelling.** Every sheet row's ``message_text``
  must equal the text of the trace slot that ``annotation_key.csv`` maps its ``message_id``
  to; a mismatch, a missing text, or one ``message_id`` denoting several texts is refused.
  Nothing is repaired and nothing is guessed.
* Annotations are translated only through ``apart.e_annotate.to_e_annotation``.
* Any invalid value or combination raises; nothing is guessed.
* Annotator disagreement is never resolved here. A message is a *disagreement* when the
  annotators' translated annotations differ in reason or target. Labels are produced only
  if there are no disagreements, or an adjudication sheet supplies every disagreed
  message (adjudication is a separate human step). Raw-field differences that translate
  to the same annotation are reported but do not block.
* Every trace in the run is labelled; a delivered slot missing from the key fails.
* The >20% unresolved-label gate (``analysis_e.unresolved_gate``) is computed on the
  produced rows and written into the manifest.
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import os
from typing import Mapping, Optional, Sequence

from .analysis_e import build_reference, unresolved_gate
from .e_annotate import (UNMAPPED_CELLS, UnmappedGuideAnnotation, to_e_annotation)
from .e_label import (EAnnotation, REASON_TO_CODE, ReasonCode, label_trace,
                      slots_from_trace)
from .schema import trace_from_dict

E_SHEET_LABELS_VERSION = "0.1"
SHEET_FIELDS = ("message_id", "coordination_attempt", "target", "speech_act_primary",
                "speech_act_secondary", "uncertainty_or_notes")
DECISION_FIELDS = ("coordination_attempt", "target", "speech_act_primary")
UNMAPPED_REASON = ReasonCode.INSUFFICIENT_EVIDENCE
UNMAPPED_NOTE_PREFIX = "UNMAPPED_ADAPTER_CELL"


class SheetError(ValueError):
    """A sheet, key or trace file cannot be turned into labels."""


class DisagreementUnresolved(SheetError):
    """Annotators disagree on messages that no adjudication sheet covers."""


def sha256_of(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def read_sheet(path: str) -> dict[str, dict]:
    """Return ``{message_id: row}``; refuses blank decision cells and duplicates."""
    with open(path, newline="", encoding="utf-8-sig") as handle:   # sheets carry a BOM
        reader = csv.DictReader(handle)
        missing = [f for f in SHEET_FIELDS if f not in (reader.fieldnames or [])]
        if missing:
            raise SheetError(f"{path}: missing column(s) {missing}")
        rows: dict[str, dict] = {}
        for row in reader:
            mid = (row.get("message_id") or "").strip()
            if not mid:
                raise SheetError(f"{path}: row without message_id")
            if mid in rows:
                raise SheetError(f"{path}: duplicate message_id {mid}")
            blank = [f for f in DECISION_FIELDS if not (row.get(f) or "").strip()]
            if blank:
                raise SheetError(f"{path}: {mid} is not finished, blank {blank}")
            rows[mid] = row
    return rows


def read_key(path: str) -> dict[str, dict[str, str]]:
    """Return ``{trace_id: {slot_id: message_id}}``."""
    out: dict[str, dict[str, str]] = {}
    with open(path, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            trace_id, slot_id, mid = row["trace_id"].strip(), row["slot_id"].strip(), row["message_id"].strip()
            slots = out.setdefault(trace_id, {})
            if slot_id in slots:
                raise SheetError(f"{path}: {trace_id}/{slot_id} listed twice")
            slots[slot_id] = mid
    return out


# ------------------------------------------------- message-to-annotation provenance


def slot_text(trace_row: Mapping, slot_id: str) -> Optional[str]:
    """The text of ``slot_id`` (``m1``, ``m2``, ...) in a raw trace record, or ``None``.

    Mirrors ``apart.e_label.slots_from_trace``: slot ``m{i}`` is the ``i``-th delivered
    message. A trace with no such slot yields ``None`` so the caller can fail closed.
    """
    if not slot_id.startswith("m") or not slot_id[1:].isdigit():
        return None
    index = int(slot_id[1:]) - 1
    messages = trace_row.get("messages") or []
    if index < 0 or index >= len(messages):
        return None
    text = messages[index].get("text")
    return text if isinstance(text, str) else None


def keyed_message_texts(run_dir: str, key_path: Optional[str] = None) -> dict[str, set[str]]:
    """``{message_id: {trace slot texts it is keyed to}}``, read from the run's traces.

    This is the *authority* for what each ``message_id`` denotes: the key maps ids to
    ``(trace_id, slot_id)`` and the trace supplies the text.
    """
    key = read_key(key_path or os.path.join(run_dir, "annotation_key.csv"))
    traces = {row["trace_id"]: row for row in load_traces(run_dir)}
    out: dict[str, set[str]] = {}
    for trace_id, slots in key.items():
        row = traces.get(trace_id)
        if row is None:
            raise SheetError(f"{run_dir}: annotation_key lists trace {trace_id!r} "
                             "which is not in the run")
        for slot_id, mid in slots.items():
            text = slot_text(row, slot_id)
            if text is None:
                raise SheetError(f"{run_dir}: {trace_id}/{slot_id} is not a message slot "
                                 "of that trace")
            out.setdefault(mid, set()).add(text)
    return out


def check_sheet_texts(sheets: Mapping[str, Mapping[str, dict]],
                      expected: Mapping[str, set[str]]) -> dict:
    """Fail closed unless every annotated row carries the text of the message it maps to.

    Guards the join itself (J-audit finding): the adapter trusts ``annotation_key.csv``
    and never compared the sheet's ``message_text`` against the trace, so a stale or
    hand-edited sheet would have produced labels for the wrong message silently. Nothing
    is repaired here -- a mismatch is an error, not a label.
    """
    verified = 0
    for name in sorted(sheets):
        for mid, row in sheets[name].items():
            wanted = expected.get(mid)
            if wanted is None:
                raise SheetError(f"{name}: {mid} is not referenced by annotation_key.csv")
            if len(wanted) != 1:
                raise SheetError(
                    f"{name}: {mid} is keyed to {len(wanted)} different message texts "
                    f"({sorted(wanted)}) -- one message_id must denote one text")
            text = (row.get("message_text") or "").strip()
            if not text:
                raise SheetError(
                    f"{name}: {mid} has no message_text; the message-to-annotation join "
                    "cannot be verified (re-generate the sheet rather than filling it in)")
            if text != next(iter(wanted)):
                raise SheetError(
                    f"{name}: {mid} message_text does not match the trace slot it maps to. "
                    f"sheet={text!r} trace={next(iter(wanted))!r}")
            verified += 1
    return {"checked": True, "n_rows_verified": verified, "n_messages": len(expected),
            "rule": ("every sheet row's message_text must equal the trace slot text that "
                     "annotation_key.csv maps its message_id to; a mismatch is refused")}


def speech_act_metadata(sheets: Mapping[str, Mapping[str, dict]]) -> dict:
    """Retain each annotator's speech-act values verbatim (P0-1: metadata, not E).

    Recorded so the divergence between annotators is visible even though it cannot change
    a label; nothing here is normalised, canonised or used to derive E.
    """
    out: dict[str, dict] = {}
    for name in sorted(sheets):
        for mid, row in sheets[name].items():
            out.setdefault(mid, {})[name] = {
                "speech_act_primary": (row.get("speech_act_primary") or "").strip() or None,
                "speech_act_secondary": (row.get("speech_act_secondary") or "").strip() or None,
            }
    return dict(sorted(out.items()))


#: The approved E rule this converter applies, recorded in every manifest. P0-1 was taken
#: post hoc -- after the sheets were filled -- and is labelled as such.
E_RULE_METADATA: dict = {
    "id": "P0-1",
    "decided": "2026-10-24",
    "post_hoc": True,
    "preregistered": False,
    "rule": ("E is a function of coordination_attempt and target only: "
             "YES+B -> E=1; YES+A -> E=0; NO -> E=0; "
             "UNCERTAIN/UNRESOLVED, YES+UNSPECIFIED/UNCLEAR -> E=UNRESOLVED; "
             "YES with a missing or invalid target is rejected"),
    "speech_acts": "retained as metadata only; they can never change E",
    "decision_log": "docs/decision_log.md, entry 'Approved: P0-1 E-label rule'",
}


def translate(row: Mapping, annotator: str) -> tuple[EAnnotation, Optional[str]]:
    """``(annotation, unmapped_cell_or_None)``. Unmapped cells become UNRESOLVED."""
    try:
        return to_e_annotation(row, annotator=annotator), None
    except UnmappedGuideAnnotation:
        cell = tuple((row.get(f) or "").strip().upper() for f in DECISION_FIELDS)
        if cell not in UNMAPPED_CELLS:          # defensive: only the listed cells qualify
            raise
        label = "/".join(cell)
        note = f"{UNMAPPED_NOTE_PREFIX}:{label}; {(row.get('uncertainty_or_notes') or '').strip()}"
        return (EAnnotation(mc=REASON_TO_CODE[UNMAPPED_REASON], reason=UNMAPPED_REASON,
                            target=cell[1], annotator=annotator, note=note.strip("; ")),
                label)


def compare_annotators(sheets: Mapping[str, Mapping[str, dict]]) -> dict:
    """Translate every sheet and list agreements, disagreements and raw differences."""
    names = sorted(sheets)
    ids = [set(sheets[n]) for n in names]
    if any(s != ids[0] for s in ids):
        raise SheetError(f"annotators covered different message sets: "
                         f"{ {n: sorted(sheets[n]) for n in names} }")
    translated = {n: {mid: translate(row, n) for mid, row in sheets[n].items()} for n in names}
    agreed, disagreements, raw_diffs = {}, {}, {}
    for mid in sorted(ids[0]):
        anns = {n: translated[n][mid][0] for n in names}
        keys = {(a.reason.value, a.target) for a in anns.values()}
        if len(keys) == 1:
            agreed[mid] = anns[names[0]]
        else:
            disagreements[mid] = {n: {"reason": a.reason.value, "mc": a.mc.value,
                                      "target": a.target} for n, a in anns.items()}
        fields = {n: tuple((sheets[n][mid].get(f) or "").strip().upper()
                           for f in SHEET_FIELDS[1:5]) for n in names}
        if len(set(fields.values())) > 1:
            raw_diffs[mid] = {n: dict(zip(SHEET_FIELDS[1:5], v)) for n, v in fields.items()}
    unmapped = {n: {mid: cell for mid, (_, cell) in translated[n].items() if cell}
                for n in names}
    return {"annotators": names, "n_messages": len(ids[0]), "agreed": agreed,
            "disagreements": disagreements, "raw_field_differences": raw_diffs,
            "unmapped_cells": unmapped,
            "n_agreed_by_e_value": _count_mc(agreed.values())}


def _count_mc(anns) -> dict:
    out: dict[str, int] = {}
    for a in anns:
        out[a.mc.value] = out.get(a.mc.value, 0) + 1
    return dict(sorted(out.items()))


def final_annotations(comparison: dict, adjudication: Optional[Mapping[str, dict]],
                      adjudicator: Optional[str]) -> tuple[dict[str, EAnnotation], dict]:
    """Agreed annotations plus adjudicated ones; refuses uncovered disagreements."""
    final = dict(comparison["agreed"])
    open_ids = sorted(comparison["disagreements"])
    source = {mid: "agreement" for mid in final}
    adjudicated_unmapped = {}
    if open_ids:
        if adjudication is None:
            raise DisagreementUnresolved(
                f"{len(open_ids)} message(s) need adjudication: {open_ids}")
        uncovered = [mid for mid in open_ids if mid not in adjudication]
        if uncovered:
            raise DisagreementUnresolved(f"adjudication sheet does not cover {uncovered}")
        for mid in open_ids:
            ann, cell = translate(adjudication[mid], adjudicator or "adjudicator")
            final[mid] = ann
            source[mid] = "adjudication"
            if cell:
                adjudicated_unmapped[mid] = cell
    extra = sorted(set(adjudication or {}) - set(open_ids))
    return final, {"source_by_message": dict(sorted(source.items())),
                   "adjudication_rows_ignored_no_disagreement": extra,
                   "adjudicated_unmapped_cells": adjudicated_unmapped}


def load_traces(run_dir: str) -> list[dict]:
    paths = sorted(glob.glob(os.path.join(run_dir, "traces_*.jsonl")))
    if not paths:
        raise SheetError(f"{run_dir}: no traces_*.jsonl files")
    rows = []
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            rows.extend(json.loads(line) for line in handle if line.strip())
    return rows


def label_run(run_dir: str, annotations: Mapping[str, EAnnotation],
              provenance: Mapping, key_path: Optional[str] = None) -> list[dict]:
    """One E record per trace in ``run_dir``; every key message must be annotated."""
    key = read_key(key_path or os.path.join(run_dir, "annotation_key.csv"))
    needed = {mid for slots in key.values() for mid in slots.values()}
    missing = sorted(needed - set(annotations))
    if missing:
        raise SheetError(f"{run_dir}: no final annotation for {missing}")
    out, seen = [], set()
    for raw in load_traces(run_dir):
        trace = trace_from_dict(raw)
        if trace.trace_id in seen:
            raise SheetError(f"{run_dir}: duplicate trace {trace.trace_id}")
        seen.add(trace.trace_id)
        slot_anns = {slot: annotations[mid] for slot, mid in key.get(trace.trace_id, {}).items()}
        slots = slots_from_trace(trace, slot_anns)        # fails closed on a missing slot
        record = label_trace(trace.trace_id, slots, trace.raw_actions,
                             scenario_id=getattr(trace, "scenario_id", None)).to_dict()
        record["message_ids"] = {s: key[trace.trace_id][s] for s in sorted(slot_anns)}
        record["provenance"] = dict(provenance)
        out.append(record)
    stray = sorted(set(key) - seen)
    if stray:
        raise SheetError(f"{run_dir}: key lists traces not in the run: {stray}")
    return out


def build(run_dir: str, annotator_sheets: Mapping[str, str], guide_version: str,
          adjudication_path: Optional[str] = None, adjudicator: Optional[str] = None,
          key_path: Optional[str] = None) -> tuple[list[dict], dict]:
    """Return ``(label_rows, manifest)``. Raises DisagreementUnresolved when blocked."""
    if len(annotator_sheets) < 2:
        raise SheetError("two annotator sheets are required")
    if not guide_version.strip():
        raise SheetError("guide_version is required")
    sheets = {name: read_sheet(path) for name, path in annotator_sheets.items()}
    # P0-1 / J-audit: the join is verified before any label is produced. Every annotated
    # row must carry the text of the trace slot its message_id maps to.
    expected_texts = keyed_message_texts(run_dir, key_path)
    provenance_check = check_sheet_texts(sheets, expected_texts)
    comparison = compare_annotators(sheets)
    adjudication = read_sheet(adjudication_path) if adjudication_path else None
    manifest = {
        "e_sheet_labels_version": E_SHEET_LABELS_VERSION,
        "run_dir": run_dir,
        "guide_version": guide_version,
        "e_rule": dict(E_RULE_METADATA),
        "annotators": {n: {"sheet": p, "sha256": sha256_of(p)} for n, p in annotator_sheets.items()},
        "adjudication": None if not adjudication_path else
            {"sheet": adjudication_path, "sha256": sha256_of(adjudication_path),
             "adjudicator": adjudicator},
        "key": {"path": key_path or os.path.join(run_dir, "annotation_key.csv")},
        "message_provenance": provenance_check,
        "speech_act_metadata": speech_act_metadata(sheets),
        "unmapped_policy": ("empty since P0-1: no speech-act cell is unmapped, because E "
                            "depends only on coordination_attempt and target"),
        "n_messages": comparison["n_messages"],
        "disagreements": comparison["disagreements"],
        "raw_field_differences": comparison["raw_field_differences"],
        "unmapped_cells": comparison["unmapped_cells"],
    }
    manifest["key"]["sha256"] = sha256_of(manifest["key"]["path"])
    try:
        final, sources = final_annotations(comparison, adjudication, adjudicator)
    except DisagreementUnresolved as exc:
        manifest["status"] = "BLOCKED_NEEDS_ADJUDICATION"
        manifest["blocked_reason"] = str(exc)
        exc.manifest = manifest                      # type: ignore[attr-defined]
        raise
    manifest.update(sources)
    provenance = {"guide_version": guide_version, "annotators": sorted(annotator_sheets),
                  "adjudicator": adjudicator if adjudication_path else None,
                  "converter": f"apart.e_sheet_labels {E_SHEET_LABELS_VERSION}"}
    rows = label_run(run_dir, final, provenance, key_path)
    for row in rows:
        row["provenance"]["message_sources"] = {
            s: sources["source_by_message"][m] for s, m in row["message_ids"].items()}
    _, reference = build_reference(rows)
    manifest["status"] = "LABELS_WRITTEN"
    manifest["n_traces"] = len(rows)
    manifest["e_state_counts"] = _count(rows, "e_state")
    manifest["evidence_status_counts"] = _count(rows, "evidence_status")
    manifest["unresolved_gate"] = unresolved_gate(reference)
    return rows, manifest


def _count(rows: Sequence[dict], field: str) -> dict:
    out: dict[str, int] = {}
    for row in rows:
        out[str(row[field])] = out.get(str(row[field]), 0) + 1
    return dict(sorted(out.items()))


def _write_json(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--annotator", action="append", required=True, metavar="NAME=SHEET",
                        help="repeat once per annotator")
    parser.add_argument("--guide-version", required=True)
    parser.add_argument("--adjudication", default=None, help="adjudicated sheet (same format)")
    parser.add_argument("--adjudicator", default=None)
    parser.add_argument("--out-dir", required=True, help="new directory for labels + manifest")
    args = parser.parse_args(argv)

    sheets = {}
    for item in args.annotator:
        name, sep, path = item.partition("=")
        if not sep or not name or not path:
            parser.error(f"--annotator expects NAME=SHEET, got {item!r}")
        sheets[name] = path
    os.makedirs(args.out_dir, exist_ok=True)
    try:
        rows, manifest = build(args.run_dir, sheets, args.guide_version,
                               args.adjudication, args.adjudicator)
    except DisagreementUnresolved as exc:
        _write_json(os.path.join(args.out_dir, "label_manifest.json"), exc.manifest)  # type: ignore[attr-defined]
        print(f"BLOCKED: {exc}. See {args.out_dir}/label_manifest.json")
        return 2
    with open(os.path.join(args.out_dir, "e_labels.jsonl"), "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    _write_json(os.path.join(args.out_dir, "label_manifest.json"), manifest)
    gate = manifest["unresolved_gate"]
    print(f"wrote {len(rows)} labels; states {manifest['e_state_counts']}; "
          f"unresolved gate {'TRIGGERED' if gate['triggered'] else 'not triggered'} "
          f"({gate['n_unresolved']}/{gate['n_assessed']})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
