"""Apply human adjudication decisions to extraction records (§6.2, §16.3).

The log is append-only JSONL written by ``extraction.cli``. Applying it is
deterministic and idempotent. The original extractor outputs and the automatic
adjudication result are preserved; the applied record carries the human
decision, the fields it changed, the log hash, and the adjudicator's identity.

Nothing here chooses scientific label semantics: a decision is applied only
when a human supplied it, and an unresolved record stays unresolved.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping, Optional

from apart.schema import ATTEMPT_TYPES, DIRECTNESS_CODES, SPEECH_ACT_TYPES
from extraction.adjudicate import CLAIM_CONFIDENCES, CLAIM_TYPES, attempt_group

RESOLVED = "resolved"
PARTIALLY_RESOLVED = "partially_resolved"
NEEDS_ADJUDICATION = "needs_adjudication"


class AdjudicationError(ValueError):
    """The log or a decision is malformed; nothing may be applied."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value, name: str, line: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdjudicationError(f"line {line}: {name} must be a non-empty string")
    return value.strip()


def _claims(claims, line: int) -> Optional[list[dict]]:
    if claims is None:
        return None
    if not isinstance(claims, list):
        raise AdjudicationError(f"line {line}: claims must be a list or null")
    out = []
    for index, claim in enumerate(claims):
        if not isinstance(claim, Mapping):
            raise AdjudicationError(f"line {line}: claims[{index}] must be an object")
        target, kind, confidence = claim.get("target"), claim.get("type"), claim.get("confidence")
        if target not in ("A", "B"):
            raise AdjudicationError(f"line {line}: claims[{index}].target must be A or B")
        if kind not in CLAIM_TYPES:
            raise AdjudicationError(f"line {line}: claims[{index}].type is not a codebook type")
        if confidence not in CLAIM_CONFIDENCES:
            raise AdjudicationError(f"line {line}: claims[{index}].confidence must be clear or ambiguous")
        item = {k: v for k, v in claim.items() if k not in ("target", "type", "confidence")}
        item.update({"target": target, "type": kind, "confidence": confidence})
        out.append(item)
    return out


def normalize_decision(record: Mapping, line: int) -> dict:
    """Validate and normalise one human decision. Raises on anything ambiguous."""
    if not isinstance(record, Mapping):
        raise AdjudicationError(f"line {line}: decision must be an object")
    message_id = _text(record.get("message_id"), "message_id", line)
    coordination = record.get("coordination_attempt")
    if coordination not in ("yes", "unsure", "no"):
        raise AdjudicationError(f"line {line}: coordination_attempt must be yes, unsure or no")
    attempt = record.get("attempt_type")
    if attempt not in ATTEMPT_TYPES:
        raise AdjudicationError(f"line {line}: attempt_type is not a codebook type")
    if attempt_group(attempt) != coordination:
        raise AdjudicationError(
            f"line {line}: coordination_attempt {coordination!r} contradicts attempt_type {attempt!r}"
        )
    directness = record.get("directness")
    if directness is not None and directness not in DIRECTNESS_CODES:
        raise AdjudicationError(f"line {line}: directness is not a codebook code")
    speech = record.get("speech_act_types")
    if speech is not None:
        if not isinstance(speech, list) or any(s not in SPEECH_ACT_TYPES for s in speech):
            raise AdjudicationError(f"line {line}: speech_act_types is not a list of codebook codes")
        speech = list(dict.fromkeys(speech))
    return {
        "message_id": message_id,
        "coordination_attempt": coordination,
        "attempt_type": attempt,
        "directness": directness,
        "speech_act_types": speech,
        "claims": _claims(record.get("claims"), line),
        "adjudication_reason": _text(record.get("adjudication_reason"), "adjudication_reason", line),
        "adjudicated_by": _text(record.get("adjudicated_by"), "adjudicated_by", line),
        "adjudication_id": record.get("adjudication_id") or f"line:{line}",
        "line": line,
    }
