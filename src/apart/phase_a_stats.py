"""Standalone statistics utilities for the proposed No Message / Neutral
Message / Informative Message experiment:
an exact McNemar test and a separate, assumption-driven power/sample-size
calculator. Neither is imported by `phase_a_runner.py` or anywhere else in
the trial-execution path -- both are pure functions over counts/
probabilities supplied by the caller, testable with synthetic numbers
alone, with no scenario or model dependency.

Conventions (documented, not left implicit):
- Uses the EXACT two-sided binomial test on discordant pairs (each
  discordant pair treated as a Bernoulli(0.5) trial under the null), NOT
  the chi-square continuity-corrected approximation some software
  defaults to -- the two can disagree at the small N typical of a pilot.
- n10 == n01 == 0 (no discordant pairs): returns p=1.0, explicitly flagged
  as uninformative -- NOT evidence of equivalence (see the discussion of
  equivalence margins this session).
- The power calculator uses the NORMAL APPROXIMATION to McNemar's power
  (via `statistics.NormalDist`, stdlib). This approximation degrades for
  small expected discordant-pair counts -- precisely the regime a modest
  pilot is likely to produce -- and no exact/simulation-based power method
  is implemented here. The power calculator never returns or implies a
  final sample-size commitment; it requires the caller to supply all
  assumptions explicitly and is not invoked anywhere else in this module
  set automatically.
"""
import math
from dataclasses import dataclass
from statistics import NormalDist


@dataclass(frozen=True)
class McNemarResult:
    n10: int
    n01: int
    statistic: int  # min(n10, n01), the exact test's basis
    p_value: float


def mcnemar_test(n10: int, n01: int) -> McNemarResult:
    """Exact two-sided McNemar test on discordant pair counts.

    n10: pairs where condition 1 was positive and condition 2 negative.
    n01: pairs where condition 2 was positive and condition 1 negative.
    (Which condition is "1" vs "2" is the caller's convention; the test
    is symmetric in n10/n01.)
    """
    if not isinstance(n10, int) or not isinstance(n01, int):
        raise ValueError("n10 and n01 must be integers.")
    if n10 < 0 or n01 < 0:
        raise ValueError("n10 and n01 must be non-negative.")

    n = n10 + n01
    if n == 0:
        return McNemarResult(n10=0, n01=0, statistic=0, p_value=1.0)

    k = min(n10, n01)
    # Exact two-sided p-value: for k <= n/2 this is 2 * P(X <= k) under
    # X ~ Binomial(n, 0.5), capped at 1.0.
    cdf_k = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    p_value = min(1.0, 2 * cdf_k)
    return McNemarResult(n10=n10, n01=n01, statistic=k, p_value=p_value)


@dataclass(frozen=True)
class PowerResult:
    power: float
    assumptions: dict


def _validate_probability(name: str, value: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number.")
    if not (0.0 <= value <= 1.0):
        raise ValueError(f"{name} must be in [0, 1].")


def estimate_power_mcnemar(
    *,
    p10: float,
    p01: float,
    n_pairs: int,
    alpha: float = 0.05,
) -> PowerResult:
    """Assumption-driven power estimate for McNemar's test, using the
    normal approximation. `p10`/`p01` are the ASSUMED probabilities (per
    pair) of each discordant direction -- inputs the caller must supply
    from pilot data or an explicit assumption, never invented here.

    Does not recommend or commit to a sample size; `n_pairs` is a
    required input, not something this function searches for.
    """
    _validate_probability("p10", p10)
    _validate_probability("p01", p01)
    if p10 + p01 > 1.0:
        raise ValueError("p10 + p01 must not exceed 1 (mutually exclusive discordant-pair rates).")
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha must be in (0, 1).")
    if not isinstance(n_pairs, int) or isinstance(n_pairs, bool) or n_pairs <= 0:
        raise ValueError("n_pairs must be a positive integer.")

    assumptions = {"p10": p10, "p01": p01, "n_pairs": n_pairs, "alpha": alpha}
    p_discordant = p10 + p01
    if p_discordant == 0.0:
        # No discordant pairs expected under these assumptions: the test
        # has no information to detect any difference. Power is 0, not
        # undefined -- stated explicitly rather than silently returning a
        # number that looks meaningful.
        return PowerResult(power=0.0, assumptions=assumptions)

    z_alpha = NormalDist().inv_cdf(1 - alpha / 2)
    effect = abs(p10 - p01)
    se = math.sqrt(p_discordant / n_pairs)
    if se == 0.0:
        power = 0.0
    else:
        z = effect / se - z_alpha
        power = NormalDist().cdf(z)
    power = max(0.0, min(1.0, power))
    return PowerResult(power=power, assumptions=assumptions)


def required_n_mcnemar(
    *,
    p10: float,
    p01: float,
    alpha: float = 0.05,
    target_power: float = 0.8,
) -> int:
    """Assumption-driven required-N estimate (normal approximation),
    computed FROM the supplied assumptions -- not a confirmatory
    sample-size commitment. Callers must still decide, separately,
    whether to adopt this number as a final N."""
    _validate_probability("p10", p10)
    _validate_probability("p01", p01)
    if p10 + p01 > 1.0:
        raise ValueError("p10 + p01 must not exceed 1 (mutually exclusive discordant-pair rates).")
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha must be in (0, 1).")
    if not (0.0 < target_power < 1.0):
        raise ValueError("target_power must be in (0, 1).")

    p_discordant = p10 + p01
    effect = abs(p10 - p01)
    if p_discordant == 0.0 or effect == 0.0:
        raise ValueError(
            "Cannot size a study with zero expected discordant-pair rate or zero "
            "expected effect; no finite N achieves the target power under these assumptions."
        )

    z_alpha = NormalDist().inv_cdf(1 - alpha / 2)
    z_power = NormalDist().inv_cdf(target_power)
    n = p_discordant * ((z_alpha + z_power) / effect) ** 2
    return math.ceil(n)
