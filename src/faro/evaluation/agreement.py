"""
Cohen's kappa, the agreement between two sets of labels beyond what chance gives (labels.py; EVL-08).
"""

from collections import Counter
from collections.abc import Hashable, Sequence


def cohen(pairs: Sequence[tuple[Hashable, Hashable]]) -> float | None:
    """
    Undefined when chance agreement is total, as when both graders gave one answer throughout.
    """
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(a == b for a, b in pairs) / n
    first, second = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    chance = sum(first[c] * second[c] for c in set(first) | set(second)) / (n * n)
    if chance == 1:
        return None
    return (observed - chance) / (1 - chance)
