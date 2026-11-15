"""
The guest entry's customers come from development customers only, four per country and number of cards by a fixed
rule, each able to play the opening's three suggestions, and reach the stack as IDs and synthetic names alone
(ADR-0007, The guest entry; DML-09, SEC-03, SCP-03 to SCP-05).
"""

from typing import Any

import duckdb
import pytest

from faro.guest.customers import Customer, decode
from faro.guest_list import COUNTRIES, GuestList, GuestListError, select, write
from faro.split import held_out

from .conftest import AS_OF, BUSINESS_DATE


def names(guest_list: GuestList) -> list[str]:
    return [c.name for c in guest_list.customers]


def test_each_cell_gets_its_four_busiest_playable_customers(
    guest_con: duckdb.DuckDBPyConnection,
) -> None:
    guests = select(guest_con, "snap", BUSINESS_DATE, AS_OF)

    # The fifth of each cell ties the fourth and has the higher ID, and México's outsiders, each busier than any
    # cell's first, fail one clause each: held out, a purchase older than a week, no limit, only declines, or a row
    # updated after the as-of instant.
    assert names(guests) == [
        f"Guest {code}{cards} {rank}"
        for code in ("MX", "CO", "AR")
        for cards in ("1", "2")
        for rank in range(4)
    ]
    assert guests.qualifying == {
        f"{country}, {cards}": 5
        for country in COUNTRIES
        for cards in ("one card", "two or more")
    }
    assert not [c for c in guests.customers if held_out(c.customer_id)]


def test_no_held_out_customer_is_read_past_its_id(
    guest_con: duckdb.DuckDBPyConnection,
) -> None:
    select(guest_con, "snap", BUSINESS_DATE, AS_OF)

    for table in ("persona_customers", "persona_cards", "persona_transactions"):
        ids = guest_con.execute(f"select distinct customer_id from {table}").fetchall()
        assert ids
        assert not [i for (i,) in ids if held_out(i)], table


def test_a_cell_short_of_customers_is_an_error(
    guest_con: duckdb.DuckDBPyConnection,
) -> None:
    with pytest.raises(
        GuestListError,
        match="5 development customers .* México, one card, fewer than 6",
    ):
        select(guest_con, "snap", BUSINESS_DATE, AS_OF, per_cell=6)


class Parameters:
    def __init__(self) -> None:
        self.puts: list[dict[str, Any]] = []

    def put_parameter(self, **request: Any) -> None:
        self.puts.append(request)


def test_the_list_is_written_under_the_stacks_prefix() -> None:
    guests = GuestList(
        "snap",
        (Customer("CLI-TEAM00000001", "Team Customer 01"),),
        {},
    )
    ssm = Parameters()

    name = write(ssm, "banking-agent-local", guests)

    assert name == "/banking-agent-local/guest-entry/customers"
    (put,) = ssm.puts
    assert (put["Name"], put["Type"], put["Tier"], put["Overwrite"]) == (
        name,
        "String",
        "Standard",
        True,
    )
    assert decode(put["Value"]) == guests.customers
