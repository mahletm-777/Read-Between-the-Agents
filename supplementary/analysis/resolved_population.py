"""Resolved-population filter for the monitor evaluation (Phase B).

All reported monitor numbers use the **resolved** population: traces whose
coordination evidence resolved to a definite E state (``evidence_status == 'resolved'``).
For each receiver that is 40 traces -- 20 E-positive and 20 E-negative -- and the
other 40 traces are ``no_agent_message`` (there is no message channel in C0), which
are outside the sensitivity/ROC comparison.

This module makes that selection explicit and returns the trace ids plus each
receiver's score vector per monitor view, so every downstream number is traceable
to the same population.
"""
from __future__ import annotations

from common import CONDITIONS, RECEIVERS, VIEWS, e_labels, scores


def resolved_ids(receiver: str) -> list[str]:
    return sorted(t for t, row in e_labels(receiver).items()
                  if row.get("evidence_status") == "resolved")


def population(receiver: str) -> dict:
    lab = e_labels(receiver)
    resolved = resolved_ids(receiver)
    pos = sorted(t for t in resolved if lab[t]["e_state"] == "positive")
    neg = sorted(t for t in resolved if lab[t]["e_state"] == "negative")
    return {"n_traces_total": len(lab), "n_resolved": len(resolved),
            "n_positive": len(pos), "n_negative": len(neg),
            "positive_ids": pos, "negative_ids": neg}


def score_vector(monitor: str, receiver: str, view: str) -> dict[str, float]:
    """``trace_id -> score`` for one monitor view, restricted to the resolved set."""
    keep = set(resolved_ids(receiver))
    out: dict[str, float] = {}
    for cond in CONDITIONS:
        for row in scores(monitor, receiver, cond):
            if row.get("view") == view and row.get("trace_id") in keep:
                out[row["trace_id"]] = row["score"]
    return out


def paired_vectors(monitor: str) -> dict[str, dict[str, dict[str, float]]]:
    return {receiver: {view: score_vector(monitor, receiver, view) for view in VIEWS}
            for receiver in RECEIVERS}


if __name__ == "__main__":
    import json
    print(json.dumps({r: population(r) for r in RECEIVERS}, indent=2, sort_keys=True))
