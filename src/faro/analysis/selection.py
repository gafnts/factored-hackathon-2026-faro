"""
The gates of docs/adr/0003-choose-workflow-from-evidence.md, computed on the tables as they stood at the as-of instant.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import duckdb

from faro.analysis.candidates import CANDIDATES, IS_CARD, Candidate, Scope
from faro.analysis.catalog import Column, Table
from faro.analysis.evidence import Evidence, gather
from faro.analysis.learned import (
    ScoreBand,
    Signal,
    fraud_by_score,
    measure,
    top_legitimate_score,
)
from faro.analysis.source import AnalysisError, connect, one, table_keys
from faro.dataset.lock import Lock

FIELD_SHARE = 0.90
STATE_CUSTOMERS = 100
RULES = 3
# ADR-0003's revisit: no candidate can pass E2 on this snapshot, so its results are evidence only.
SET_ASIDE = frozenset({"e2"})

STAGED = frozenset(
    {
        "customers",
        "products",
        "transactions",
        "complaints",
        "call_center_interactions",
        "satisfaction_surveys",
    }
)

Log = Callable[[str], None]

# Each table keeps only rows dated by the as-of instant, with the columns the gates and the card support
# analysis read, typed.
_STAGING = {
    "customers": """
        select customer_id, strip_accents(country) as country, segment, customer_status,
          try_cast(credit_score as integer) as credit_score,
          try_cast(estimated_monthly_income as decimal(12,2)) as estimated_monthly_income,
          try_cast(date_of_birth as date) as date_of_birth,
          try_cast(registration_date as timestamp) as registration_date
        from raw_customers
        where try_cast(registration_date as timestamp) <= getvariable('as_of')
    """,
    "products": """
        select product_id, customer_id, product_type, product_number, product_status, currency,
          try_cast(current_balance as decimal(15,2)) as current_balance,
          try_cast(credit_limit as decimal(15,2)) as credit_limit,
          try_cast(interest_rate as decimal(5,2)) as interest_rate,
          try_cast(expiration_date as date) as expiration_date,
          try_cast(days_past_due as integer) as days_past_due,
          try_cast(opening_date as date) as opening_date,
          try_cast(last_transaction_date as timestamp) as last_transaction_date,
          try_cast(last_updated as timestamp) as last_updated
        from raw_products
        where try_cast(opening_date as date) <= getvariable('as_of')
    """,
    "transactions": """
        select t.transaction_id, t.customer_id, t.product_id, p.product_type,
          try_cast(t.transaction_date as timestamp) as transaction_date,
          t.transaction_type, t.transaction_category,
          try_cast(t.amount as decimal(15,2)) as amount,
          try_cast(t.amount_usd as decimal(15,2)) as amount_usd, t.currency, t.channel,
          t.merchant_name, t.merchant_category,
          strip_accents(t.transaction_country) as transaction_country,
          t.transaction_status, t.response_code,
          try_cast(t.is_fraud as boolean) as is_fraud,
          try_cast(t.fraud_score as decimal(5,2)) as fraud_score
        from raw_transactions t left join products p on p.product_id = t.product_id
        where try_cast(t.transaction_date as timestamp) <= getvariable('as_of')
    """,
    "complaints": """
        select complaint_id, customer_id, category, subcategory, case_type, status,
          affected_product_id,
          try_cast(creation_date as timestamp) as creation_date,
          try_cast(claimed_amount as decimal(15,2)) as claimed_amount
        from raw_complaints
        where try_cast(creation_date as timestamp) <= getvariable('as_of')
    """,
    "interactions": """
        select interaction_id, customer_id, reason_category,
          try_cast(interaction_date as timestamp) as interaction_date,
          try_cast(duration_seconds as integer) as duration_seconds,
          try_cast(was_resolved as boolean) as was_resolved,
          try_cast(was_escalated as boolean) as was_escalated
        from raw_call_center_interactions
        where try_cast(interaction_date as timestamp) <= getvariable('as_of')
    """,
    "surveys": """
        select interaction_id, survey_type, try_cast(main_score as integer) as main_score
        from raw_satisfaction_surveys
        where try_cast(survey_date as timestamp) <= getvariable('as_of')
    """,
}


@dataclass(frozen=True)
class FieldPopulation:
    table: str
    name: str
    rows: int
    populated: int

    @property
    def share(self) -> float | None:
        return self.populated / self.rows if self.rows else None

    @property
    def passes(self) -> bool:
        return self.share is not None and self.share >= FIELD_SHARE


@dataclass(frozen=True)
class RuleCheck:
    rules: tuple[str, ...]
    fields: int
    # Names the rules read or follow that the dictionary doesn't have.
    unknown_fields: tuple[str, ...]
    unknown_references: tuple[str, ...]

    @property
    def passes(self) -> bool:
        return (
            len(self.rules) == RULES
            and not self.unknown_fields
            and not self.unknown_references
        )


@dataclass(frozen=True)
class CandidateGates:
    key: str
    name: str
    normal_path: str
    fields: tuple[FieldPopulation, ...]
    customers_in_state: int
    rules: RuleCheck
    # None when the dictionary holds no label for the candidate.
    signal: Signal | None

    @property
    def f1(self) -> bool:
        return all(f.passes for f in self.fields)

    @property
    def f2(self) -> bool:
        return self.customers_in_state >= STATE_CUSTOMERS

    @property
    def e1(self) -> bool:
        return self.rules.passes

    @property
    def e2(self) -> bool:
        return self.signal is not None and self.signal.passes

    @property
    def verdicts(self) -> dict[str, bool]:
        return {"f1": self.f1, "f2": self.f2, "e1": self.e1, "e2": self.e2}

    @property
    def passes(self) -> bool:
        return all(v for gate, v in self.verdicts.items() if gate not in SET_ASIDE)


@dataclass(frozen=True)
class Selection:
    snapshot_id: str
    duckdb_version: str
    business_date: date
    as_of: datetime
    candidates: tuple[CandidateGates, ...]
    # Tables some complaint column references; ADR-0003 notes that none is transactions.
    complaints_reference: tuple[str, ...]
    # How is_fraud on card transactions varies with the bank's fraud_score, the field E2 leaves out.
    fraud_by_score: tuple[ScoreBand, ...]
    # The highest fraud_score on a legitimate card transaction: every one scored above it is fraud.
    top_legitimate_score: float | None
    evidence: Evidence


def define_window(con: duckdb.DuckDBPyConnection, as_of: datetime) -> None:
    con.execute(f"set variable as_of = timestamp '{as_of.isoformat(sep=' ')}'")
    con.execute(
        "create or replace temp macro within(ts, days) as "
        "ts > getvariable('as_of') - to_days(days)"
    )


def stage(con: duckdb.DuckDBPyConnection, as_of: datetime) -> None:
    define_window(con, as_of)
    for name, sql in _STAGING.items():
        con.execute(f"create or replace temp table {name} as {sql}")


def field_populations(
    con: duckdb.DuckDBPyConnection, scope: Scope
) -> tuple[FieldPopulation, ...]:
    aggregates = []
    for name in scope.fields:
        rows = scope.only.get(name, "true")
        aggregates += [
            f"count(*) filter (where {rows})",
            f"count({name}) filter (where {rows})",
        ]
    row = one(
        con, f"select {', '.join(aggregates)} from {scope.table} where {scope.rows}"
    )
    return tuple(
        FieldPopulation(scope.table, name, row[2 * i], row[2 * i + 1])
        for i, name in enumerate(scope.fields)
    )


def customers_in_state(con: duckdb.DuckDBPyConnection, candidate: Candidate) -> int:
    n: int = one(con, f"select count(distinct customer_id) from ({candidate.state})")[0]
    return n


def check_rules(candidate: Candidate, catalog: Mapping[str, Table]) -> RuleCheck:
    reads = sorted({r for rule in candidate.rules for r in rule.reads})
    follows = sorted({f for rule in candidate.rules for f in rule.follows})
    return RuleCheck(
        rules=tuple(rule.name for rule in candidate.rules),
        fields=len(reads),
        unknown_fields=tuple(r for r in reads if _column(catalog, r) is None),
        unknown_references=tuple(
            f
            for f in follows
            if (c := _column(catalog, f)) is None or c.references is None
        ),
    )


def _column(catalog: Mapping[str, Table], name: str) -> Column | None:
    table, _, column = name.partition(".")
    try:
        return catalog[table].column(column)
    except KeyError:
        return None


def _signal(
    con: duckdb.DuckDBPyConnection, candidate: Candidate, business_date: date, log: Log
) -> Signal | None:
    if candidate.label is None:
        return None
    log(f"Fitting the E2 model for {candidate.name.lower()}")
    return measure(con, candidate.label, business_date)


def select(
    lock: Lock,
    root: Path,
    tables: Sequence[Table],
    business_date: date,
    as_of: datetime,
    candidates: Sequence[Candidate] = CANDIDATES,
    log: Log = print,
) -> Selection:
    catalog = {t.name: t for t in tables}
    missing = sorted(STAGED - catalog.keys())
    if missing:
        raise AnalysisError(f"the selection needs {', '.join(missing)}")
    raw = [catalog[name] for name in sorted(STAGED)]
    con = connect(root, raw, {t.name: table_keys(lock, t) for t in raw})
    try:
        log(f"Staging the tables as of {as_of}")
        stage(con, as_of)
        bands = fraud_by_score(con, IS_CARD)
        top = top_legitimate_score(con, IS_CARD)
        results = []
        for c in candidates:
            log(f"Computing the gates for {c.name.lower()}")
            results.append(
                CandidateGates(
                    key=c.key,
                    name=c.name,
                    normal_path=c.normal_path,
                    fields=tuple(
                        f for scope in c.scopes for f in field_populations(con, scope)
                    ),
                    customers_in_state=customers_in_state(con, c),
                    rules=check_rules(c, catalog),
                    signal=_signal(con, c, business_date, log),
                )
            )
        log("Gathering the evidence for the judgment")
        evidence = gather(con, tuple(candidates))
    finally:
        con.close()
    return Selection(
        snapshot_id=lock.snapshot_id,
        duckdb_version=duckdb.__version__,
        business_date=business_date,
        as_of=as_of,
        candidates=tuple(results),
        complaints_reference=tuple(
            sorted(
                {c.references for c in catalog["complaints"].columns if c.references}
            )
        ),
        fraud_by_score=bands,
        top_legitimate_score=top,
        evidence=evidence,
    )
