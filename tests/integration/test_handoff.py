"""
file_handoff against the deployed stack, and the README's journey through the Runtime. The Lambda is off the Gateway,
and it checks the customer's access token through Cognito's GetUser before anything else, then saves a draft under the
customer and sign-in the token names (ADR-0004, its amendments of 2026-09-30; SEC-05); the tests invoke it with the
deploy role, as the Runtime invokes it with its own. In the journey, the Portuguese persona reports a charge they don't
recognize: the chat finds it in the card's window and offers the block, and the case goes to dispute intake with its
reference in the reply, normal once the block is verified and urgent when it's cancelled, each of its facts cited to a
recorded tool call (POL-27, POL-39, POL-45, POL-47; CTL-05, EVL-04, OPS-02). Assertions compare without printing a
reply, a transaction, or an ID.
"""

import base64
import json
import uuid
from collections import Counter
from collections.abc import Callable
from typing import Any

import boto3
import pytest

from faro.agent.nodes.find import PAGES
from faro.agent.texts import render
from faro.contracts import validator

from .conftest import SignIn, User, claims, stored
from .test_block import Conversation, control_of, reply, status
from .test_stack import arguments, call, tool_output

pytestmark = pytest.mark.integration


def draft(access: str, **overrides: Any) -> dict[str, Any]:
    token = claims(access)
    return {
        "customer_id": token.get("customer_id", "CLI-ITEST0000404"),
        "origin_jti": token["origin_jti"],
        "call_id": str(uuid.uuid4()),
        "mode": "draft",
        "draft": {
            "handoff_id": str(uuid.uuid4()),
            "language": "pt",
            "reason_code": "unrecognized_charge",
            "confirmation_id": str(uuid.uuid4()),
            "card_id": "PRD-ITEST0000001",
            "reason": "unrecognized_charge",
        },
    } | overrides


def file_handoff(
    outputs: dict[str, Any], token: str | None, arguments: dict[str, Any]
) -> dict[str, Any]:
    response = boto3.client("lambda", region_name="us-east-1").invoke(
        FunctionName=outputs["handoff"]["function"],
        Payload=json.dumps({"token": token, "input": arguments}).encode(),
    )
    assert "FunctionError" not in response
    output: dict[str, Any] = json.loads(response["Payload"].read())
    assert validator("tools", "file_handoff_output").is_valid(output)
    return output


def forged(access: str, **changes: str) -> str:
    header, payload, signature = access.split(".")
    body = claims(access) | changes
    encoded = base64.urlsafe_b64encode(json.dumps(body).encode()).rstrip(b"=")
    return f"{header}.{encoded.decode()}.{signature}"


def test_a_customers_own_token_saves_a_draft_under_their_sign_in(
    outputs: dict[str, Any],
    users: dict[str, User],
    sign_in: SignIn,
    saved: list[str],
) -> None:
    access = sign_in(users["customer"], "customer")["access"]
    arguments = draft(access)
    handoff_id = arguments["draft"]["handoff_id"]
    saved.append(handoff_id)

    output = file_handoff(outputs, access, arguments)

    assert output == {
        "outcome": "ok",
        "status": "draft_saved",
        "handoff_id": handoff_id,
    }
    item = stored(outputs, handoff_id)
    assert item is not None
    owned = item["customer_id"] == users["customer"].customer_id
    assert item["status"] == "draft"
    assert owned
    bound = item["draft"]["sign_in"] == claims(access)["origin_jti"]
    assert bound, "the draft isn't saved under the token's sign-in"
    assert item["source"] == "demo"


def test_a_retried_draft_is_saved_once(
    outputs: dict[str, Any],
    users: dict[str, User],
    sign_in: SignIn,
    saved: list[str],
) -> None:
    access = sign_in(users["customer"], "customer")["access"]
    arguments = draft(access)
    saved.append(arguments["draft"]["handoff_id"])

    first = file_handoff(outputs, access, arguments)
    second = file_handoff(outputs, access, arguments)

    assert first == second


@pytest.mark.parametrize(
    "token",
    ["missing", "forged_customer", "forged_sign_in", "id_token", "staff"],
)
def test_file_handoff_refuses_a_token_that_isnt_a_customers_own(
    outputs: dict[str, Any],
    users: dict[str, User],
    sign_in: SignIn,
    token: str,
) -> None:
    access = sign_in(users["customer"], "customer")["access"]
    other = users["other_customer"].customer_id
    assert other is not None
    presented = {
        "missing": None,
        "forged_customer": forged(access, customer_id=other),
        "forged_sign_in": forged(access, origin_jti=str(uuid.uuid4())),
        "id_token": sign_in(users["customer"], "customer")["id"],
        "staff": sign_in(users["staff"], "staff")["access"],
    }[token]

    output = file_handoff(outputs, presented, draft(access))

    assert output == {"outcome": "refused", "refusal": "token_invalid"}


