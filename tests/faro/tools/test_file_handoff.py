"""
file_handoff acts for the token's customer and sign-in only, saves a draft and files a case once, adds the customer's
status and each named transaction's is_fraud citing its own call, and files what the schema accepts, flagged with each
failure's path and rule, never its value (ADR-0004, The handoff and Where the tools run, and their amendments; ADR-0007,
Handoffs as cases; POL-07, POL-08, POL-11, POL-12, POL-40, POL-45 to POL-47; CTL-05, SEC-05, OPS-05).
"""

import copy
import json
import re
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any

import pytest

from faro.contracts import validator
from faro.tools import handoff
from faro.tools.cases import MemoryCases, MemoryFlags
from faro.tools.file_handoff import HandoffStores
from faro.tools.handoff import answer, handler
from faro.tools.identity import Caller
from faro.tools.provenance import MemoryRecords
from faro.tools.sandbox import MemoryOverlay
from faro.tools.store import MemoryData

from .conftest import OTHER, OWN, SIGN_IN, example_items, recorded_example

NOW = datetime(2026, 10, 2, 15, 42, 8, 512000, tzinfo=UTC)
CUSTOMER = Caller("7d3e1f0a-2b4c-4d6e-8f10-a1b2c3d4e5f6", OWN, SIGN_IN, "demo")
TOKENS = {
    "customer-token": CUSTOMER,
    "other-token": Caller(
        "0f9e8d7c-6b5a-4c3d-9e2f-1a0b9c8d7e6f",
        OTHER,
        "c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f",
        "demo",
    ),
    "evaluation-token": Caller(CUSTOMER.sub, OWN, SIGN_IN, "evaluation"),
}
DIGITS = "4123 4567 8901 4821"


def examples(name: str) -> list[dict[str, Any]]:
    text = files("faro.contracts").joinpath(f"examples/{name}").read_text()
    loaded: list[dict[str, Any]] = json.loads(text)
    return loaded


class Tokens:
    def verify(self, token: str) -> Caller | None:
        return TOKENS.get(token)


def draft_input() -> dict[str, Any]:
    found: dict[str, Any] = copy.deepcopy(examples("tools.file_handoff_input.json")[0])
    return found


def file_input() -> dict[str, Any]:
    found: dict[str, Any] = copy.deepcopy(examples("tools.file_handoff_input.json")[1])
    return found


@pytest.fixture
def stores() -> HandoffStores:
    items = example_items()
    return HandoffStores(
        data=MemoryData(items),
        flags=MemoryFlags(items),
        cases=MemoryCases(),
        verifier=Tokens(),
        records=MemoryRecords(recorded_example()),
    )


def run(
    stores: HandoffStores, arguments: dict[str, Any], token: str = "customer-token"
) -> dict[str, Any]:
    output = answer({"token": token, "input": arguments}, lambda: stores, lambda: NOW)
    validator("tools", "file_handoff_output").validate(output)
    return output


def stored(stores: HandoffStores) -> dict[str, Any]:
    held: dict[str, Any] = stores.cases.items  # type: ignore[attr-defined]
    for item in held.values():
        validator("handoff-case").validate(item)
    return held


def test_a_draft_is_saved_for_the_tokens_customer_and_sign_in(
    stores: HandoffStores,
) -> None:
    output = run(stores, draft_input())

    assert output == {
        "outcome": "ok",
        "status": "draft_saved",
        "handoff_id": draft_input()["draft"]["handoff_id"],
    }
    (case,) = stored(stores).values()
    assert case["status"] == "draft"
    assert case["draft"]["sign_in"] == SIGN_IN
    assert (case["customer_id"], case["source"]) == (OWN, "demo")
    assert "queue_key" not in case


def test_a_case_is_filed_with_the_facts_only_this_tool_reads(
    stores: HandoffStores,
) -> None:
    arguments = file_input()

    output = run(stores, arguments)

    assert output["status"] == "filed"
    assert re.fullmatch(
        r"[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}", output["reference"]
    )
    assert (output["queue"], output["priority"], output["flagged"]) == (
        "dispute_intake",
        "normal",
        False,
    )
    call_id = arguments["call_id"]
    assert output["added_facts"] == [
        {
            "subject": "customer",
            "id": OWN,
            "field": "customer_status",
            "value": "Active",
            "evidence": call_id,
        },
        {
            "subject": "transaction",
            "id": "TRX-EXAMPLE0000000000003",
            "field": "is_fraud",
            "value": False,
            "evidence": call_id,
        },
    ]
    held = stored(stores)
    case = held[arguments["payload"]["handoff_id"]]
    assert held[f"REF#{output['reference']}"]["handoff_id"] == case["handoff_id"]
    assert case["reference"] == output["reference"]
    assert case["queue_key"] == "demo#dispute_intake#filed"
    assert case["queue_order"] == f"0#{case['filed_at']}"
    assert case["record"] == {"sign_in": SIGN_IN, "turns": arguments["turns"]}
    assert all(f in case["payload"]["verified_facts"] for f in output["added_facts"])
    own = [e for e in case["payload"]["evidence"] if e["call_id"] == call_id]
    assert [e["tool"] for e in own] == ["file_handoff"]