def load_adjudications(path: Path) -> tuple[dict[str, dict], str]:
    """Return ({message_id: decision}, sha256 of the log bytes). Rejects conflicts."""
    path = Path(path)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    decisions: dict[str, dict] = {}
    for line_no, raw in enumerate(data.decode("utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AdjudicationError(f"line {line_no}: invalid JSON: {exc}") from exc
        decision = normalize_decision(record, line_no)
        message_id = decision["message_id"]
        if message_id in decisions:
            previous, current = dict(decisions[message_id]), dict(decision)
            previous.pop("line", None)
            current.pop("line", None)
            if previous != current:
                raise AdjudicationError(f"conflicting decisions for message_id {message_id!r}")
            continue
        decisions[message_id] = decision
    return decisions, digest


def apply_adjudication(original: Mapping, decision: Mapping, *, log_hash: str) -> dict:
    """Apply one validated decision, preserving the automatic result as ``auto``."""
    auto = original.get("adjudication") or {}
    before = {
        "claims": auto.get("claims"),
        "attempt_type": auto.get("attempt_type"),
        "coordination_attempt": auto.get("coordination_attempt"),
        "directness": auto.get("directness"),
        "speech_act_types": auto.get("speech_act_types"),
    }
    after = {
        "claims": decision.get("claims"),
        "attempt_type": decision.get("attempt_type"),
        "coordination_attempt": decision.get("coordination_attempt"),
        "directness": decision.get("directness"),
        "speech_act_types": decision.get("speech_act_types"),
    }
    changed = sorted(k for k in before if before[k] != after[k])
    claims_resolved = after["claims"] is not None
    attempt_resolved = after["attempt_type"] is not None and after["coordination_attempt"] is not None
    if claims_resolved and attempt_resolved:
        status = RESOLVED
    elif claims_resolved or attempt_resolved:
        status = PARTIALLY_RESOLVED
    else:
        status = NEEDS_ADJUDICATION
    result = dict(original)
    result.update({
        "message_id": original.get("message_id") or decision["message_id"],
        "text": original.get("text"),
        "message_status": original.get("message_status", "delivered"),
        "annotation_status": status,
        "claims": after["claims"],
        "claims_status": "human_resolved" if claims_resolved else "unresolved",
        "attempt_type": after["attempt_type"],
        "coordination_attempt": after["coordination_attempt"],
        "attempt_status": "human_resolved" if attempt_resolved else "unresolved",
        "directness": after["directness"],
        "directness_status": "human_resolved" if after["directness"] is not None else "unadjudicated",
        "speech_act_types": after["speech_act_types"],
        "speech_act_status": "human_resolved" if after["speech_act_types"] is not None else "unadjudicated",
        "adjudicated_by": decision["adjudicated_by"],
        "adjudication_reason": decision["adjudication_reason"],
        "adjudication_id": decision["adjudication_id"],
        "adjudication_log_hash": log_hash,
        "changed_fields": changed,
        "before": before,
        "after": after,
        "auto": dict(auto),
        "extractor_codes": auto.get("extractor_codes") or {
            "E1": original.get("E1"), "E2": original.get("E2"),
        },
        "logged_disagreements": list(auto.get("logged_disagreements") or []),
        "label_status": "ready" if claims_resolved else "not_computed",
        "computed_label": None,
    })
    return result


def apply_log(records: Iterable[Mapping], path: Path) -> tuple[list[dict], dict]:
    """Apply the log to records. Decisions may override an automatic result too."""
    decisions, digest = load_adjudications(path)
    applied: list[dict] = []
    matched: set[str] = set()
    for original in records:
        auto = original.get("adjudication") or {}
        message_id = original.get("message_id") or ""
        if message_id in decisions:
            applied.append(apply_adjudication(original, decisions[message_id], log_hash=digest))
            matched.add(message_id)
        elif auto.get("needs_adjudication"):
            record = dict(original)
            record.update(
                annotation_status=NEEDS_ADJUDICATION,
                adjudication_log_hash=digest,
                label_status="not_computed",
            )
            applied.append(record)
        else:
            record = dict(original)
            record.setdefault("annotation_status", RESOLVED)
            record.setdefault("label_status", "ready")
            applied.append(record)
    summary = {
        "log_hash": digest,
        "decisions": len(decisions),
        "matched": len(matched),
        "unmatched_decisions": sorted(set(decisions) - matched),
        "still_unresolved": sum(1 for r in applied if r.get("annotation_status") == NEEDS_ADJUDICATION),
    }
    return applied, summary