def test_file_handoff_refuses_another_customers_id_or_sign_in(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn
) -> None:
    access = sign_in(users["customer"], "customer")["access"]
    other = users["other_customer"].customer_id
    assert other is not None

    outputs_seen = [
        file_handoff(outputs, access, draft(access, customer_id=other)),
        file_handoff(outputs, access, draft(access, origin_jti=str(uuid.uuid4()))),
    ]

    assert outputs_seen == [{"outcome": "refused", "refusal": "customer_mismatch"}] * 2


def test_a_draft_another_customer_saved_isnt_overwritten(
    outputs: dict[str, Any],
    users: dict[str, User],
    sign_in: SignIn,
    saved: list[str],
) -> None:
    access = sign_in(users["customer"], "customer")["access"]
    other = sign_in(users["other_customer"], "customer")["access"]
    arguments = draft(access)
    saved.append(arguments["draft"]["handoff_id"])
    file_handoff(outputs, access, arguments)

    output = file_handoff(
        outputs,
        other,
        draft(other, draft=arguments["draft"]),
    )

    assert output == {"outcome": "refused", "refusal": "customer_mismatch"}


def window(outputs: dict[str, Any], access: str, card_id: str) -> list[dict[str, Any]]:
    """
    The card's transactions as the chat reads them: newest first, as many pages as it reads.
    """
    listed: list[dict[str, Any]] = []
    cursor: dict[str, str] = {}
    for _ in range(PAGES):
        read = tool_output(
            call(
                outputs,
                access,
                "find_transactions",
                arguments(access, card_id=card_id, **cursor),
            )
        )
        listed += read["transactions"]
        if read.get("next_cursor") is None:
            break
        cursor = {"cursor": read["next_cursor"]}
    return listed


def disputed(listed: list[dict[str, Any]]) -> dict[str, Any]:
    """
    The newest approved purchase with a merchant, whose amount no other transaction shares, so a message can name it.
    """
    amounts = Counter(t["amount"] for t in listed)
    found: dict[str, Any] = next(
        t
        for t in listed
        if (t["transaction_type"], t["transaction_status"]) == ("Purchase", "Approved")
        and t["merchant_name"]
        and amounts[t["amount"]] == 1
    )
    return found


def named(charge: dict[str, Any]) -> str:
    date = charge["transaction_date"]
    amount = f"{charge['amount']:.2f}".replace(".", ",")
    return (
        f"uma compra de {amount} {charge['currency']} em {charge['merchant_name']},"
        f" no dia {date[8:10]}/{date[5:7]}"
    )


def unmatched(case: dict[str, Any], entries: list[dict[str, Any]]) -> list[str]:
    """
    Where a filed case parts from the execution record: evidence that isn't a recorded call of its tool and time, and
    facts that cite no evidence. The filing's own call, recorded once it returns, need only be recorded.
    """
    calls = {e["call_id"]: e for e in entries if e["kind"] == "tool_call"}
    payload = case["payload"]
    problems = []
    for i, item in enumerate(payload["evidence"]):
        recorded = calls.get(item["call_id"])
        if (
            recorded is None
            or recorded["tool"] != item["tool"]
            or (
                item["tool"] != "file_handoff"
                and recorded["called_at"] != item["called_at"]
            )
        ):
            problems.append(f"evidence {i}")
    cited = {item["call_id"] for item in payload["evidence"]}
    problems += [
        f"fact {i}"
        for i, fact in enumerate(payload["verified_facts"])
        if fact["evidence"] not in cited
    ]
    return problems


Dispute = tuple[Conversation, dict[str, Any], dict[str, Any], str]


def dispute(outputs: dict[str, Any], access: str) -> Dispute:
    """
    A conversation in Portuguese on a new sign-in, the persona's active credit card, a charge on it to dispute, and the
    persona's country, by which the reply groups an amount.
    """
    listed = tool_output(call(outputs, access, "list_cards", arguments(access)))
    card: dict[str, Any] = next(
        c
        for c in listed["cards"]
        if c["product_status"] == "Active"
        and c["product_type"] == "Tarjeta Crédito"
        and not c["past_expiration"]
    )
    charge = disputed(window(outputs, access, card["card_id"]))
    country: str = listed["customer"]["country"]
    return Conversation(outputs, access, "pt"), card, charge, country


@pytest.fixture
def disputing(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn
) -> Callable[[], Dispute]:
    def start() -> Dispute:
        return dispute(outputs, sign_in(users["other_customer"], "customer")["access"])

    return start


