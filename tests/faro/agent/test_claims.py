"""
A record's source follows the token's labels and fails closed: a customer we invited is labeled so, and any other
counts as a visitor, the same in the Runtime and in file_handoff, whose zip carries none of the agent (ADR-0004,
Operations, access controls, and its amendment of 2026-11-07; EVL-13).
"""

import pytest

from faro.agent import claims
from faro.tools import identity

TABLE = [
    (["customer", "invited"], "demo"),
    (["customer", "evaluation"], "evaluation"),
    (["customer", "invited", "evaluation"], "evaluation"),
    (["customer", "visitor"], "visitor"),
    (["customer"], "visitor"),
    ([], "visitor"),
]


@pytest.mark.parametrize(("groups", "source"), TABLE)
def test_the_runtime_and_the_tools_read_one_source_from_the_labels(
    groups: list[str], source: str
) -> None:
    assert claims.source_of(groups) == source
    assert identity.source_of(groups) == source


def test_groups_that_arent_a_list_count_as_a_visitors() -> None:
    assert claims.source_of("customer invited") == "visitor"
    assert claims.source_of(None) == "visitor"
