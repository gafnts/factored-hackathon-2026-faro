"""
Agreement and its intervals: kappa against a table worked by hand, a Wilson interval, and a percentile bootstrap that
resamples within strata and pairs every statistic on one resample (EVL-08).
"""

from collections.abc import Sequence

import pytest

from faro.evaluation import agreement, intervals


def test_cohen_s_kappa_matches_a_table_worked_by_hand() -> None:
    table = [("y", "y")] * 20 + [("y", "n")] * 5 + [("n", "y")] * 10 + [("n", "n")] * 15

    assert agreement.cohen(table) == pytest.approx(0.4)
    assert agreement.cohen([("y", "y"), ("n", "n")]) == pytest.approx(1.0)
    assert agreement.cohen([("y", "y")] * 4) is None
    assert agreement.cohen([]) is None


def test_wilson_matches_the_published_interval() -> None:
    low, high = intervals.wilson(9, 10) or (0.0, 0.0)

    assert (round(low, 3), round(high, 3)) == (0.596, 0.982)
    assert intervals.wilson(0, 0) is None
    every = intervals.wilson(10, 10) or (0.0, 0.0)
    assert every[0] == pytest.approx(0.7225, abs=1e-4)
    assert every[1] == pytest.approx(1.0)


def test_percentile_interpolates_between_ranks() -> None:
    assert intervals.percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert intervals.percentile([5.0], 0.975) == 5.0


def test_the_bootstrap_is_seeded_stratified_and_paired() -> None:
    units = [("a", 1.0), ("a", 3.0), ("b", 10.0), ("b", 20.0), ("b", 30.0)]
    seen: list[list[tuple[str, float]]] = []

    def measure(drawn: Sequence[tuple[str, float]]) -> dict[str, float | None]:
        seen.append(list(drawn))
        mean = sum(v for _, v in drawn) / len(drawn)
        doubled = 2 * mean
        return {"mean": mean, "zero": doubled - 2 * mean}

    first = intervals.bootstrap(
        units, measure, resamples=200, seed=7, strata=lambda u: u[0]
    )
    again = intervals.bootstrap(
        units, measure, resamples=200, seed=7, strata=lambda u: u[0]
    )

    assert first == again
    assert first["mean"].value == pytest.approx(12.8)
    assert first["mean"].low is not None and first["mean"].high is not None
    assert first["mean"].low < 12.8 < first["mean"].high
    assert (first["zero"].low, first["zero"].high) == (0.0, 0.0)
    assert not first["zero"].excludes_zero()
    assert all(sum(u[0] == "a" for u in drawn) == 2 for drawn in seen)


def test_an_undefined_statistic_is_counted_out_of_its_resamples() -> None:
    found = intervals.bootstrap(
        [1, 2], lambda d: {"odd": None if sum(d) % 2 else 1.0}, resamples=100
    )

    assert 0 < found["odd"].defined < 100
    empty = intervals.bootstrap([], lambda d: {"x": None})
    assert empty["x"] == intervals.Estimate(None, None, None, 0)