def test_its_own_call_joins_the_evidence_when_the_payload_lacks_it(
    stores: HandoffStores,
) -> None:
    arguments = file_input()
    evidence = arguments["payload"]["evidence"]
    evidence[:] = [e for e in evidence if e["call_id"] != arguments["call_id"]]

    run(stores, arguments)

    case = stored(stores)[arguments["payload"]["handoff_id"]]
    assert case["payload"]["evidence"][-1] == {
        "call_id": arguments["call_id"],
        "tool": "file_handoff",
        "called_at": "2026-10-02T15:42:08.512Z",
        "outcome": "ok",
    }


def test_a_retry_files_nothing_and_returns_the_first_reference(
    stores: HandoffStores,
) -> None:
    first = run(stores, file_input())
    before = copy.deepcopy(stored(stores))

    again = run(stores, file_input())

    assert again["status"] == "already_filed"
    assert again["reference"] == first["reference"]
    assert again["added_facts"] == []
    assert stored(stores) == before


def test_a_draft_is_filed_in_place_and_a_late_draft_changes_nothing(
    stores: HandoffStores,
) -> None:
    run(stores, draft_input())
    saved = stored(stores)[draft_input()["draft"]["handoff_id"]]["saved_at"]

    filed = run(stores, file_input())
    late = run(stores, draft_input())

    case = stored(stores)[draft_input()["draft"]["handoff_id"]]
    assert (case["status"], case["saved_at"], case["reference"]) == (
        "filed",
        saved,
        filed["reference"],
    )
    assert "draft" not in case
    assert late["status"] == "draft_saved"


@pytest.mark.parametrize(
    ("token", "change"),
    [
        ("other-token", {}),
        ("customer-token", {"customer_id": OTHER}),
        ("customer-token", {"origin_jti": "c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f"}),
    ],
)
def test_nothing_is_filed_for_anyone_but_the_tokens_customer_and_sign_in(
    stores: HandoffStores, token: str, change: dict[str, str]
) -> None:
    for arguments in (draft_input(), file_input()):
        output = run(stores, {**arguments, **change}, token)

        assert output == {"outcome": "refused", "refusal": "customer_mismatch"}
    assert stored(stores) == {}


@pytest.mark.parametrize(
    "change",
    [
        {"customer_id": OTHER},
        {"session_id": "c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f"},
    ],
)
def test_a_payload_naming_another_customer_or_sign_in_is_refused(
    stores: HandoffStores, change: dict[str, str]
) -> None:
    arguments = file_input()
    arguments["payload"] |= change

    output = run(stores, arguments)

    assert output == {"outcome": "refused", "refusal": "customer_mismatch"}
    assert stored(stores) == {}


def test_another_customers_handoff_id_is_never_taken_over(
    stores: HandoffStores,
) -> None:
    theirs = draft_input()
    theirs |= {"customer_id": OTHER, "origin_jti": TOKENS["other-token"].origin_jti}
    run(stores, theirs, "other-token")

    mine = run(stores, file_input())
    draft = run(stores, draft_input())

    assert mine == draft == {"outcome": "refused", "refusal": "customer_mismatch"}
    (case,) = stored(stores).values()
    assert (case["customer_id"], case["status"]) == (OTHER, "draft")


def test_a_token_that_isnt_a_customers_is_refused_before_the_input_is_read(
    stores: HandoffStores,
) -> None:
    for event in (
        {"token": "forged", "input": file_input()},
        {"token": "forged", "input": {"mode": "file"}},
        {"input": file_input()},
        None,
    ):
        assert answer(event, lambda: stores, lambda: NOW) == {
            "outcome": "refused",
            "refusal": "token_invalid",
        }
    assert stored(stores) == {}


def test_an_input_that_fails_the_contract_names_the_rule_not_the_value(
    stores: HandoffStores,
) -> None:
    arguments = {**file_input(), "customer_id": DIGITS}

    output = run(stores, arguments)

    assert output["outcome"] == "invalid_input"
    assert DIGITS not in json.dumps(output)


def test_facts_only_this_tool_may_state_are_left_out_when_they_arrive(
    stores: HandoffStores,
) -> None:
    arguments = file_input()
    index = len(arguments["payload"]["verified_facts"])
    forged = {
        "subject": "transaction",
        "id": "TRX-EXAMPLE0000000000003",
        "field": "is_fraud",
        "value": True,
        "evidence": arguments["call_id"],
    }
    arguments["payload"]["verified_facts"].append(forged)

    output = run(stores, arguments)

    assert output["flagged"] is True
    reserved = {"path": f"/verified_facts/{index}", "rule": "reserved"}
    assert reserved in output["validation_errors"]
    case = stored(stores)[arguments["payload"]["handoff_id"]]
    flags = [f for f in case["payload"]["verified_facts"] if f["field"] == "is_fraud"]
    assert [f["value"] for f in flags] == [False]


