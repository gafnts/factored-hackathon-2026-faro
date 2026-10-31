"""
Intervals for the evaluation's measures (ADR-0005: Grading, validated before use; Baselines, the router), computed in
code without a statistics library. A percentile bootstrap resamples units with replacement, within strata when they
are given, 1,000 times from a fixed, reported seed, and computes every figure on the same resample, so a difference
between two candidates' figures is paired by construction. A figure undefined on a resample (None, as a kappa is when
both graders gave one answer throughout) is left out of that resample, and the estimate says how many resamples
defined it. A share gets a Wilson interval.
"""

import math
import random
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass

RESAMPLES = 1000
SEED = 20261002
LEVEL = 0.95
# The normal quantile for LEVEL's two-sided interval.
Z = 1.959963984540054

type Measure[T] = Callable[[Sequence[T]], Mapping[str, float | None]]


@dataclass(frozen=True)
class Estimate:
    value: float | None
    low: float | None
    high: float | None
    defined: int

    def excludes_zero(self) -> bool:
        return (
            self.low is not None
            and self.high is not None
            and (self.low > 0 or self.high < 0)
        )

    def to_json(self) -> dict[str, float | int | None]:
        def rounded(x: float | None) -> float | None:
            return None if x is None else round(x, 4)

        return {
            "value": rounded(self.value),
            "low": rounded(self.low),
            "high": rounded(self.high),
            "defined": self.defined,
        }


def percentile(ordered: Sequence[float], share: float) -> float:
    """
    The share's percentile of sorted values, interpolated linearly between ranks.
    """
    at = share * (len(ordered) - 1)
    below = math.floor(at)
    above = min(below + 1, len(ordered) - 1)
    return ordered[below] + (ordered[above] - ordered[below]) * (at - below)


def bootstrap[T](
    units: Sequence[T],
    measure: Measure[T],
    resamples: int = RESAMPLES,
    seed: int = SEED,
    strata: Callable[[T], Hashable] | None = None,
    level: float = LEVEL,
) -> dict[str, Estimate]:
    """
    measure gives every figure of a set of units by name, so all of them come from one resample.
    """
    rng = random.Random(seed)
    groups: dict[Hashable, list[T]] = {}
    for unit in units:
        groups.setdefault(strata(unit) if strata else None, []).append(unit)
    ordered = [groups[k] for k in sorted(groups, key=repr)]
    point = measure(units)
    found: dict[str, list[float]] = {name: [] for name in point}
    for _ in range(resamples):
        drawn = [g[rng.randrange(len(g))] for g in ordered for _ in g]
        for name, value in measure(drawn).items():
            if value is not None and name in found:
                found[name].append(value)
    tail = (1 - level) / 2
    estimates = {}
    for name, value in point.items():
        values = sorted(found[name])
        estimates[name] = Estimate(
            value=value,
            low=percentile(values, tail) if values else None,
            high=percentile(values, 1 - tail) if values else None,
            defined=len(values),
        )
    return estimates


def wilson(hits: int, n: int, z: float = Z) -> tuple[float, float] | None:
    if n == 0:
        return None
    share = hits / n
    centre = (share + z * z / (2 * n)) / (1 + z * z / n)
    spread = (
        z * math.sqrt(share * (1 - share) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    )
    return max(0.0, centre - spread), min(1.0, centre + spread)
