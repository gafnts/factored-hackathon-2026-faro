"""
The business clock (ADR-0003's rule, ADR-0006 Freshness): one row with the business date and the as-of instant, read
from how the seven daily tables' rows arrived, by the rule the profile shares (faro.clock).
"""

from datetime import date, time
from typing import Any

from faro import clock

# The date each daily table's rows are dated by; transcripts take their interaction's.
EVENT_DATES = {
    "transactions": "transaction_date",
    "call_center_interactions": "interaction_date",
    "satisfaction_surveys": "survey_date",
    "complaints": "creation_date",
    "digital_events": "event_date",
    "campaign_sends": "send_date",
}


def _arrival(
    session: Any, events: str, last_partition: date
) -> tuple[date | None, time | None]:
    settled = dict(
        session.sql(
            f"select process_date - happened::date, count(*) from {events} e "
            "where process_date is not null and happened is not null "
            f"and happened::date <= date '{clock.settled_before(last_partition)}' group by 1"
        ).fetchall()
    )
    (cutoff,) = session.sql(
        "select max(happened::time) filter (where happened::date - process_date = 1) "
        f"from {events} e"
    ).fetchone()
    return clock.last_complete_day(last_partition, clock.lag_p99(settled)), cutoff


def model(dbt: Any, session: Any) -> Any:
    dbt.config(materialized="table")
    relations = {
        "transactions": dbt.ref("bronze_transactions"),
        "call_center_interactions": dbt.ref("bronze_call_center_interactions"),
        "call_transcripts": dbt.ref("bronze_call_transcripts"),
        "satisfaction_surveys": dbt.ref("bronze_satisfaction_surveys"),
        "complaints": dbt.ref("bronze_complaints"),
        "digital_events": dbt.ref("bronze_digital_events"),
        "campaign_sends": dbt.ref("bronze_campaign_sends"),
    }
    lock_files = dbt.source("checks", "lock_files").sql_query()
    tables = {name: f"({r.sql_query()})" for name, r in relations.items()}
    events = {
        name: f"(select process_date, {column} as happened from {tables[name]} t)"
        for name, column in EVENT_DATES.items()
    }
    events["call_transcripts"] = (
        f"(select t.process_date, i.happened from {tables['call_transcripts']} t left join "
        "(select interaction_id, min(interaction_date) as happened "
        f"from {tables['call_center_interactions']} c group by 1) i using (interaction_id))"
    )

    days, cutoffs = [], []
    for name in relations:
        (last_partition,) = session.sql(
            "select max(strptime(regexp_extract(key, '_(\\d{8})\\.csv$', 1), '%Y%m%d')::date) "
            f"from ({lock_files}) l where key like '{name}/%'"
        ).fetchone()
        day, cutoff = _arrival(session, events[name], last_partition)
        days.append(day)
        cutoffs.append(cutoff)
    business_date = clock.business_date(days)
    if business_date is None:
        raise ValueError("no daily table has settled rows to read a business date from")
    as_of = clock.as_of(business_date, cutoffs)
    return session.sql(
        f"select date '{business_date.isoformat()}' as business_date, "
        f"timestamp '{as_of.isoformat(sep=' ')}' as as_of"
    )
