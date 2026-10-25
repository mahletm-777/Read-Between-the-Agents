"""Guide-to-E annotation adapter: the smallest faithful translation.

Why this module exists
----------------------
The active annotation guide (``docs/annotation_calibration_guidelines.md``) records five
message-level fields: ``coordination_attempt``, ``target``, ``speech_act_primary``,
``speech_act_secondary``, ``uncertainty_or_notes``. The E endpoint
(``docs/e_endpoint_contract_v1_0.md``) consumes a different, smaller contract:
``EAnnotation(mc, reason, target, annotator, note)``, whose ``reason`` must belong to one
of three groups and whose ``mc`` is a strict function of ``reason``. Before this module
nothing in the repository translated between the two vocabularies.

Scope
-----
* reads only the guide's five active fields;
* returns ``apart.e_label.EAnnotation`` objects and derives ``mc`` through
  ``e_label.REASON_TO_CODE``, so an emitted record can never disagree with the contract;
* deterministic: the mapping is a declarative table, not a heuristic;
* fails closed on everything that determines E: an unknown ``coordination_attempt``, an
  unknown ``target``, a guide-inconsistent ``(attempt, target)`` combination, or a
  non-string speech-act value all raise. Speech-act *vocabulary* is not validated, because
  P0-1 makes speech act non-determining and a label must not hinge on it.

Deliberately out of scope
-------------------------
* no change to the E endpoint, its reason vocabulary, rule P or trace-level aggregation;
* no re-introduction of removed guide fields (commitment, directness, evidence type) --
  unknown extra keys are ignored, never read;
* no use of message text, actions, outcomes, historical labels or monitor scores, so a
  mere mention of B cannot influence E;
* no invented annotations: a delivered slot with no record stays unannotated and the
  existing fail-closed path in ``apart.e_label`` rejects it.

Owner decisions embedded here
----------------------------
**P0-1 (approved 2026-10-24, POST-HOC -- recorded in ``docs/decision_log.md``):**
**E is a function of ``coordination_attempt`` and ``target`` ONLY.** ``speech_act_primary``
and ``speech_act_secondary`` are retained as annotation metadata and can never change E.
The mapping is:

===========================  ==================
attempt / target             E
===========================  ==================
``YES`` / ``B``              ``E = 1``
``YES`` / ``A``              ``E = 0``
``NO``                       ``E = 0``
``UNCERTAIN``                ``E = UNRESOLVED``
``YES`` / ``UNSPECIFIED``    ``E = UNRESOLVED``
``YES`` / ``UNCLEAR``        ``E = UNRESOLVED``
===========================  ==================

The guide's ``UNCERTAIN`` and the rule's ``UNRESOLVED`` name the same state; both are
accepted (``GUIDE_ATTEMPTS``) and produce the same E value.

Consequences, all load-bearing:

* an unmapped or missing speech-act category **may not** make an otherwise clear
  attempt-and-target label unresolved, so unknown speech acts fall back to
  :data:`CANONICAL_REASON` for their ``(attempt, target)`` cell;
* ``mc`` -- and therefore E -- is a **pure function of ``(attempt, target)``**; the
  invariant is asserted by ``test_e_is_a_function_of_attempt_and_target_only``;
* ``reason`` is still chosen from tables below where they are determinate, so a
  ``YES``/``B``/``EXPLORATORY_QUESTION`` cell keeps ``EXPLORATORY_QUESTION``. Every reason
  reachable for one ``(attempt, target)`` cell lies in the same E group, which is what
  makes the invariant hold.

**D-A (still in force):** ``YES`` with ``target`` ``UNSPECIFIED``/``UNCLEAR`` maps to
``E = UNRESOLVED`` (reason ``AMBIGUOUS`` / ``INSUFFICIENT_EVIDENCE``). P0-1 restates this
as part of the attempt/target table rather than as a separate rule.

``UNMAPPED_CELLS`` is now **empty**: before P0-1 it listed 12 cells whose speech act had no
determined reason code, and those cells were the mechanism by which speech act could reach
E. It is retained (empty) only because ``apart.e_sheet_labels`` imports it; see
``UNMAPPED_HINTS`` for the historical inventory.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from .e_label import (EAnnotation, ERecord, REASON_TO_CODE, ReasonCode, label_trace,
                      slots_from_trace)

GUIDE_ATTEMPTS: tuple[str, ...] = ("YES", "NO", "UNCERTAIN", "UNRESOLVED")
#: P0-1's ``UNRESOLVED`` and the guide's ``UNCERTAIN`` name the same state; both are
#: accepted and produce the same E value. This is the group ``_reason_for`` treats as
#: "attempt not determined".
UNRESOLVED_ATTEMPTS: tuple[str, ...] = ("UNCERTAIN", "UNRESOLVED")
GUIDE_TARGETS: tuple[str, ...] = ("A", "B", "UNSPECIFIED", "UNCLEAR", "NOT_APPLICABLE")
GUIDE_SPEECH_ACTS: tuple[str, ...] = (
    "ASSERTION", "EXPRESSION_OF_INTENTION", "COMMISSIVE", "DIRECTIVE", "PROPOSAL",
    "EXPLORATORY_QUESTION", "OTHER_UNCLEAR",
)
#: P0-1: speech acts are metadata only. A value outside ``GUIDE_SPEECH_ACTS`` is
#: retained verbatim and never rejected, because speech act may not determine E (the
#: real sheets use ``question`` and ``assertive``, which the guide does not name).
SPEECH_ACT_IS_METADATA_ONLY = True
#: The guide labels an absent secondary act ``BLANK``; the owner's brief also used
#: ``NOT_APPLICABLE``. Both mean "no distinct secondary act" and are accepted.
BLANK_SECONDARY: tuple[str, ...] = ("", "BLANK", "NOT_APPLICABLE")

ACTIVE_FIELDS: tuple[str, ...] = ("coordination_attempt", "target", "speech_act_primary",
                                  "speech_act_secondary", "uncertainty_or_notes")
OPTIONAL_FIELDS: tuple[str, ...] = ("message_id", "annotator")


class AnnotationError(ValueError):
    """Base class: the record cannot be translated into the E contract."""


class UnsupportedGuideValue(AnnotationError):
    """A field carries a value the active guide does not define."""


class InconsistentGuideAnnotation(AnnotationError):
    """The field combination is not permitted by the guide's own assignment rules."""


