"""
The evidence for ADR-0003's written judgment: evaluation depth, attributable demand, and the contact-center baseline (PRB-07).
"""

from dataclasses import dataclass

import duckdb

from faro.analysis.candidates import COMPLAINT_MAPPING, Candidate, Scope
from faro.analysis.source import one

COVERED = 100
WINDOW = "interval 1 year"

_CATEGORIES = {category: c for (category, _), c in COMPLAINT_MAPPING.items()}


@dataclass(frozen=True)
class Coverage:
    situation: str
    description: str
    customers: int

    @property
    def covered(self) -> bool:
        return self.customers >= COVERED


@dataclass(frozen=True)
class Depth:
    key: str
    name: str
    coverage: tuple[Coverage, ...]

    @property
    def covered(self) -> int:
        return sum(c.covered for c in self.coverage)


@dataclass(frozen=True)
class ComplaintDemand:
    category: str
    subcategory: str | None
    # The candidate key ADR-0003's mapping assigns, or None for out of scope.
    candidate: str | None
    complaints: int


@dataclass(frozen=True)
class ReasonBaseline:
    reason: str
    contacts: int
    timed: int
    median_seconds: float | None
    p90_seconds: float | None
    resolution_known: int
    resolved: int
    escalated: int
    csat_responses: int
    csat_mean: float | None


@dataclass(frozen=True)
class Evidence:
    depth: tuple[Depth, ...]
    complaints: tuple[ComplaintDemand, ...]
    contacts: tuple[ReasonBaseline, ...]
    # The lowest and highest CSAT main_score in the snapshot, against the documented 1 to 5.
    csat_range: tuple[int, int] | None


def _missing_data(scope: Scope) -> str:
    missing = " or ".join(
        f"({scope.only.get(name, 'true')} and {name} is null)" for name in scope.fields
    )
    return f"select customer_id from {scope.table} where {scope.rows} and ({missing})"


def _customers(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    n: int = one(con, f"select count(distinct customer_id) from ({sql})")[0]
    return n


def depth(con: duckdb.DuckDBPyConnection, candidate: Candidate) -> Depth:
    situations = (
        ("SCP-03", f"Normal path: {candidate.normal_path.lower()}", candidate.state),
        ("SCP-04", candidate.unsupported.description, candidate.unsupported.customers),
        ("SCP-05", candidate.handoff.description, candidate.handoff.customers),
        (
            "EVL-02",
            "Incorrect or missing data: a core field missing in the rows the tools read",
            " union ".join(_missing_data(s) for s in candidate.scopes),
        ),
    )
    return Depth(
        key=candidate.key,
        name=candidate.name,
        coverage=tuple(
            Coverage(situation, description, _customers(con, sql))
            for situation, description, sql in situations
        ),
    )


def complaint_demand(con: duckdb.DuckDBPyConnection) -> tuple[ComplaintDemand, ...]:
    return tuple(
        ComplaintDemand(category, subcategory, _CATEGORIES.get(category), n)
        for category, subcategory, n in con.execute(
            "select category, subcategory, count(*) from complaints "
            f"where creation_date > getvariable('as_of') - {WINDOW} "
            "group by all order by 1, 2 nulls last"
        ).fetchall()
    )


def contact_baseline(con: duckdb.DuckDBPyConnection) -> tuple[ReasonBaseline, ...]:
    rows = con.execute(
        f"""
        with contacts as (
          select * from interactions
          where interaction_date > getvariable('as_of') - {WINDOW}
        ), csat as (
          select c.reason_category, count(*) as responses, avg(s.main_score) as mean
          from surveys s join contacts c on c.interaction_id = s.interaction_id
          where s.survey_type = 'CSAT'
          group by 1
        )
        select c.reason_category, count(*), count(c.duration_seconds),
          median(c.duration_seconds), quantile_cont(c.duration_seconds, 0.9),
          count(c.was_resolved), count(*) filter (where c.was_resolved),
          count(*) filter (where c.was_escalated),
          coalesce(any_value(q.responses), 0), any_value(q.mean)
        from contacts c left join csat q on q.reason_category = c.reason_category
        group by 1 order by 1
        """
    ).fetchall()
    return tuple(
        ReasonBaseline(
            reason=reason,
            contacts=contacts,
            timed=timed,
            median_seconds=None if median is None else float(median),
            p90_seconds=None if p90 is None else float(p90),
            resolution_known=known,
            resolved=resolved,
            escalated=escalated,
            csat_responses=responses,
            csat_mean=None if mean is None else float(mean),
        )
        for reason, contacts, timed, median, p90, known, resolved, escalated, responses, mean in rows
    )


def gather(
    con: duckdb.DuckDBPyConnection, candidates: tuple[Candidate, ...]
) -> Evidence:
    low, high = one(
        con,
        "select min(main_score), max(main_score) from surveys where survey_type = 'CSAT'",
    )
    return Evidence(
        depth=tuple(depth(con, c) for c in candidates),
        complaints=complaint_demand(con),
        contacts=contact_baseline(con),
        csat_range=None if low is None else (low, high),
    )
