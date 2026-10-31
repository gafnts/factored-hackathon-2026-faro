"""
The catalog transcribes the data dictionary; these checks keep it internally consistent.
"""

import pytest

from faro.analysis.catalog import TABLES, Table, parse_columns

CATALOG = {t.name: t for t in TABLES}


def test_parses_types_constraints_references_and_values() -> None:
    (column,) = parse_columns(
        "status VARCHAR(20) NOT NULL REFERENCES statuses IN (Open, In Process)"
    )

    assert column.name == "status"
    assert column.type == "VARCHAR(20)"
    assert column.base_type == "VARCHAR"
    assert column.not_null
    assert column.references == "statuses"
    assert column.values == ("Open", "In Process")


def test_rejects_an_unknown_type() -> None:
    with pytest.raises(ValueError, match="can't parse"):
        parse_columns("amount MONEY")


def test_names_all_thirteen_tables_once() -> None:
    assert len(CATALOG) == len(TABLES) == 13


@pytest.mark.parametrize("t", TABLES, ids=lambda t: t.name)
def test_keys_exist(t: Table) -> None:
    for name in (*t.key, *t.unique):
        assert t.column(name).not_null


@pytest.mark.parametrize("t", TABLES, ids=lambda t: t.name)
def test_references_match_the_referenced_key(t: Table) -> None:
    for column in t.columns:
        if column.references:
            parent = CATALOG[column.references]
            assert len(parent.key) == 1
            assert parent.column(parent.key[0]).type == column.type


@pytest.mark.parametrize("t", [t for t in TABLES if t.daily], ids=lambda t: t.name)
def test_daily_tables_are_dated(t: Table) -> None:
    assert t.event_date is not None
    t.column("process_date")
    if t.event_date.via:
        reference = t.column(t.event_date.via).references
        assert reference is not None
        CATALOG[reference].column(t.event_date.column)
    else:
        t.column(t.event_date.column)


def test_an_unknown_column_is_a_key_error() -> None:
    with pytest.raises(KeyError, match="no column"):
        CATALOG["customers"].column("nickname")
