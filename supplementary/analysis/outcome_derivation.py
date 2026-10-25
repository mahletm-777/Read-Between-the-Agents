"""Outcome derivation for the sender-receiver interaction (Phase A).

Definitions (canonical: ``src/apart/phase_a_results.py``):

* **receiver B-choice** -- the receiver (agent_2) chose B. Denominator = valid trials.
* **BB**  -- both agents chose B (joint outcome ``B/B``).
* **OB**  -- at least one agent chose B (``A/B``, ``B/A`` or ``B/B``).
* A trial is **valid** when both actions resolved to A or B. Invalid, missing and
  unparseable outcomes are excluded from every denominator and are never counted
  as zero.

Structural bound: the scripted sender plays B in only 10 of 20 scenarios, so
``BB <= 10/20`` and ``OB >= 10/20`` in every condition.
"""
from __future__ import annotations

from collections import Counter

from common import CONDITIONS, RECEIVERS, plan, trials, wilson

JOINT = ("AA", "AB", "BA", "BB")


def _valid(row: dict) -> bool:
    return row.get("validity") == "valid" and row.get("parsed_action") in ("A", "B")


def _meta(row: dict, key: str):
    return (row.get("metadata") or {}).get(key)


def sender_actions() -> dict[str, str]:
    return {s["scenario_id"]: s["sender_action"] for s in plan()["scenarios"]}


def condition_counts(receiver: str) -> dict[str, dict]:
    """Per-condition counts of receiver B-choice, BB and OB (denominator = 20).

    ``condition`` and ``scenario_id`` are read from the trial ``metadata`` block.
    """
    senders = sender_actions()
    out: dict[str, dict] = {}
    for cond in CONDITIONS:
        rows = [r for r in trials(receiver) if _meta(r, "condition") == cond]
        valid = [r for r in rows if _valid(r)]
        joint = Counter()
        for row in valid:
            joint[f"{senders[_meta(row, 'scenario_id')]}{row['parsed_action']}"] += 1
        n = len(valid)
        recv_b = sum(1 for r in valid if r["parsed_action"] == "B")
        bb = joint["BB"]
        ob = joint["AB"] + joint["BA"] + joint["BB"]
        out[cond] = {
            "n_trials": len(rows), "denominator": n,
            "receiver_b_count": recv_b, "receiver_b_rate": recv_b / n if n else None,
            "bb_count": bb, "bb_rate": bb / n if n else None,
            "ob_count": ob, "ob_rate": ob / n if n else None,
            "joint": {k: joint[k] for k in JOINT},
            "n_excluded": len(rows) - n,
            "receiver_b_wilson95": list(wilson(recv_b, n)) if n else None,
        }
    return out


def all_conditions() -> dict[str, dict]:
    return {receiver: condition_counts(receiver) for receiver in RECEIVERS}


def validity_summary() -> dict[str, dict]:
    """Retry / validity accounting, so excluded and retried states stay visible."""
    out = {}
    for receiver in RECEIVERS:
        rows = trials(receiver)
        out[receiver] = {
            "n_trials": len(rows),
            "validity": dict(Counter(r.get("validity") for r in rows)),
            "parsed_action": dict(Counter(str(r.get("parsed_action")) for r in rows)),
            "error_present": sum(1 for r in rows if r.get("error")),
            "call_log_len_gt_1": sum(1 for r in rows if len(r.get("call_log") or []) > 1),
            "total_attempts": sum(len(r.get("call_log") or []) for r in rows),
        }
    return out


if __name__ == "__main__":
    import json
    print(json.dumps({"conditions": all_conditions(), "validity": validity_summary()},
                     indent=2, sort_keys=True))