def reported(
    chat: Conversation, card: dict[str, Any], charge: dict[str, Any], country: str
) -> list[dict[str, Any]]:
    shown = chat.say(
        f"Não reconheço {named(charge)}, no meu cartão de crédito final {card['last_four']}."
    )
    control = control_of(shown)
    assert (control["kind"], control["reason"]) == (
        "block_confirmation",
        "unrecognized_charge",
    )
    # The charge stands in its own paragraph under the sentence (policy version 6).
    found = "\n\n".join(reply(shown).split("\n\n")[:2]) == render(
        "charge_found", "pt", {"card": card, "transaction": charge, "country": country}
    )
    assert found, "the reply doesn't name the charge the customer described"
    draft = next(
        e
        for e in chat.last_turn()
        if e["kind"] == "tool_call" and e["tool"] == "file_handoff"
    )
    assert (draft["via"], draft["result"]["status"]) == ("direct", "draft_saved")
    return shown


def filed_case(
    outputs: dict[str, Any], chat: Conversation, saved: list[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    filed = next(e for e in chat.last_turn() if e["kind"] == "handoff")
    saved.append(filed["handoff_id"])
    case = stored(outputs, filed["handoff_id"])
    assert case is not None
    return filed, case


def test_a_charge_the_customer_doesnt_recognize_is_blocked_and_filed_to_dispute_intake(
    outputs: dict[str, Any], disputing: Any, saved: list[str]
) -> None:
    chat, card, charge, country = disputing()
    shown = reported(chat, card, charge, country)

    done = chat.press("confirm", shown)

    assert done[-1]["outcome"] == {"type": "success"}
    filed, case = filed_case(outputs, chat, saved)
    assert (
        filed["reason_code"],
        filed["trigger"],
        filed["queue"],
        filed["priority"],
        filed["status"],
        filed["flagged"],
    ) == ("unrecognized_charge", "required", "dispute_intake", "normal", "filed", False)
    said = reply(done).split("\n\n") == [
        render("block_verified", "pt", {"card": {**card, "product_status": "Blocked"}}),
        render("handoff_filed", "pt", {"reference": filed["reference"]}),
    ]
    assert said, "the reply isn't the verified block's with the case's reference"
    assert status(outputs, chat.access, card["card_id"]) == "Blocked"
    # A required handoff's turn records hand_off, verified block or not (ADR-0005 as amended 2026-10-02).
    assert chat.decision()["outcome_class"] == "hand_off"
    # The case filed is the draft the control's turn saved.
    drafted = next(
        e["result"]["handoff_id"]
        for e in chat.entries()
        if e["kind"] == "tool_call" and e["tool"] == "file_handoff"
    )
    assert drafted == filed["handoff_id"]
    assert (
        case["status"],
        case["reference"] == filed["reference"],
        case["queue"],
        case["priority"],
        case["flagged"],
        case["validation_errors"],
    ) == ("filed", True, "dispute_intake", "normal", False, [])
    payload = case["payload"]
    about = [f for f in payload["verified_facts"] if f["subject"] == "transaction"]
    stated = {f["id"] for f in about} == {charge["transaction_id"]} and {
        ("amount", charge["amount"]),
        ("currency", charge["currency"]),
        ("product_id", card["card_id"]),
    } <= {(f["field"], f["value"]) for f in about}
    assert stated, "the case doesn't state the charge the customer described"
    assert [(a["action"], a["outcome"]) for a in payload["actions"]] == [
        ("block_card", "verified")
    ]
    assert unmatched(case, chat.entries()) == []


def test_a_cancelled_block_files_the_charge_as_urgent(
    outputs: dict[str, Any], disputing: Any, saved: list[str]
) -> None:
    chat, card, charge, country = disputing()
    shown = reported(chat, card, charge, country)

    cancelled = chat.press("cancel", shown)

    assert cancelled[-1]["outcome"] == {"type": "success"}
    filed, case = filed_case(outputs, chat, saved)
    # Left unblocked, the charge's card makes the case urgent (POL-47).
    assert (filed["queue"], filed["priority"], filed["flagged"]) == (
        "dispute_intake",
        "urgent",
        False,
    )
    said = reply(cancelled).split("\n\n") == [
        render("confirmation_cancelled", "pt", {"card": card}),
        render("handoff_filed", "pt", {"reference": filed["reference"]}),
    ]
    assert said, "the reply isn't the cancel's with the case's reference"
    assert status(outputs, chat.access, card["card_id"]) == "Active"
    assert (case["priority"], case["validation_errors"]) == ("urgent", [])
    assert [(a["action"], a["outcome"]) for a in case["payload"]["actions"]] == [
        ("block_card", "declined_by_customer")
    ]
    assert unmatched(case, chat.entries()) == []
