"""
The candidates are ADR-0003's, written against the organizers' dictionary (PRB-05).
"""

from faro.analysis.candidates import (
    CANDIDATES,
    COMPLAINT_MAPPING,
    DISPUTES,
    IS_DISPUTE,
)
from faro.analysis.catalog import TABLES

CATALOG = {t.name: t for t in TABLES}


def test_every_core_field_is_in_the_dictionary() -> None:
    for candidate in CANDIDATES:
        for scope in candidate.scopes:
            for name in scope.fields:
                CATALOG[scope.table].column(name)
            assert set(scope.only) <= set(scope.fields)


def test_each_candidate_sketches_three_rules() -> None:
    assert [len(c.rules) for c in CANDIDATES] == [3, 3, 3, 3]
    assert len({c.key for c in CANDIDATES}) == len(CANDIDATES)


def test_only_dispute_intake_owns_complaints() -> None:
    assert set(COMPLAINT_MAPPING.values()) == {DISPUTES, None}
    assert IS_DISPUTE == "category in ('Fees', 'Transactions')"
