"""
A blind sheet's draw splits its total over the strata in proportion, one each while the total allows.
"""

import pytest

from faro.evaluation import blind


@pytest.mark.parametrize(
    ("sizes", "total", "expected"),
    [
        ({"a": 50, "b": 30, "c": 20}, 10, {"a": 5, "b": 3, "c": 2}),
        ({"a": 97, "b": 2, "c": 1}, 10, {"a": 8, "b": 1, "c": 1}),
        ({"a": 3, "b": 2}, 10, {"a": 3, "b": 2}),
        ({"a": 5, "b": 5, "c": 5}, 2, {"a": 1, "b": 1}),
        ({"a": 0, "b": 4}, 2, {"b": 2}),
    ],
)
def test_allocate_splits_in_proportion_with_one_each_while_it_can(
    sizes: dict[str, int], total: int, expected: dict[str, int]
) -> None:
    given = blind.allocate(sizes, total)

    assert sum(given.values()) == min(total, sum(sizes.values()))
    if total >= len([v for v in sizes.values() if v]):
        assert given == expected
    else:
        assert all(given[k] <= sizes[k] for k in given)
