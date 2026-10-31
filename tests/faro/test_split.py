"""
The split holds out about a fifth of customers, by the MD5 of their ID (ADR-0003), and exactly a third of each label's
request families, by the MD5 of theirs (ADR-0005; DML-09).
"""

import hashlib

from faro.split import HELD_OUT_EVERY, held_out, held_out_families


def md5(value: str) -> int:
    return int(hashlib.md5(value.encode(), usedforsecurity=False).hexdigest(), 16)


def test_holds_out_a_fifth_of_customers_by_their_md5() -> None:
    ids = [f"CUS{i:06d}" for i in range(10_000)]
    share = sum(held_out(c) for c in ids) / len(ids)

    assert 0.18 < share < 0.22
    for c in ids[:50]:
        assert held_out(c) == (md5(c) % HELD_OUT_EVERY == 0)


def test_holds_out_the_third_of_a_labels_families_with_the_lowest_md5() -> None:
    ids = [f"card_status-{i:02d}" for i in range(1, 13)]

    chosen = held_out_families(ids)

    assert len(chosen) == 4
    assert chosen == set(sorted(ids, key=md5)[:4])


def test_the_families_held_out_dont_depend_on_their_order_or_repeats() -> None:
    ids = [f"block_card-{i:02d}" for i in range(1, 13)]

    assert held_out_families(ids) == held_out_families([*reversed(ids), ids[0]])


def test_a_group_smaller_than_three_holds_none_out() -> None:
    assert held_out_families(["none-01", "none-02"]) == frozenset()
