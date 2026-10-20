"""Tests for the standalone Phase A statistics utilities: exact McNemar
and the separate, assumption-driven power/sample-size calculator."""
import inspect
import math

import pytest

from apart.phase_a_stats import estimate_power_mcnemar, mcnemar_test, required_n_mcnemar


def test_mcnemar_zero_discordant_pairs_returns_p_one():
    result = mcnemar_test(0, 0)
    assert result.p_value == 1.0


def test_mcnemar_balanced_discordant_pairs_returns_p_one():
    result = mcnemar_test(5, 5)
    assert result.p_value == pytest.approx(1.0)


def test_mcnemar_rejects_negative_counts():
    with pytest.raises(ValueError):
        mcnemar_test(-1, 3)


def test_mcnemar_rejects_non_integer_counts():
    with pytest.raises(ValueError):
        mcnemar_test(1.5, 3)


def test_mcnemar_worked_example_independently_verified():
    # n10=1, n01=9 (10 discordant pairs). Independently recomputed here via
    # a separate summation, not by calling into mcnemar_test's internals.
    n10, n01 = 1, 9
    n = n10 + n01
    k = min(n10, n01)
    independent_cdf = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    independent_p = min(1.0, 2 * independent_cdf)

    result = mcnemar_test(n10, n01)
    assert result.p_value == pytest.approx(independent_p)
    assert result.p_value == pytest.approx(22 / 1024)


def test_power_calculator_rejects_out_of_range_probabilities():
    with pytest.raises(ValueError):
        estimate_power_mcnemar(p10=1.5, p01=0.1, n_pairs=100)
    with pytest.raises(ValueError):
        estimate_power_mcnemar(p10=0.6, p01=0.6, n_pairs=100)  # sum > 1


def test_power_calculator_rejects_bad_alpha_or_n():
    with pytest.raises(ValueError):
        estimate_power_mcnemar(p10=0.1, p01=0.2, n_pairs=100, alpha=1.5)
    with pytest.raises(ValueError):
        estimate_power_mcnemar(p10=0.1, p01=0.2, n_pairs=0)


def test_power_calculator_zero_discordant_rate_returns_zero_power():
    result = estimate_power_mcnemar(p10=0.0, p01=0.0, n_pairs=100)
    assert result.power == 0.0


def test_required_n_rejects_zero_effect():
    with pytest.raises(ValueError):
        required_n_mcnemar(p10=0.2, p01=0.2, target_power=0.8)


def test_required_n_and_power_are_mutually_consistent():
    n = required_n_mcnemar(p10=0.30, p01=0.10, alpha=0.05, target_power=0.8)
    result = estimate_power_mcnemar(p10=0.30, p01=0.10, n_pairs=n, alpha=0.05)
    assert result.power >= 0.8 - 0.02  # required_n rounds up, so power should meet/exceed target


def test_power_calculator_is_separate_from_runner():
    import apart.phase_a_runner as runner_module

    source = inspect.getsource(runner_module)
    assert "phase_a_stats" not in source
