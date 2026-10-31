"""
The sign-in's fixtures are read as if exported, and its fault plans fail a tool's attempts until their failures run
out, in memory as in DynamoDB, for the sign-in and the customer they name only (ADR-0004's amendment of 2026-10-01;
EVL-02, EVL-05, EVL-06; POL-25, POL-40, POL-48).
"""

import copy
import json
from collections.abc import Callable, Iterator
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

from faro.export.items import attribute
from faro.tools import block, reads
from faro.tools.sandbox import (
    BlockStores,
    DynamoOverlay,
    MemoryOverlay,
    MemorySandbox,
    Overlay,
    OverlayItemError,
    Stores,
)
from faro.tools.store import MemoryData

from .conftest import OTHER, OWN, SIGN_IN, example_items
from .test_block_card import NOW, confirmation

OVERLAY = "banking-agent-local-sandbox-overlay"
ELSEWHERE = "0b4a9c3e-5d2f-4e8a-9c71-2f6d8e1a7b50"
REAL = "PRD-EXAMPLE00002"
FIXTURE = "PRD-FIXTUREA0001"


def examples(name: str) -> list[dict[str, Any]]:
    text = files("faro.contracts").joinpath(f"examples/{name}").read_text()
    loaded: list[dict[str, Any]] = json.loads(text)
    return loaded


def fixture_card(**fields: Any) -> dict[str, Any]:
    return {**examples("overlay.fixture_card.json")[0], **fields}


def fixture_transaction(
    number: int, card_id: str, date: str, **fields: Any
) -> dict[str, Any]:
    transaction_id = f"TRX-FIXTURE{number:013d}"
    item = copy.deepcopy(examples("overlay.fixture_transaction.json")[0])
    item.update(
        item=f"FIXTURE#TRX#{transaction_id}",
        transaction_id=transaction_id,
        card_id=card_id,
        card_key=f"{OWN}#{card_id}",
        transaction_date=date,
        listed_at=f"{date}#{transaction_id}",
        is_fraud=True,
    )
    return {**item, **fields}


def plan(tool: str, failures: int, error: str, **fields: Any) -> dict[str, Any]:
    return {
        "sign_in": SIGN_IN,
        "item": f"FAULT#{tool}",
        "customer_id": OWN,
        "tool": tool,
        "failures": failures,
        "error": error,
        "ttl": 1791043200,
        **fields,
    }


EXAMPLES = [
    fixture_card(),
    *(
        fixture_transaction(n, card, date)
        for n, card, date in (
            (1, REAL, "2026-06-16 18:22:05"),
            (2, REAL, "2026-06-12 09:41:50"),
            (3, FIXTURE, "2026-06-10 13:05:12"),
        )
    ),
]

Backend = Callable[[list[dict[str, Any]]], Overlay]


@pytest.fixture(params=["memory", "dynamodb"])
def backend(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Backend]:
    if request.param == "memory":
        yield lambda items: MemoryOverlay(copy.deepcopy(items))
        return
    credentials = tmp_path / "aws-credentials"
    credentials.write_text(
        "[default]\naws_access_key_id = testing\naws_secret_access_key = testing\n"
    )
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=OVERLAY,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": name, "AttributeType": "S"}
                for name in ("sign_in", "item")
            ],
            KeySchema=[
                {"AttributeName": "sign_in", "KeyType": "HASH"},
                {"AttributeName": "item", "KeyType": "RANGE"},
            ],
        )

        def opened(items: list[dict[str, Any]]) -> Overlay:
            for item in json.loads(json.dumps(items), parse_float=Decimal):
                client.put_item(
                    TableName=OVERLAY,
                    Item={k: attribute(v) for k, v in item.items()},
                )
            return DynamoOverlay(client, OVERLAY)

        yield opened


def read(
    overlay: Overlay, tool: str, sign_in: str = SIGN_IN, **arguments: Any
) -> dict[str, Any]:
    stores = Stores(MemoryData(example_items()), overlay)
    return reads.answer(
        tool, {"customer_id": OWN, "origin_jti": sign_in, **arguments}, lambda: stores
    )


def listed(output: dict[str, Any]) -> list[str]:
    return [t["transaction_id"] for t in output["transactions"]]


def keys(node: Any) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in keys(v)}
    return set()


def test_a_fixture_transaction_is_listed_among_the_exports_newest_first(
    backend: Backend,
) -> None:
    output = read(backend(EXAMPLES), "find_transactions", card_id=REAL)

    assert listed(output) == [
        "TRX-FIXTURE0000000000001",
        "TRX-EXAMPLE0000000000003",
        "TRX-FIXTURE0000000000002",
    ]
    assert output["next_cursor"] is None
    declined = output["transactions"][0]
    assert (declined["response_code"], declined["response_meaning"]) == ("61", None)


