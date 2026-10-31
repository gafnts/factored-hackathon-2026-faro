"""
The bronze contracts come from the dictionary and the profile's corrections, each with its reason, and tag every column
by the personal data it holds (DML-02, POL-11, SEC-03).
"""

import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from string import Template
from typing import Any

import duckdb
import pytest
import yaml

from faro.analysis.catalog import TABLES, table
from faro.pipeline import contracts
from faro.pipeline.__main__ import main


def test_the_committed_bronze_is_the_generators() -> None:
    committed = {
        path.name: path.read_text(encoding="utf-8")
        for path in contracts.MODELS.iterdir()
    }

    assert committed == contracts.render(contracts.build()), (
        "run python -m faro.pipeline contracts"
    )


def test_every_table_and_column_of_the_dictionary_has_a_contract_in_its_order() -> None:
    built = contracts.build()

    assert [c.name for c in built] == [t.name for t in TABLES]
    for contract, dictionary in zip(built, TABLES, strict=True):
        assert [c.name for c in contract.columns] == [
            c.name for c in dictionary.columns
        ]


def test_the_corrections_describe_the_delivery_and_keep_the_dictionarys_values() -> (
    None
):
    built = {c.name: c for c in contracts.build()}

    country = built["customers"].column("country")
    assert country.values == ("Argentina", "Colombia", "México")
    assert country.dictionary_values == ("Mexico", "Colombia", "Argentina")
    assert country.reasons
    duration = built["call_transcripts"].column("duration_seconds")
    assert duration.nullable
    assert "NOT NULL" in duration.dictionary
    product_type = built["products"].column("product_type")
    assert "Tarjeta Crédito" in product_type.values
    assert product_type.dictionary_values == ()


def test_every_column_is_tagged_and_only_the_card_number_is_one() -> None:
    columns = [
        (t.name, c.name, c.personal_data) for t in contracts.build() for c in t.columns
    ]

    assert {tag for *_, tag in columns} <= set(contracts.TAGS)
    assert [(t, c) for t, c, tag in columns if tag == "card_number"] == [
        ("products", "product_number")
    ]
    assert ("customers", "customer_id", "none") in columns
    assert ("customers", "document_number", "identity") in columns
    assert ("customers", "email", "contact") in columns


@pytest.mark.parametrize(
    ("loaded", "message"),
    [
        ({"corrections": {"accounts": {}}}, "accounts isn't a table"),
        ({"corrections": {"customers": {"nickname": {}}}}, "has no column nickname"),
        (
            {"corrections": {"customers": {"country": {"values": ["X"]}}}},
            "gives no reason",
        ),
        (
            {"corrections": {"customers": {"country": {"reason": "Because."}}}},
            "changes values or nullable",
        ),
        (
            {"corrections": {"customers": {"country": {"type": "X", "reason": "."}}}},
            "changes values or nullable",
        ),
        ({"personal_data": {"customers": {"secret": ["email"]}}}, "isn't a"),
        (
            {
                "personal_data": {
                    "customers": {"contact": ["email"], "identity": ["email"]}
                }
            },
            "two personal-data tags",
        ),
    ],
)
def test_a_correction_or_tag_that_doesnt_fit_the_dictionary_is_refused(
    loaded: dict[str, Any], message: str
) -> None:
    with pytest.raises((contracts.ContractError, KeyError), match=message):
        contracts.build(loaded=loaded)


def test_the_typed_read_never_infers_or_forgives() -> None:
    for contract in contracts.build():
        read = contracts.location(contract)

        assert "auto_detect = false" in read
        assert "hive_partitioning = false" in read
        assert "header = true" in read
        for lenient in ("all_varchar", "union_by_name", "try_cast", "ignore_errors"):
            assert lenient not in read.lower()
    daily = {c.name: c for c in contracts.build()}["transactions"]
    assert contracts.location(daily).startswith(
        "read_csv('$root/transactions/**/*.csv'"
    )


def test_the_typed_read_types_each_value_by_its_contract(tmp_path: Path) -> None:
    ledger = table(
        "ledger",
        """
        entry_id VARCHAR(10) NOT NULL
        booked TIMESTAMP NOT NULL
        day DATE NOT NULL
        amount DECIMAL(15,2) NOT NULL
        settled BOOLEAN NOT NULL
        count INTEGER
        note TEXT
        """,
        key=("entry_id",),
    )
    (tmp_path / "ledger.csv").write_bytes(
        "﻿entry_id,booked,day,amount,settled,count,note\n"
        'E1,2026-06-17 21:07:33,2026-06-17,189.90,True,,"two\nlines"\n'.encode()
    )
    [contract] = contracts.build((ledger,), {})
    read = Template(contracts.location(contract)).substitute(root=tmp_path)

    rows = duckdb.sql(f"select * exclude (filename) from {read}").fetchall()

    assert rows == [
        (
            "E1",
            datetime(2026, 6, 17, 21, 7, 33),
            date(2026, 6, 17),
            Decimal("189.90"),
            True,
            None,
            "two\nlines",
        )
    ]
    (tmp_path / "ledger.csv").write_text(
        "entry_id,booked,day,amount,settled,count,note\nE1,yesterday,2026-06-17,1,True,,\n"
    )
    with pytest.raises(duckdb.ConversionException):
        duckdb.sql(f"select * from {read}").fetchall()


def test_make_contracts_rewrites_bronze_and_removes_what_it_no_longer_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "models" / "bronze"
    directory.mkdir(parents=True)
    (directory / "bronze_accounts.sql").write_text("select 1\n")
    monkeypatch.setattr(contracts, "MODELS", directory)

    assert main(["contracts"]) == 0

    assert {p.name: p.read_text(encoding="utf-8") for p in directory.iterdir()} == (
        contracts.render(contracts.build())
    )


def test_only_the_rules_adr_0006_names_stop_the_build() -> None:
    document = yaml.safe_load(
        (contracts.MODELS / contracts.BRONZE).read_text(encoding="utf-8")
    )
    errors = set()
    for model in document["models"]:
        tests = list(model["data_tests"])
        tests += [t for c in model["columns"] for t in c.get("data_tests", [])]
        for test in tests:
            [(kind, entry)] = test.items()
            if entry.get("config", {}).get("severity") != "warn":
                errors.add(entry["name"].removeprefix(model["name"] + "_"))
    kinds = {
        re.sub(
            r"^.*?_(key_not_null|key_unique|accepted_values|references_\w+)$", r"\1", e
        )
        for e in errors
        if not e.endswith(
            ("processed_on_its_day", "files_match_the_lock", "rows_match_the_records")
        )
    }

    assert kinds == {
        "key_not_null",
        "key_unique",
        "accepted_values",
        "references_products",
        "references_customers",
    }
    assert {e for e in errors if "references" in e} == {
        "product_id_references_products",
        "customer_id_references_customers",
    }
    assert {e for e in errors if e.endswith("accepted_values")} == {
        f"{column}_accepted_values" for _, column in contracts.REQUIRED_VALUES
    }
