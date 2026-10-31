"""
The evidence ADR-0003 rests on: whether contacts can be traced to a workflow, and where processing days end.
"""

import duckdb
import pytest

from faro.analysis.attribution import contact_attribution, cutoffs
from faro.analysis.catalog import EventDate, table

VISITS = table(
    "visits",
    """
    visit_id VARCHAR(5) NOT NULL
    visited_at TIMESTAMP NOT NULL
    process_date DATE NOT NULL
    customer_id VARCHAR(5) NOT NULL REFERENCES customers
    """,
    key=("visit_id",),
    event_date=EventDate("visited_at"),
    daily=True,
)


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(
        """
        create view raw_call_center_interactions as select * from (values
          ('I1', 'C1', 'Queja', 'Queja', 'P1,P9'),
          ('I2', 'C2', 'Queja', 'Queja', null),
          ('I3', 'C1', 'Técnico', 'Técnico', 'P2'),
          ('I4', 'C2', 'Técnico', 'Producto', null)
        ) t(interaction_id, customer_id, contact_reason, reason_category, mentioned_products)
        """
    )
    con.execute(
        """
        create view raw_call_transcripts as select * from (values
          ('I1', 'saldo', 'consulta_general'),
          ('I3', 'saldo', 'consulta_general'),
          ('I2', 'bloqueo', null)
        ) t(interaction_id, customer_text, detected_intents)
        """
    )
    con.execute(
        "create view raw_products as select * from (values ('P1', 'C1'), ('P2', 'C2')) "
        "t(product_id, customer_id)"
    )
    con.execute(
        "create view raw_complaints as select * from (values (null), ('I1')) "
        "t(origin_interaction_id)"
    )
    con.execute(
        "create view raw_customers as select * from (values ('C1', 'Mexico'), ('C2', 'Argentina')) "
        "t(customer_id, country)"
    )
    con.execute(
        """
        create view raw_visits as select * from (values
          ('V1', '2026-06-16 08:00:00', '2026-06-16', 'C1'),
          ('V2', '2026-06-17 07:59:00', '2026-06-16', 'C1'),
          ('V3', '2026-06-16 09:30:00', '2026-06-16', 'C2')
        ) t(visit_id, visited_at, process_date, customer_id)
        """
    )
    return con


def test_measures_how_far_contacts_can_be_traced(
    con: duckdb.DuckDBPyConnection,
) -> None:
    a = contact_attribution(con)

    assert (a.contacts, a.reasons, a.reasons_matching_category) == (4, 2, 3)
    assert a.intents == ("consulta_general",)
    assert (a.transcripts, a.customer_texts, a.texts_under_every_reason) == (3, 2, 1)
    assert (a.mentions, a.mentions_found, a.mentions_owned) == (3, 2, 1)
    assert (a.complaints, a.complaints_linked) == (2, 1)


def test_finds_each_countrys_cutoff(con: duckdb.DuckDBPyConnection) -> None:
    by_country = {c.country: c for c in cutoffs(con, (VISITS,))}

    mexico, argentina = by_country["Mexico"], by_country["Argentina"]
    assert mexico.rows == 2
    assert (mexico.same_day_from, mexico.next_day_until) == ("08:00:00", "07:59:00")
    assert (argentina.same_day_from, argentina.next_day_until) == ("09:30:00", None)


def test_skips_tables_without_customers_or_own_dates(
    con: duckdb.DuckDBPyConnection,
) -> None:
    undated = table("notes", "note_id VARCHAR(5) NOT NULL", key=("note_id",))

    assert cutoffs(con, (undated,)) == ()