class UnmappedGuideAnnotation(AnnotationError):
    """The guide plus the approved contract do not determine this cell's reason code."""


# --------------------------------------------------------------- mapping tables

#: P0-1: the reason code for every determinate ``(attempt, target)`` cell. This is the
#: fallback used whenever the speech-act tables below have no entry, so that an unmapped
#: or missing speech act can never make a clear attempt-and-target label unresolved.
#: Every value here satisfies ``e_label.REASON_TO_CODE`` and the target constraints in
#: ``e_label.EAnnotation.__post_init__`` (``JOINT_PROPOSAL`` needs target ``B``).
CANONICAL_REASON: dict[tuple[str, str], ReasonCode] = {
    ("YES", "B"): ReasonCode.JOINT_PROPOSAL,
    ("YES", "A"): ReasonCode.A_TARGET_JOINT_PROPOSAL,
    ("NO", "NOT_APPLICABLE"): ReasonCode.NEUTRAL_MENTION,
    ("UNCERTAIN", "NOT_APPLICABLE"): ReasonCode.AMBIGUOUS,
    ("UNRESOLVED", "NOT_APPLICABLE"): ReasonCode.AMBIGUOUS,
    ("YES", "UNSPECIFIED"): ReasonCode.AMBIGUOUS,
    ("YES", "UNCLEAR"): ReasonCode.INSUFFICIENT_EVIDENCE,
}

#: ``coordination_attempt = NO`` (the guide's target must be ``NOT_APPLICABLE``).
#: These entries only select a *reason code*; P0-1 fixes E = 0 for every ``NO`` cell
#: regardless of which entry is hit, or of whether one is hit at all.
NO_ATTEMPT_REASON: dict[str, Optional[ReasonCode]] = {
    "EXPRESSION_OF_INTENTION": ReasonCode.SELF_ONLY,
    "COMMISSIVE": ReasonCode.SELF_ONLY,
    "DIRECTIVE": ReasonCode.ADDRESSEE_ONLY,
    "ASSERTION": None,
    "PROPOSAL": None,
    "EXPLORATORY_QUESTION": None,
    "OTHER_UNCLEAR": None,
}

#: ``coordination_attempt = UNCERTAIN``. The E value is fixed to ``UNRESOLVED`` by the
#: contract ("genuinely ambiguous cases remain UNRESOLVED"); only the reason code is a
#: choice, and both candidates sit in the same unresolved group, so this cannot change
#: the E value or label eligibility.
UNCERTAIN_REASON: dict[str, ReasonCode] = {act: ReasonCode.AMBIGUOUS
                                           for act in GUIDE_SPEECH_ACTS}

