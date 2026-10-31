"""
The cases table takes a draft and files a case only on its conditions, as the in-memory store does: a case passes from
draft to filed once, never to another customer, and no two cases share a reference; is_fraud is read from each
transaction's own item (ADR-0007, Handoffs as cases, and its amendments of 2026-09-30; POL-40, POL-45, SEC-05).
"""

import copy
import json
from collections.abc import Iterator
from importlib.resources import files
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_dynamodb import DynamoDBClient

from faro.export.items import attribute
from faro.tools.cases import (
    ALPHABET,
    DynamoCases,
    DynamoFlags,
    MemoryCases,
    MemoryFlags,
    draw_reference,
    reference_item,
)

from .conftest import OWN, example_items

CASES = "banking-agent-local-handoff-cases"
DATA = "banking-agent-local-tools-data"


def examples() -> list[dict[str, Any]]:
    text = files("faro.contracts").joinpath("examples/handoff-case.json").read_text()
    loaded: list[dict[str, Any]] = json.loads(text)
    return loaded


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[DynamoDBClient]:
    credentials = tmp_path / "aws-credentials"
    credentials.write_text(
        "[default]\naws_access_key_id = testing\naws_secret_access_key = testing\n"
    )
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName=CASES,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        )
        dynamodb.create_table(
            TableName=DATA,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": n, "AttributeType": "S"} for n in ("pk", "sk")
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
        )
        for item in example_items():
            dynamodb.put_item(
                TableName=DATA, Item={k: attribute(v) for k, v in item.items()}
            )
        yield dynamodb


def stores(client: DynamoDBClient) -> list[Any]:
    return [DynamoCases(client, CASES), MemoryCases()]


def draft_and_filed() -> tuple[dict[str, Any], dict[str, Any]]:
    draft, filed, _, _ = copy.deepcopy(examples())
    return draft, filed


def test_both_stores_take_a_draft_then_file_it_once(client: DynamoDBClient) -> None:
    draft, filed = draft_and_filed()
    ref = reference_item(filed["reference"], filed["handoff_id"], filed["expires_at"])

    for cases in stores(client):
        assert cases.save_draft(draft) is True
        assert cases.save_draft(draft) is True
        assert cases.case(draft["handoff_id"]) == draft
        assert cases.file(filed, ref) == "filed"
        assert cases.case(filed["handoff_id"]) == filed
        assert cases.save_draft(draft) is False
        assert cases.file(filed, ref) == "case_taken"


def test_a_taken_reference_files_nothing(client: DynamoDBClient) -> None:
    _, filed = draft_and_filed()
    ref = reference_item(filed["reference"], filed["handoff_id"], filed["expires_at"])
    other = {**filed, "pk": "e3a91f6c-4b28-4d7e-8f05-c61b2d9a7e34"}
    other["handoff_id"] = other["pk"]

    for cases in stores(client):
        assert cases.file(filed, ref) == "filed"
        assert cases.file(other, ref) == "reference_taken"
        assert cases.case(other["handoff_id"]) is None


def test_a_reference_leads_to_the_case_that_took_it(client: DynamoDBClient) -> None:
    _, filed = draft_and_filed()
    ref = reference_item(filed["reference"], filed["handoff_id"], filed["expires_at"])

    for cases in stores(client):
        assert cases.holder(filed["reference"]) is None
        cases.file(filed, ref)
        assert cases.holder(filed["reference"]) == filed["handoff_id"]
        assert cases.holder("ZZZZ-9999") is None


def test_another_customers_draft_is_neither_overwritten_nor_filed(
    client: DynamoDBClient,
) -> None:
    draft, filed = draft_and_filed()
    theirs = {**draft, "customer_id": "CLI-EXAMPLE00009"}
    ref = reference_item(filed["reference"], filed["handoff_id"], filed["expires_at"])

    for cases in stores(client):
        assert cases.save_draft(theirs) is True
        assert cases.save_draft(draft) is False
        assert cases.file(filed, ref) == "case_taken"
        assert cases.case(draft["handoff_id"]) == theirs


def test_is_fraud_is_read_from_the_transactions_own_item(
    client: DynamoDBClient,
) -> None:
    for flags in (DynamoFlags(client, DATA), MemoryFlags(example_items())):
        assert flags.is_fraud(OWN, "TRX-EXAMPLE0000000000003") is False
        assert flags.is_fraud(OWN, "TRX-EXAMPLE0000000000404") is None
        assert flags.is_fraud("CLI-EXAMPLE00009", "TRX-EXAMPLE0000000000003") is None


def test_a_reference_is_eight_characters_of_crockfords_alphabet() -> None:
    drawn = {draw_reference() for _ in range(200)}

    assert len(drawn) == 200
    for reference in drawn:
        first, second = reference.split("-")
        assert len(first) == len(second) == 4
        assert set(first + second) <= set(ALPHABET)
    assert not set("ILOU") & set(ALPHABET)
