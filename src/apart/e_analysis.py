"""DEPRECATED compatibility shim. Use :mod:`apart.analysis_e` instead.

Why deprecated
--------------
This module encoded a statistical contract that the approved plan supersedes:
paired *accuracy* as the primary metric, only two contrasts (M3 vs M1 and
M4 vs M3), no paired AUROC difference, no precision/F1, no Holm family, and no
score-equals-50 diagnostic. That discrepancy is recorded in
``docs/e_analysis_plan_v1_0.md`` section 10 and in ``docs/decision_log.md``
(entry "2026-10-23 - Approved: E-based statistical analysis plan").

The canonical implementation is :mod:`apart.analysis_e`, which implements the
approved plan: primary contrast M3 vs M1; primary metric = paired AUROC
difference with an uncertainty interval; the four approved secondary contrasts;
Holm across that family; fixed ``score >= 50`` threshold metrics; explicit
degenerate reasons; unresolved-label bounds; and missingness reporting.

This module now only forwards to the canonical one and emits a
``DeprecationWarning`` on import. It holds no scientific policy of its own.

Record-shape adapter
--------------------
``run_e_analysis`` accepts the canonical contract records
(``{"trace_id", "e", "evidence_status", ...}``) and forwards them unchanged;
:func:`apart.analysis_e.parse_e_label` understands that shape directly.

Eligibility follows ``e``, not ``evidence_status``. In particular the contract
records that ``E = 1`` may coexist with ``evidence_status = obscured`` (a positive
finding on one slot outranks an obscuring failure elsewhere), so ``obscured`` and
``partial`` do **not** exclude a record. The only status-driven exclusion is the
``no_agent_message`` stratum, which is kept distinct from resolved ``E = 0``.
"""

from __future__ import annotations

import warnings
from typing import Mapping, Sequence

import apart.analysis_e as _canonical
from apart.analysis_e import (  # noqa: F401  (re-exported canonical API)
    ARMS,
    CONTRASTS,
    PRIMARY_CONTRAST,
    PRIMARY_CONTRAST_STATUS,
    SECONDARY_CONTRASTS,
    TAU,
    LabelError,
    auroc,
    build_reference,
    clopper_pearson,
    exact_mcnemar,
    holm,
    paired_accuracy_difference,
    paired_auroc_difference,
    run_analysis_e,
    wilson,
)

DEPRECATED = True
CANONICAL_MODULE = "apart.analysis_e"

#: Legacy names kept for import compatibility only.
PRIMARY_PAIR = _canonical.PRIMARY_CONTRAST
SECONDARY_PAIR = ("M4", "M3")

_DEPRECATION_MESSAGE = (
    "apart.e_analysis is DEPRECATED and carries no scientific policy of its own. "
    "The approved E-based analysis lives in apart.analysis_e "
    "(primary contrast M3 vs M1; primary metric paired AUROC difference). "
    "See docs/e_analysis_plan_v1_0.md section 10."
)

warnings.warn(_DEPRECATION_MESSAGE, DeprecationWarning, stacklevel=2)

#: Reporting only: the contract's evidence_status vocabulary (contract section 6).
#: NOT used to convert or reclassify records -- eligibility follows ``e``.
CONTRACT_EVIDENCE_STATUSES = ("no_agent_message", "obscured", "partial", "resolved")


def to_label_row(record: Mapping) -> dict:
    """Return a label row the canonical analysis accepts.

    This is deliberately a PASS-THROUGH: ``apart.analysis_e.parse_e_label`` already
    understands the contract record shape (``e`` + ``evidence_status``), so no lossy
    status conversion is performed here.

    In particular, ``evidence_status`` in {``obscured``, ``partial``} does NOT exclude
    a record whose ``e`` is ``True``: the contract records that ``E = 1`` may coexist
    with ``evidence_status = obscured`` (contract section 6). Eligibility follows
    ``e``.
    """
    missing = [key for key in ("trace_id", "e") if key not in record]
    if missing:
        raise LabelError(f"E record is missing required field(s) {missing}")
    row = {"trace_id": record["trace_id"], "e": record["e"]}
    for optional in ("evidence_status", "joint_action"):
        if optional in record:
            row[optional] = record[optional]
    return row


def run_e_analysis(e_records: Sequence[Mapping], score_rows: Sequence[Mapping],
                   views: Sequence[str] = ("M1", "M2", "M3", "M4"), **kwargs) -> dict:
    """Deprecated entry point. Delegates to :func:`apart.analysis_e.run_analysis_e`."""
    warnings.warn(
        "apart.e_analysis.run_e_analysis is deprecated; call "
        "apart.analysis_e.run_analysis_e instead.",
        DeprecationWarning, stacklevel=2,
    )
    label_rows = [to_label_row(record) for record in e_records]
    if tuple(views) != tuple(ARMS):
        kwargs.setdefault("arms", tuple(views))
    return run_analysis_e(label_rows, list(score_rows), **kwargs)