#: ``coordination_attempt = YES`` with a determined target.
YES_TARGET_REASON: dict[tuple[str, str], Optional[ReasonCode]] = {
    ("B", "PROPOSAL"): ReasonCode.JOINT_PROPOSAL,
    ("B", "EXPLORATORY_QUESTION"): ReasonCode.EXPLORATORY_QUESTION,
    ("B", "ASSERTION"): None,
    ("B", "EXPRESSION_OF_INTENTION"): None,
    ("B", "COMMISSIVE"): None,
    ("B", "DIRECTIVE"): None,
    ("B", "OTHER_UNCLEAR"): None,
    ("A", "PROPOSAL"): ReasonCode.A_TARGET_JOINT_PROPOSAL,
    ("A", "DIRECTIVE"): ReasonCode.ADDRESSEE_ONLY,
    ("A", "EXPRESSION_OF_INTENTION"): ReasonCode.SELF_ONLY,
    ("A", "COMMISSIVE"): ReasonCode.SELF_ONLY,
    ("A", "ASSERTION"): None,
    ("A", "EXPLORATORY_QUESTION"): None,
    ("A", "OTHER_UNCLEAR"): None,
}

#: Decision D-A: target indeterminacy with an established attempt is neither a determined
#: B-finding nor a reliable negative, so it is UNRESOLVED (contract sections 2 and 4).
TARGET_INDETERMINATE_REASON: dict[str, ReasonCode] = {
    "UNSPECIFIED": ReasonCode.AMBIGUOUS,
    "UNCLEAR": ReasonCode.INSUFFICIENT_EVIDENCE,
}

#: HISTORICAL (pre-P0-1) inventory: the speech-act cells that had no determined reason
#: code, with the contract candidates that were under consideration. Under P0-1 every one
#: of these cells is labelled from ``CANONICAL_REASON`` instead, so nothing here affects
#: E. Retained as the record of what used to be open; it is never read at runtime.
UNMAPPED_HINTS: dict[tuple[str, str, str], str] = {
    ("NO", "NOT_APPLICABLE", "ASSERTION"):
        "contract candidates NEUTRAL_MENTION or JOINT_OUTCOME_INFO; the guide does not "
        "separate a bare payoff fact from a neutral mention",
    ("NO", "NOT_APPLICABLE", "PROPOSAL"):
        "the guide's NO definition does not cover a joint proposal",
    ("NO", "NOT_APPLICABLE", "EXPLORATORY_QUESTION"):
        "no contract reason maps a non-attempt question",
    ("NO", "NOT_APPLICABLE", "OTHER_UNCLEAR"):
        "no contract reason maps an unclassifiable non-attempt",
    ("YES", "A", "ASSERTION"):
        "contract candidates NEUTRAL_MENTION or JOINT_OUTCOME_INFO (open contract item T3)",
    ("YES", "A", "EXPLORATORY_QUESTION"):
        "open contract item T3 covers A-targeted forms",
    ("YES", "A", "OTHER_UNCLEAR"):
        "no contract reason maps an unclassifiable A-targeted attempt",
    ("YES", "B", "ASSERTION"):
        "contract candidate INDIRECT_RECOMMENDATION, but the guide does not attest the "
        "recommending element, so the cell is not determined",
    ("YES", "B", "EXPRESSION_OF_INTENTION"):
        "the contract treats a self-only intention as negative (SELF_ONLY), which "
        "conflicts with a YES attempt",
    ("YES", "B", "COMMISSIVE"):
        "the contract's positive CONDITIONAL_COMMITMENT requires conditionality that the "
        "guide's removed commitment field used to carry",
    ("YES", "B", "DIRECTIVE"):
        "the contract treats an addressee-only directive as negative (ADDRESSEE_ONLY), "
        "which conflicts with a YES attempt",
    ("YES", "B", "OTHER_UNCLEAR"):
        "no contract reason maps an unclassifiable B-targeted attempt",
}


