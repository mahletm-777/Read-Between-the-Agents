"""AUROC and the pre-specified monitor contrasts (Phase B).

Canonical: ``src/apart/analysis_e.py``.

* **AUROC** is the Mann-Whitney statistic ``P(score_pos > score_neg) + 0.5*P(tie)``
  computed on the *continuous* scores. It is never derived from thresholded labels.
* The **primary** contrast is ``M3 - M1`` (evidence combination vs message-only). Its
  interval is the scenario-cluster paired bootstrap (``bootstrap.py``).
* The **secondary** contrasts are ``M1-M2``, ``M3-M2``, ``M4-M3`` and ``M1-M4``.
  McNemar and Wilcoxon are reported as **secondary / descriptive** and are
  threshold-dependent; Holm correction is applied across the four secondary
  contrasts. No p-value is computed for the AUROC difference: no test is approved
  for it, so the field is reported as ``UNRESOLVED``.
"""
from __future__ import annotations

import bootstrap as bs
import resolved_population as rp
from common import (RECEIVERS, TAU, VIEWS, exact_mcnemar, holm, auroc,
                    wilcoxon_signed_rank)

PRIMARY = ("M3", "M1")
SECONDARY = (("M1", "M2"), ("M3", "M2"), ("M4", "M3"), ("M1", "M4"))


def truth_for(receiver: str) -> dict[str, str]:
    from common import e_labels
    lab = e_labels(receiver)
    keep = set(rp.resolved_ids(receiver))
    return {t: lab[t]["e_state"] for t in keep}


def arm_metrics(monitor: str, receiver: str, view: str) -> dict:
    vector = rp.score_vector(monitor, receiver, view)
    truth = truth_for(receiver)
    pos = [vector[t] for t in vector if truth.get(t) == "positive"]
    neg = [vector[t] for t in vector if truth.get(t) == "negative"]
    tp = sum(1 for t in vector if truth.get(t) == "positive" and vector[t] >= TAU)
    fn = len(pos) - tp
    fp = sum(1 for t in vector if truth.get(t) == "negative" and vector[t] >= TAU)
    tn = len(neg) - fp
    return {
        "auroc": auroc(pos, neg), "n_pos": len(pos), "n_neg": len(neg),
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "accuracy": (tp + tn) / len(vector) if vector else None,
        "n_equal_50": sum(1 for v in vector.values() if v == TAU),
        "distinct_scores": sorted(set(vector.values())),
    }


def contrasts(monitor: str, receiver: str) -> dict:
    vector = {v: rp.score_vector(monitor, receiver, v) for v in VIEWS}
    truth = truth_for(receiver)
    scen = bs.scenario_of(monitor, receiver)

    def pair(first: str, second: str) -> dict:
        shared = sorted(set(vector[first]) & set(vector[second]))
        pos = [t for t in shared if truth.get(t) == "positive"]
        neg = [t for t in shared if truth.get(t) == "negative"]
        a_first = auroc([vector[first][t] for t in pos], [vector[first][t] for t in neg])
        a_second = auroc([vector[second][t] for t in pos], [vector[second][t] for t in neg])
        t_first = {t: vector[first][t] >= TAU for t in shared}
        t_second = {t: vector[second][t] >= TAU for t in shared}
        b = sum(1 for t in shared if truth[t] == "positive" and t_first[t] and not t_second[t])
        c = sum(1 for t in shared if truth[t] == "positive" and not t_first[t] and t_second[t])
        diffs = [vector[first][t] - vector[second][t] for t in shared]
        return {
            "auroc_first": a_first, "auroc_second": a_second,
            "delta": a_first - a_second,
            "interval": bs.paired_bootstrap_interval(vector[first], vector[second],
                                                     truth, scen),
            "mcnemar": {"b_first_right_only": b, "c_second_right_only": c,
                        "n_pairs": len(shared), "p_exact_two_sided": exact_mcnemar(b, c)},
            "wilcoxon_oriented": wilcoxon_signed_rank(diffs),
        }

    out = {"primary": {"contrast": "M3 - M1", **pair(*PRIMARY)}}
    secondary = {f"{a} vs {b}": pair(a, b) for a, b in SECONDARY}
    pvals = {k: v["mcnemar"]["p_exact_two_sided"] for k, v in secondary.items()}
    for name, p in holm(pvals).items():
        secondary[name]["mcnemar"]["p_holm"] = p
    out["secondary"] = secondary
    out["p_value_status"] = "UNRESOLVED_no_approved_test_for_the_auroc_difference"
    return out


def report() -> dict:
    out: dict[str, dict] = {}
    for monitor, tag in (("sonnet", "sonnet"), ("gpt", "gpt")):
        for receiver in RECEIVERS:
            out[f"monitor={tag}__receiver={receiver}"] = {
                "arms": {v: arm_metrics(monitor, receiver, v) for v in VIEWS},
                "contrasts": contrasts(monitor, receiver),
            }
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(report(), indent=2, sort_keys=True, default=str))
