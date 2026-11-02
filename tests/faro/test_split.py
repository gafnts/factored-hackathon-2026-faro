"""
The split holds out about a fifth of customers, by the MD5 of their ID (ADR-0003; DML-09).
"""

import hashlib

from faro.split import HELD_OUT_EVERY, held_out


def md5(value: str) -> int:
    return int(hashlib.md5(value.encode(), usedforsecurity=False).hexdigest(), 16)


def test_holds_out_a_fifth_of_customers_by_their_md5() -> None:
    ids = [f"CUS{i:06d}" for i in range(10_000)]
    share = sum(held_out(c) for c in ids) / len(ids)

    assert 0.18 < share < 0.22
    for c in ids[:50]:
        assert held_out(c) == (md5(c) % HELD_OUT_EVERY == 0)