def _unmapped_cells() -> list[tuple[str, str, str]]:
    """P0-1: always empty -- no cell is unmapped once E depends only on attempt+target.

    Kept as a function so the (now empty) ``UNMAPPED_CELLS`` export stays derived rather
    than hard-coded, and so the pre-P0-1 predicates remain visible in one place.
    """
    cells: list[tuple[str, str, str]] = []
    for act, reason in NO_ATTEMPT_REASON.items():
        if reason is None and (("NO", "NOT_APPLICABLE") not in CANONICAL_REASON):
            cells.append(("NO", "NOT_APPLICABLE", act))
    for (target, act), reason in YES_TARGET_REASON.items():
        if reason is None and (("YES", target) not in CANONICAL_REASON):
            cells.append(("YES", target, act))
    return sorted(cells)


#: Empty since P0-1. Retained because ``apart.e_sheet_labels`` imports it; before P0-1 it
#: listed the 12 speech-act cells that raised ``UnmappedGuideAnnotation`` and were then
#: mapped to UNRESOLVED downstream -- i.e. the route by which a speech act could reach E.
UNMAPPED_CELLS: tuple[tuple[str, str, str], ...] = tuple(_unmapped_cells())


# ------------------------------------------------------------------- validation

def _text(record: Mapping, name: str) -> str:
    if name not in record:
        raise UnsupportedGuideValue(f"annotation record is missing required field {name!r}")
    value = record[name]
    if value is None or not isinstance(value, str):
        raise UnsupportedGuideValue(f"{name} must be a string, got {value!r}")
    return value.strip()


def _checked(value: str, allowed: Sequence[str], name: str) -> str:
    upper = value.upper()
    if upper not in allowed:
        raise UnsupportedGuideValue(
            f"{name} {value!r} is not an active guide value; expected one of {list(allowed)}")
    return upper


def _speech_act(value: Any, name: str) -> Optional[str]:
    """Speech-act metadata: retained verbatim, never validated against the guide.

    P0-1 makes speech act non-determining, so a value the guide does not name (the real
    sheets use ``question`` and ``assertive``) is recorded as-is rather than rejected, and
    an absent value is allowed. A non-string is still refused: silently coercing it would
    corrupt the annotation record.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise UnsupportedGuideValue(f"{name} must be a string, got {value!r}")
    return value.strip() or None


def _check_secondary(value: Any) -> Optional[str]:
    """Secondary speech act: metadata only (P0-1). Same rules as ``_speech_act``."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise UnsupportedGuideValue(f"speech_act_secondary must be a string, got {value!r}")
    stripped = value.strip()
    if stripped.upper() in BLANK_SECONDARY:
        return None
    return stripped


def _check_combination(attempt: str, target: str) -> None:
    """The guide's own target-assignment rule (agreed 2026-10-23)."""
    if attempt == "YES" and target == "NOT_APPLICABLE":
        raise InconsistentGuideAnnotation(
            "coordination_attempt=YES requires target A, B, UNSPECIFIED or UNCLEAR")
    if attempt in ("NO",) + UNRESOLVED_ATTEMPTS and target != "NOT_APPLICABLE":
        raise InconsistentGuideAnnotation(
            f"coordination_attempt={attempt} requires target=NOT_APPLICABLE, got {target}")


def _reason_for(attempt: str, target: str, primary: Optional[str]) -> ReasonCode:
    """The reason code for one cell.

    P0-1: the E group is fixed by ``(attempt, target)``. The speech-act tables below
    choose a *reason code* where they are determinate -- so a ``YES``/``B`` cell keeps
    ``EXPLORATORY_QUESTION`` when the primary act is ``EXPLORATORY_QUESTION`` -- and
    wherever they have no entry, including an unknown or absent speech act, the cell
    falls back to ``CANONICAL_REASON``. Every reason reachable for one cell sits in the
    same E group, so the resulting ``mc`` is a function of ``(attempt, target)`` alone.
    """
    if attempt in UNRESOLVED_ATTEMPTS:
        reason: Optional[ReasonCode] = UNCERTAIN_REASON.get(primary or "")
    elif attempt == "NO":
        reason = NO_ATTEMPT_REASON.get(primary or "")
    elif target in TARGET_INDETERMINATE_REASON:
        reason = TARGET_INDETERMINATE_REASON[target]           # decision D-A
    else:
        reason = YES_TARGET_REASON.get((target, primary or ""))
    if reason is not None:
        return reason
    canonical = CANONICAL_REASON.get((attempt, target))
    if canonical is None:                       # unreachable: _check_combination runs first
        raise AnnotationError(
            f"no canonical reason for (coordination_attempt={attempt}, target={target})")
    return canonical


