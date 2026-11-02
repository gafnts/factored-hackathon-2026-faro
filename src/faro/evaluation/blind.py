"""
A blind sheet for a person to fill, as the relabel draws it (labels.py): a total split over strata in proportion, and
the sheet written as CSV with its hash, which tells the filled copy from the one drawn.
"""

import csv
import hashlib
import math
from collections.abc import Mapping, Sequence
from pathlib import Path


def allocate(sizes: Mapping[str, int], total: int) -> dict[str, int]:
    """
    total split over the strata in proportion to their sizes, at least one each while total allows, the rest by
    largest remainder.
    """
    present = {k: v for k, v in sorted(sizes.items()) if v}
    whole = sum(present.values())
    if whole <= total:
        return present
    quota = {k: total * v / whole for k, v in present.items()}
    floor = 1 if total >= len(present) else 0
    given = {k: min(v, max(floor, math.floor(quota[k]))) for k, v in present.items()}
    while sum(given.values()) < total:
        k = max(
            (k for k in present if given[k] < present[k]),
            key=lambda k: (quota[k] - given[k], k),
        )
        given[k] += 1
    while sum(given.values()) > total:
        k = max(
            (k for k in given if given[k] > floor),
            key=lambda k: (given[k] - quota[k], k),
        )
        given[k] -= 1
    return given


def write_sheet(path: Path, rows: Sequence[dict[str, str]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as kept:
        writer = csv.DictWriter(kept, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return hashlib.sha256(path.read_bytes()).hexdigest()
