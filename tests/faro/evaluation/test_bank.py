"""
The evaluation's bank builds through the real pipeline with the pinned snapshot's clock, holds one held-out customer,
and reproduces the contracts' example records, so the oracle's tests and the tools read the same made-up bank.
"""

import json
from importlib.resources import files
from typing import Any

import pytest

from faro.split import held_out

from .bank import EXAMPLE_CUSTOMER, HELD_OUT_CUSTOMER, Bank

pytestmark = pytest.mark.xdist_group("evaluation_bank")


def example(name: str) -> list[dict[str, Any]]:
    text = (
        files("faro.contracts")
        .joinpath(f"examples/{name}.json")
        .read_text(encoding="utf-8")
    )
    loaded: list[dict[str, Any]] = json.loads(text)
    return loaded


def test_the_bank_reads_as_of_the_pinned_snapshots_instant(bank: Bank) -> None:
    [metadata] = [i for i in bank.items if i["kind"] == "metadata"]

    assert metadata["clock"] == {
        "business_date": "2026-06-17",
        "as_of": "2026-06-18 06:00:00",
    }


def test_exactly_the_customer_meant_to_be_is_held_out(bank: Bank) -> None:
    ours = {
        i["customer_id"]
        for i in bank.items
        if i["kind"] == "customer" and "EVAL" in i["customer_id"]
    }

    assert {c for c in ours if held_out(c)} == {HELD_OUT_CUSTOMER}
    assert not held_out(EXAMPLE_CUSTOMER)


def test_the_bank_reproduces_the_contracts_example_records(bank: Bank) -> None:
    built = {(i["pk"], i["sk"]): i for i in bank.items}

    for item in example("tools-data"):
        if item["kind"] == "metadata":
            continue
        assert built[(item["pk"], item["sk"])] == item