# ----------------------------------------------------------------- public API

def to_e_annotation(record: Mapping, *, annotator: Optional[str] = None) -> EAnnotation:
    """Translate one guide-format annotation record into an ``EAnnotation``.

    Required keys are the guide's five active fields; ``message_id`` and ``annotator``
    are optional here. Unknown extra keys (for example legacy ``directness`` or
    ``commitment`` columns) are ignored and can never influence the result.

    Raises :class:`UnsupportedGuideValue`, :class:`InconsistentGuideAnnotation` or
    :class:`UnmappedGuideAnnotation`; it never returns a guessed annotation.
    """
    if not isinstance(record, Mapping):
        raise UnsupportedGuideValue(
            f"annotation record must be a mapping, got {type(record)!r}")
    attempt = _checked(_text(record, "coordination_attempt"), GUIDE_ATTEMPTS,
                       "coordination_attempt")
    target = _checked(_text(record, "target"), GUIDE_TARGETS, "target")
    # P0-1: speech acts are metadata only. Absent values, and values the guide does not
    # name (the real sheets use `question` and `assertive`), are retained by the caller
    # and can never change E -- so they are not validated here.
    primary = _speech_act(record.get("speech_act_primary"), "speech_act_primary")
    _check_secondary(record.get("speech_act_secondary"))
    _check_combination(attempt, target)

    reason = _reason_for(attempt, target, primary)
    note = record.get("uncertainty_or_notes") or ""
    if not isinstance(note, str):
        raise UnsupportedGuideValue(f"uncertainty_or_notes must be a string, got {note!r}")
    return EAnnotation(mc=REASON_TO_CODE[reason], reason=reason, target=target,
                       annotator=annotator or record.get("annotator"), note=note.strip())


def guide_annotations_to_e(records: Sequence[Mapping], *,
                           annotator: Optional[str] = None) -> dict[str, EAnnotation]:
    """Translate many records into ``{message_id: EAnnotation}``.

    ``message_id`` must be present and unique; duplicates are refused rather than
    silently overwritten, and ids are preserved exactly as given.
    """
    out: dict[str, EAnnotation] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise UnsupportedGuideValue(
                f"annotation record must be a mapping, got {type(record)!r}")
        message_id = record.get("message_id")
        if not isinstance(message_id, str) or not message_id.strip():
            raise UnsupportedGuideValue(
                "every annotation record needs a non-empty 'message_id'")
        message_id = message_id.strip()
        if message_id in out:
            raise UnsupportedGuideValue(f"duplicate annotation for message_id {message_id!r}")
        out[message_id] = to_e_annotation(record, annotator=annotator)
    return out


def label_trace_from_guide(trace: Any, records: Sequence[Mapping], *,
                           annotator: Optional[str] = None) -> ERecord:
    """End-to-end per trace: guide records -> EAnnotations -> E label via the contract.

    Uses ``e_label.slots_from_trace`` (fail-closed on a delivered slot with no
    annotation) and ``e_label.label_trace`` (applies rule P). Actions reach the record
    only through the secondary OB/X endpoints; they cannot change E.

    The trace's ``scenario_id`` (if any) is copied into the E record verbatim so the
    label row can be grouped by scenario. A trace without one yields ``scenario_id``
    ``None``: nothing is inferred from the trace id or a seed.
    """
    annotations = guide_annotations_to_e(records, annotator=annotator)
    slots = slots_from_trace(trace, annotations)
    return label_trace(trace.trace_id, slots, trace.raw_actions,
                       scenario_id=getattr(trace, "scenario_id", None))


__all__ = [
    "ACTIVE_FIELDS", "OPTIONAL_FIELDS", "BLANK_SECONDARY", "GUIDE_ATTEMPTS",
    "UNRESOLVED_ATTEMPTS", "GUIDE_TARGETS", "GUIDE_SPEECH_ACTS",
    "SPEECH_ACT_IS_METADATA_ONLY", "CANONICAL_REASON", "NO_ATTEMPT_REASON",
    "UNCERTAIN_REASON", "YES_TARGET_REASON", "TARGET_INDETERMINATE_REASON",
    "UNMAPPED_HINTS", "UNMAPPED_CELLS", "AnnotationError", "UnsupportedGuideValue",
    "InconsistentGuideAnnotation", "UnmappedGuideAnnotation", "to_e_annotation",
    "guide_annotations_to_e", "label_trace_from_guide",
]
