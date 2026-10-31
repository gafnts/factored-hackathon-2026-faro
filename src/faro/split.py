"""
The split, shared by the analysis, the personas, and the evaluation (ADR-0005; DML-09).

- By customer (ADR-0003): a customer is held out when the MD5 of their customer_id, read as an integer, is divisible by
  5, so all of a customer's rows fall on one side.
- By family (ADR-0005's amendment of 2026-10-01): within one label's request families, or one kind of answer, the third
  with the lowest MD5 of their ID is held out, so each label holds out exactly a third and no one picks which.
"""

import hashlib
from collections.abc import Iterable

HELD_OUT_EVERY = 5
FAMILIES_HELD_OUT_EVERY = 3


def _digest(value: str) -> int:
    return int(hashlib.md5(value.encode(), usedforsecurity=False).hexdigest(), 16)


def held_out(customer_id: str) -> bool:
    return _digest(customer_id) % HELD_OUT_EVERY == 0


def held_out_families(family_ids: Iterable[str]) -> frozenset[str]:
    """
    The IDs passed are one label's families (or one kind's answers), all of them: the third depends on the whole group.
    """
    ranked = sorted(set(family_ids), key=lambda f: (_digest(f), f))
    return frozenset(ranked[: len(ranked) // FAMILIES_HELD_OUT_EVERY])
