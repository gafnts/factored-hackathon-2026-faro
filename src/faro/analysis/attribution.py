"""
Whether a contact can be traced to a workflow, and where each processing day ends (docs/adr/0003-choose-workflow-from-evidence.md).
"""

from collections.abc import Sequence
from dataclasses import dataclass

import duckdb

from faro.analysis.catalog import Table
from faro.analysis.source import one, quote

TABLES = frozenset(
    {"call_center_interactions", "call_transcripts", "products", "complaints"}
)
LISTED_INTENTS = 20


@dataclass(frozen=True)
class Attribution:
    contacts: int
    reasons: int
    reasons_matching_category: int
    intents: tuple[str, ...]
    transcripts: int
    customer_texts: int
    texts_under_every_reason: int
    mentions: int
    mentions_found: int
    mentions_owned: int
    complaints: int
    complaints_linked: int


@dataclass(frozen=True)
class Cutoff:
    table: str
    country: str
    rows: int
    # Earliest time of day among events dated on their process date, latest among those dated the day after.
    same_day_from: str | None
    next_day_until: str | None


def contact_attribution(con: duckdb.DuckDBPyConnection) -> Attribution:
    contacts, reasons, matching = one(
        con,
        "select count(*), count(distinct contact_reason), "
        "count(*) filter (where contact_reason = reason_category) "
        "from raw_call_center_interactions",
    )
    intents = tuple(
        value
        for (value,) in con.execute(
            "select distinct detected_intents from raw_call_transcripts "
            f"where detected_intents is not null order by 1 limit {LISTED_INTENTS}"
        ).fetchall()
    )
    transcripts, texts, everywhere = one(
        con,
        """
        with texts as (
          select t.customer_text, count(distinct i.contact_reason) as reasons
          from raw_call_transcripts t
          join raw_call_center_interactions i on i.interaction_id = t.interaction_id
          group by 1
        )
        select
          (select count(*) from raw_call_transcripts),
          count(*),
          count(*) filter (where reasons = ?)
        from texts
        """,
        [reasons],
    )
    mentions, found, owned = one(
        con,
        """
        with mentioned as (
          select customer_id, trim(unnest(string_split(mentioned_products, ','))) as product_id
          from raw_call_center_interactions
          where mentioned_products is not null
        )
        select count(*), count(p.product_id), count(*) filter (where p.customer_id = m.customer_id)
        from mentioned m left join raw_products p on p.product_id = m.product_id
        """,
    )
    complaints, linked = one(
        con, "select count(*), count(origin_interaction_id) from raw_complaints"
    )
    return Attribution(
        contacts=contacts,
        reasons=reasons,
        reasons_matching_category=matching,
        intents=intents,
        transcripts=transcripts,
        customer_texts=texts,
        texts_under_every_reason=everywhere,
        mentions=mentions,
        mentions_found=found,
        mentions_owned=owned,
        complaints=complaints,
        complaints_linked=linked,
    )


def cutoffs(
    con: duckdb.DuckDBPyConnection, tables: Sequence[Table]
) -> tuple[Cutoff, ...]:
    results = []
    for t in tables:
        if not t.daily or t.event_date is None or t.event_date.via:
            continue
        if not any(
            c.name == "customer_id" and c.references == "customers" for c in t.columns
        ):
            continue
        at = f"try_cast(x.{quote(t.event_date.column)} as timestamp)"
        processed = "try_cast(x.process_date as date)"
        for country, rows, same_day_from, next_day_until in con.execute(
            f"""
            select c.country, count(*),
              min({at}::time) filter (where {at}::date = {processed})::varchar,
              max({at}::time) filter (where {at}::date = {processed} + 1)::varchar
            from {quote("raw_" + t.name)} x
            join raw_customers c on c.customer_id = x.customer_id
            group by 1 order by 1
            """
        ).fetchall():
            results.append(Cutoff(t.name, country, rows, same_day_from, next_day_until))
    return tuple(results)
