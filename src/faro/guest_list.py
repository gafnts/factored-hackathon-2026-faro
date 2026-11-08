"""
The guest entry's customers (ADR-0007, The guest entry, and its amendment of 2026-11-08): 24 development customers
chosen by a fixed rule from the pinned snapshot, each of whom can play the opening's three suggestions, a card's
status, a block, and a purchase they don't recognize. Each passes the personas' filter (active, nothing updated after
the as-of instant, no conflicting record) and holds an active credit card with a recorded limit and an approved
purchase in the last week. Four come from each of six cells, a country by one active card or two or more, since a
block on a customer with several asks which (POL-14); within a cell, the most card transactions in the 90-day window
come first, then the lowest customer_id. Only development customers are read (DML-09).

make guest-list writes each chosen customer's ID and synthetic name to the stack's Parameter Store entry, which the
guest entry reads. The repository holds the rule and never an ID or a name, and the script prints counts alone
(SEC-03).
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import duckdb

from faro import personas
from faro.guest.customers import Customer, encode, parameter

COUNTRIES = ("México", "Colombia", "Argentina")
CARDS = {"one card": "= 1", "two or more": ">= 2"}
PER_CELL = 4

PLAYABLE = """
    exists (select 1 from persona_cards k where k.customer_id = c.customer_id
      and k.product_type = 'Tarjeta Crédito' and k.product_status = 'Active'
      and k.credit_limit is not null
      and exists (select 1 from persona_transactions t where t.product_id = k.product_id
        and t.transaction_type = 'Purchase' and t.transaction_status = 'Approved'
        and t.transaction_date > $recent))
"""
ACTIVE_CARDS = """
    (select count(*) from persona_cards k where k.customer_id = c.customer_id and k.product_status = 'Active')
"""


class GuestListError(Exception):
    pass


@dataclass(frozen=True)
class GuestList:
    snapshot: str
    customers: tuple[Customer, ...]
    # How many development customers met the rule in each cell; counts, never IDs, are what gets printed.
    qualifying: dict[str, int]


def select(
    con: duckdb.DuckDBPyConnection,
    snapshot: str,
    business_date: date,
    as_of: datetime,
    per_cell: int = PER_CELL,
) -> GuestList:
    personas.tables(con, as_of)
    values = personas.parameters(business_date, as_of)
    chosen: list[Customer] = []
    qualifying: dict[str, int] = {}
    for country in COUNTRIES:
        for cards, count in CARDS.items():
            query = (
                "select c.customer_id, c.first_name, c.last_name from persona_customers c "
                f"where {personas.CLEAN} and {PLAYABLE} and c.country = $country "
                f"and {ACTIVE_CARDS} {count} "
                "order by (select count(*) from persona_transactions t where t.customer_id = c.customer_id) desc, "
                "customer_id"
            )
            rows = con.execute(
                query, personas.bound(query, {**values, "country": country})
            ).fetchall()
            cell = f"{country}, {cards}"
            qualifying[cell] = len(rows)
            if len(rows) < per_cell:
                raise GuestListError(
                    f"{len(rows)} development customers meet the guest list's rule in {cell}, "
                    f"fewer than {per_cell}"
                )
            chosen.extend(
                Customer(customer_id, f"{first} {last}")
                for customer_id, first, last in rows[:per_cell]
            )
    return GuestList(snapshot, tuple(chosen), qualifying)


def write(ssm: Any, prefix: str, guest_list: GuestList) -> str:
    name = parameter(prefix)
    ssm.put_parameter(
        Name=name,
        Value=encode(guest_list.snapshot, guest_list.customers),
        Type="String",
        Tier="Standard",
        Overwrite=True,
        Description="The guest entry's curated customers, written by make guest-list (ADR-0007)",
    )
    return name