def test_a_fixture_card_is_listed_and_read_as_if_exported(backend: Backend) -> None:
    overlay = backend(EXAMPLES)

    cards = read(overlay, "list_cards")["cards"]
    card = read(overlay, "get_card", card_id=FIXTURE)
    credit = read(overlay, "get_available_credit", card_id=FIXTURE)
    page = read(overlay, "find_transactions", card_id=FIXTURE)

    debits = [c["card_id"] for c in cards if c["product_type"] == "Tarjeta Débito"]
    assert debits == ["PRD-EXAMPLE00005", FIXTURE]
    assert (card["outcome"], card["card"]["product_status"]) == ("ok", "Blocked")
    assert credit["card"]["availability"] == "debit_card"
    assert listed(page) == ["TRX-FIXTURE0000000000003"]


def test_pages_run_across_fixtures_and_the_export(backend: Backend) -> None:
    on_real = [
        fixture_transaction(n, REAL, f"2026-06-15 10:{n:02d}:00") for n in range(10, 20)
    ]
    on_fixture = [
        fixture_transaction(n, FIXTURE, f"2026-06-0{1 + n % 8} 08:00:{n:02d}")
        for n in range(20, 31)
    ]
    overlay = backend([fixture_card(), *on_real, *on_fixture])

    first = read(overlay, "find_transactions", card_id=REAL)
    second = read(
        overlay, "find_transactions", card_id=REAL, cursor=first["next_cursor"]
    )
    alone = read(overlay, "find_transactions", card_id=FIXTURE)
    after = read(
        overlay, "find_transactions", card_id=FIXTURE, cursor=alone["next_cursor"]
    )

    assert listed(first) == [f"TRX-FIXTURE{n:013d}" for n in range(19, 9, -1)]
    assert listed(second) == ["TRX-EXAMPLE0000000000003"]
    assert second["next_cursor"] is None
    assert len(listed(alone)) == 10 and len(listed(after)) == 1
    assert set(listed(alone) + listed(after)) == {
        f"TRX-FIXTURE{n:013d}" for n in range(20, 31)
    }


def test_a_fixture_outside_the_window_is_left_out(backend: Backend) -> None:
    overlay = backend(
        [
            fixture_transaction(1, REAL, "2026-03-20 06:00:00"),
            fixture_transaction(2, REAL, "2026-06-18 06:00:00"),
            fixture_transaction(3, REAL, "2026-06-18 06:00:01"),
        ]
    )

    assert "TRX-FIXTURE0000000000002" in listed(
        read(overlay, "find_transactions", card_id=REAL)
    )
    assert not {"TRX-FIXTURE0000000000001", "TRX-FIXTURE0000000000003"} & set(
        listed(read(overlay, "find_transactions", card_id=REAL))
    )


def test_another_sign_ins_or_customers_fixtures_are_never_read(
    backend: Backend,
) -> None:
    theirs = fixture_transaction(
        5, "PRD-EXAMPLE00008", "2026-06-16 10:00:00", customer_id=OTHER
    )
    theirs["card_key"] = f"{OTHER}#PRD-EXAMPLE00008"
    overlay = backend(
        [
            fixture_card(sign_in=ELSEWHERE),
            fixture_transaction(4, REAL, "2026-06-16 09:00:00", sign_in=ELSEWHERE),
            fixture_card(customer_id=OTHER),
            theirs,
        ]
    )

    cards = [c["card_id"] for c in read(overlay, "list_cards")["cards"]]

    assert FIXTURE not in cards
    assert read(overlay, "get_card", card_id=FIXTURE)["outcome"] == "not_found"
    assert listed(read(overlay, "find_transactions", card_id=REAL)) == [
        "TRX-EXAMPLE0000000000003"
    ]


@pytest.mark.parametrize(
    "broken",
    [
        {"card_key": f"{OWN}#PRD-EXAMPLE00005"},
        {"listed_at": "2026-06-16 18:22:06#TRX-FIXTURE0000000000001"},
        {"item": "FIXTURE#TRX#TRX-FIXTURE0000000000009"},
        {"amount": "310.00"},
    ],
)
def test_a_fixture_that_breaks_its_contract_or_keys_fails_the_call(
    backend: Backend, broken: dict[str, Any]
) -> None:
    overlay = backend([fixture_transaction(1, REAL, "2026-06-16 18:22:05", **broken)])

    with pytest.raises(OverlayItemError) as raised:
        read(overlay, "find_transactions", card_id=REAL)
    assert "310" not in str(raised.value)