def test_text_that_could_be_a_card_number_is_replaced_or_left_out_and_never_stored(
    stores: HandoffStores,
) -> None:
    arguments = file_input()
    arguments["payload"]["request"]["summary"] = f"Tarjeta {DIGITS}"
    arguments["payload"]["customer_statements"].append(f"Mi número es {DIGITS}.")

    output = run(stores, arguments)

    assert output["flagged"] is True
    assert {e["path"] for e in output["validation_errors"]} == {
        "/request/summary",
        "/customer_statements/1",
    }
    case = stored(stores)[arguments["payload"]["handoff_id"]]
    assert case["payload"]["request"]["summary"] == (
        "El cliente no reconoce un cargo en su tarjeta."
    )
    assert len(case["payload"]["customer_statements"]) == 1
    assert DIGITS.replace(" ", "") not in json.dumps(case).replace(" ", "")


def test_a_payload_pruning_cant_mend_is_refused_as_invalid(
    stores: HandoffStores,
) -> None:
    arguments = file_input()
    arguments["payload"]["queue"] = "customer_service"

    output = run(stores, arguments)

    assert output == {
        "outcome": "invalid_input",
        "errors": [{"path": "/payload/queue", "rule": "const"}],
    }
    assert stored(stores) == {}


def test_an_evaluation_token_files_outside_the_demos_queue(
    stores: HandoffStores,
) -> None:
    run(stores, file_input(), "evaluation-token")

    case = stored(stores)[file_input()["payload"]["handoff_id"]]
    assert (case["source"], case["queue_key"]) == (
        "evaluation",
        "evaluation#dispute_intake#filed",
    )


def test_a_reference_already_taken_is_drawn_again(
    stores: HandoffStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawn = iter(["7K2M-9QXA", "7K2M-9QXA", "Q4TR-8B2N"])
    monkeypatch.setattr("faro.tools.file_handoff.draw_reference", lambda: next(drawn))
    other = file_input()
    other["payload"]["handoff_id"] = "e3a91f6c-4b28-4d7e-8f05-c61b2d9a7e34"
    run(stores, other)

    output = run(stores, file_input())

    assert output["reference"] == "Q4TR-8B2N"
    assert {k for k in stored(stores) if k.startswith("REF#")} == {
        "REF#7K2M-9QXA",
        "REF#Q4TR-8B2N",
    }


def test_the_handler_files_through_its_stores(
    stores: HandoffStores, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handoff, "stores", lambda: stores)

    output = handler({"token": "customer-token", "input": draft_input()}, None)

    assert output["status"] == "draft_saved"


def test_a_priority_the_payload_shows_should_be_urgent_is_raised(
    stores: HandoffStores,
) -> None:
    arguments = file_input()
    action = arguments["payload"]["actions"][0]
    action |= {"outcome": "declined_by_customer", "confirmed_at": None, "evidence": []}
    arguments["payload"]["verified_facts"] = [
        f for f in arguments["payload"]["verified_facts"] if f["subject"] != "card"
    ]

    output = run(stores, arguments)

    assert output["priority"] == "urgent"
    assert {"path": "/priority", "rule": "policy"} in output["validation_errors"]
    case = stored(stores)[arguments["payload"]["handoff_id"]]
    assert case["priority"] == "urgent"
    # The queue sorts the raised priority, not the one the payload arrived with.
    assert case["queue_order"] == f"1#{case['filed_at']}"


RECORDED = "TRX-EXAMPLE0000000000003"
FIXTURE = "TRX-FIXTURE0000000000004"


def as_fixture(node: Any) -> Any:
    """
    The example handoff and the calls it cites, about a fixture transaction in place of the export's.
    """
    return json.loads(json.dumps(node).replace(RECORDED, FIXTURE))


def fixture_stores(sign_in: str) -> HandoffStores:
    items = example_items()
    fixture = examples("overlay.fixture_transaction.json")[0]
    fixture.update(
        sign_in=sign_in,
        item=f"FIXTURE#TRX#{FIXTURE}",
        transaction_id=FIXTURE,
        listed_at=f"{fixture['transaction_date']}#{FIXTURE}",
        is_fraud=True,
    )
    return HandoffStores(
        data=MemoryData(items),
        flags=MemoryFlags(items),
        cases=MemoryCases(),
        verifier=Tokens(),
        records=MemoryRecords(as_fixture(recorded_example())),
        fixtures=MemoryOverlay([fixture]),
    )


def test_a_handoff_about_a_fixture_passes_the_check_and_carries_its_flag() -> None:
    arguments = as_fixture(file_input())

    output = run(fixture_stores(SIGN_IN), arguments)

    assert (output["status"], output["flagged"]) == ("filed", False)
    assert output["validation_errors"] == []
    assert {
        "subject": "transaction",
        "id": FIXTURE,
        "field": "is_fraud",
        "value": True,
        "evidence": arguments["call_id"],
    } in output["added_facts"]


def test_another_sign_ins_fixture_adds_no_flag() -> None:
    output = run(
        fixture_stores(TOKENS["other-token"].origin_jti), as_fixture(file_input())
    )

    assert output["status"] == "filed"
    assert not [f for f in output["added_facts"] if f["field"] == "is_fraud"]
