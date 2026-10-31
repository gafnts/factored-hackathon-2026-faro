"""
The tiny export holds what gold would for the personas: their customers, cards, and the 90-day window's card
transactions, with gold's rules applied and nothing the tools mustn't read (ADR-0006; POL-11, POL-20, POL-30, POL-40;
DML-01, DML-02).
"""

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest

from faro.export.items import check
from faro.export.tiny import (
    TinyExportError,
    build,
    clock_from_profile,
    partitions_needed,
    pipeline_version,
)

from ..conftest import AS_OF, BUSINESS_DATE, PERSONA_FILES

STAMP = {"snapshot": "b3b8b248f604ef9a", "pipeline_version": "0123456789abcdef"}


def built(con: duckdb.DuckDBPyConnection) -> dict[str, dict[str, Any]]:
    items = build(
        con,
        ["CLI-TEAM00000003", "CLI-TEAM00000005"],
        stamp=STAMP,
        business_date=BUSINESS_DATE,
        as_of=AS_OF,
    )
    return {f"{item['pk']}|{item['sk']}": item for item in items}


def test_it_holds_the_personas_cards_and_their_window(
    persona_con: duckdb.DuckDBPyConnection,
) -> None:
    items = built(persona_con)

    check(list(items.values()), STAMP)
    kinds = sorted(item["kind"] for item in items.values())
    assert kinds.count("customer") == 2
    # 33 opens after the as-of instant, and 34 isn't a card.
    assert {i["card_id"] for i in items.values() if i["kind"] == "card"} == {
        "PRD-TEAM00000031",
        "PRD-TEAM00000032",
        "PRD-TEAM00000051",
        "PRD-TEAM00000052",
    }
    # 301 is before the window, 305 after the as-of instant, and 306 on the savings account.
    assert {
        i["transaction_id"][-3:] for i in items.values() if i["kind"] == "transaction"
    } == {
        "302",
        "303",
        "304",
        "501",
        "502",
        "503",
    }
    assert items["META|META"]["stamp"] == STAMP
    assert items["META|META"]["clock"] == {
        "business_date": "2026-06-17",
        "as_of": "2026-06-18 06:00:00",
    }


def test_cards_carry_their_last_four_digits_and_values_as_delivered(
    persona_con: duckdb.DuckDBPyConnection,
) -> None:
    items = built(persona_con)

    card = items["CLI-TEAM00000003|CARD#PRD-TEAM00000031"]
    assert card["last_four"] == "4821"
    assert card["current_balance"] == Decimal("1240.55")
    assert card["credit_limit"] == Decimal("2000.00")
    assert (card["opening_date"], card["expiration_date"]) == (
        "2023-02-14",
        "2029-02-13",
    )
    no_limit = items["CLI-TEAM00000003|CARD#PRD-TEAM00000032"]
    assert (no_limit["credit_limit"], no_limit["expiration_date"]) == (None, None)
    assert no_limit["past_expiration"] is False
    assert (
        items["CLI-TEAM00000005|CARD#PRD-TEAM00000052"]["product_status"] == "Blocked"
    )


def test_transactions_are_keyed_for_the_index_and_spelled_one_way(
    persona_con: duckdb.DuckDBPyConnection,
) -> None:
    items = built(persona_con)

    declined = items["CLI-TEAM00000003|TXN#TRX-TEAM0000000000000302"]
    assert declined["card_key"] == "CLI-TEAM00000003#PRD-TEAM00000031"
    assert declined["listed_at"] == "2026-06-15 21:07:33#TRX-TEAM0000000000000302"
    assert declined["transaction_country"] == "México"
    assert (declined["transaction_status"], declined["response_code"]) == (
        "Declined",
        "05",
    )
    assert declined["is_fraud"] is True
    assert declined["amount"] == Decimal("189.90")
    assert (
        items["CLI-TEAM00000003|TXN#TRX-TEAM0000000000000303"]["merchant_name"] is None
    )
    assert (
        items["CLI-TEAM00000003|TXN#TRX-TEAM0000000000000304"]["transaction_country"]
        == "Brazil"
    )


def test_nothing_the_tools_mustnt_read_is_exported(
    persona_con: duckdb.DuckDBPyConnection,
) -> None:
    items = built(persona_con)

    names = {name for item in items.values() for name in item}
    assert not names & {
        "product_number",
        "amount_usd",
        "fraud_score",
        "last_transaction_date",
    }
    text = json.dumps([{k: str(v) for k, v in item.items()} for item in items.values()])
    rows = PERSONA_FILES["products.csv"].splitlines()[1:]
    numbers = [row.split(",")[3] for row in rows]
    assert [n for n in numbers if n in text] == []


def test_a_persona_not_registered_by_the_as_of_instant_is_an_error(
    persona_con: duckdb.DuckDBPyConnection,
) -> None:
    with pytest.raises(TinyExportError, match="registered"):
        build(
            persona_con,
            ["CLI-TEAM00000003", "CLI-TEAM00000010"],
            stamp=STAMP,
            business_date=BUSINESS_DATE,
            as_of=AS_OF,
        )


def test_only_partitions_from_just_before_the_window_are_needed() -> None:
    assert partitions_needed(date(2026, 3, 18), AS_OF)
    assert not partitions_needed(date(2026, 3, 17), AS_OF)
    assert partitions_needed(date(2026, 6, 19), AS_OF)


def test_the_pipeline_version_is_a_stable_hash_of_what_shapes_the_export() -> None:
    assert re.fullmatch(r"[0-9a-f]{16}", pipeline_version())
    assert pipeline_version() == pipeline_version()


def test_the_clock_comes_from_the_same_snapshots_profile(tmp_path: Path) -> None:
    path = tmp_path / "profiling.json"
    path.write_text(
        json.dumps(
            {
                "snapshot_id": "snap",
                "business_date": "2026-06-17",
                "as_of": "2026-06-18 06:00:00",
            }
        )
    )

    assert clock_from_profile(path, "snap") == (BUSINESS_DATE, AS_OF)
    with pytest.raises(TinyExportError, match="another snapshot"):
        clock_from_profile(path, "other")
