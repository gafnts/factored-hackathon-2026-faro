"""
The judgment evidence counts what ADR-0003 names, in the 12 months before the as-of instant (PRB-01, PRB-07, EVL-02).
"""

import duckdb

from faro.analysis.candidates import CANDIDATES
from faro.analysis.evidence import (
    ComplaintDemand,
    ReasonBaseline,
    complaint_demand,
    contact_baseline,
    depth,
    gather,
)


def test_counts_the_customers_behind_each_situation(
    staged: duckdb.DuckDBPyConnection,
) -> None:
    counts = {
        c.key: [(s.situation, s.customers) for s in depth(staged, c).coverage]
        for c in CANDIDATES
    }

    assert counts == {
        # C2's savings account; no failed payments, and nothing missing in the rows read.
        "account_inquiries": [
            ("SCP-03", 1),
            ("SCP-04", 1),
            ("SCP-05", 0),
            ("EVL-02", 0),
        ],
        # T2 is declined with a listed code and marked fraud; P2 lacks an expiration date.
        "card_support": [("SCP-03", 1), ("SCP-04", 0), ("SCP-05", 1), ("EVL-02", 1)],
        # T2 and T4 aren't approved purchases; K2 has no claimed amount.
        "disputes": [("SCP-03", 1), ("SCP-04", 2), ("SCP-05", 0), ("EVL-02", 1)],
        # C2 has no credit score, and P4 no credit limit.
        "credit": [("SCP-03", 1), ("SCP-04", 0), ("SCP-05", 1), ("EVL-02", 1)],
    }


def test_attributes_complaints_by_the_adrs_mapping(
    staged: duckdb.DuckDBPyConnection,
) -> None:
    assert complaint_demand(staged) == (
        ComplaintDemand("Fees", None, "disputes", 1),
        ComplaintDemand("Transactions", "Cargo no reconocido", "disputes", 1),
    )


def test_baselines_each_reason_on_the_last_twelve_months(
    staged: duckdb.DuckDBPyConnection,
) -> None:
    assert contact_baseline(staged) == (
        ReasonBaseline("Producto", 1, 0, None, None, 1, 0, 1, 1, 1.0),
        ReasonBaseline("Queja", 1, 1, 300.0, 300.0, 1, 1, 0, 1, 4.0),
    )


def test_reads_the_range_csat_scores_take(staged: duckdb.DuckDBPyConnection) -> None:
    evidence = gather(staged, CANDIDATES)

    assert evidence.csat_range == (1, 4)
    assert [d.covered for d in evidence.depth] == [0, 0, 0, 0]