def test_no_read_returns_is_fraud(backend: Backend) -> None:
    overlay = backend(EXAMPLES)

    for tool, arguments in (
        ("list_cards", {}),
        ("get_card", {"card_id": FIXTURE}),
        ("find_transactions", {"card_id": REAL}),
        ("find_transactions", {"card_id": FIXTURE}),
    ):
        assert "is_fraud" not in keys(read(overlay, tool, **arguments))


@pytest.mark.parametrize("error", ["timeout", "throttled", "lambda_error"])
def test_a_planned_attempt_is_answered_with_its_error_until_none_is_left(
    backend: Backend, error: str
) -> None:
    overlay = backend([plan("get_card", 2, error)])

    outputs = [read(overlay, "get_card", card_id=REAL) for _ in range(3)]

    assert outputs[:2] == [{"outcome": "fault", "error": error}] * 2
    assert outputs[2]["outcome"] == "ok"
    assert read(overlay, "list_cards")["outcome"] == "ok"


def test_the_count_is_per_attempt_across_the_sign_ins_calls(backend: Backend) -> None:
    overlay = backend([plan("find_transactions", 4, "throttled")])

    first_call = [read(overlay, "find_transactions", card_id=REAL) for _ in range(3)]
    second_call = [read(overlay, "find_transactions", card_id=REAL) for _ in range(2)]

    assert [o["outcome"] for o in first_call] == ["fault"] * 3
    assert [o["outcome"] for o in second_call] == ["fault", "ok"]


def test_another_sign_ins_or_customers_plan_fails_nothing(backend: Backend) -> None:
    overlay = backend(
        [
            plan("get_card", 3, "timeout", customer_id=OTHER),
            plan("list_cards", 3, "timeout"),
        ]
    )

    assert read(overlay, "get_card", card_id=REAL)["outcome"] == "ok"
    assert read(overlay, "list_cards", sign_in=ELSEWHERE)["outcome"] == "ok"
    assert read(overlay, "list_cards")["outcome"] == "fault"


def test_a_plan_that_breaks_its_contract_fails_the_call(backend: Backend) -> None:
    overlay = backend([plan("get_card", 3, "transport")])

    with pytest.raises(OverlayItemError):
        read(overlay, "get_card", card_id=REAL)


def test_an_invalid_input_takes_no_planned_failure() -> None:
    overlay = MemoryOverlay([plan("get_card", 1, "timeout")])

    refused = read(overlay, "get_card", card_id="4821")
    taken = read(overlay, "get_card", card_id=REAL)

    assert refused["outcome"] == "invalid_input"
    assert taken["outcome"] == "fault"


def blocking(sandbox: MemorySandbox, **arguments: Any) -> dict[str, Any]:
    stores = BlockStores(MemoryData(example_items()), sandbox)
    call = {
        "customer_id": OWN,
        "origin_jti": SIGN_IN,
        "card_id": REAL,
        "reason": "lost",
        "confirmation_id": confirmation()["confirmation_id"],
        **arguments,
    }
    return block.answer(call, lambda: stores, lambda: NOW)


def test_a_planned_block_attempt_leaves_the_confirmation_as_it_was() -> None:
    sandbox = MemorySandbox(
        [confirmation(card_id=REAL, reason="lost")],
        [plan("block_card", 1, "lambda_error")],
    )

    planned = blocking(sandbox)
    held = sandbox.confirmation(confirmation()["confirmation_id"])
    blocked = blocking(sandbox)

    assert planned == {"outcome": "fault", "error": "lambda_error"}
    assert held is not None and held["status"] == "confirmed"
    assert (blocked["outcome"], blocked["block_outcome"]) == ("ok", "verified")


def test_a_fixture_card_is_blocked_only_when_its_status_allows() -> None:
    sandbox = MemorySandbox(
        [confirmation(card_id=FIXTURE, reason="lost")], [fixture_card()]
    )

    refused = blocking(sandbox, card_id=FIXTURE)

    assert (refused["refusal"], refused["product_status"]) == ("not_active", "Blocked")


def test_a_fixture_cards_block_shows_in_its_sign_ins_reads() -> None:
    sandbox = MemorySandbox(
        [confirmation(card_id=FIXTURE, reason="lost")],
        [fixture_card(product_status="Active")],
    )

    blocked = blocking(sandbox, card_id=FIXTURE)
    shown = read(sandbox, "get_card", card_id=FIXTURE)

    assert blocked["block_outcome"] == "verified"
    assert shown["card"]["product_status"] == "Blocked"
