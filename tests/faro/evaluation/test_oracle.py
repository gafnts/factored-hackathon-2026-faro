"""
The oracle on the bank: each situation of ADR-0004's table and of ADR-0005's flows comes out with the outcome, the wait,
the tools, the facts, and the handoff the policy gives it, and agrees with get_available_credit's contract examples.
"""

import json
from collections.abc import Iterator
from dataclasses import replace
from importlib.resources import files
from typing import Any

import duckdb
import pytest

from faro.evaluation import bronze, cases, families, oracle, state
from faro.evaluation.facts import contract_words

from .bank import EXAMPLE_CUSTOMER, Bank, fixture_card, fixture_transaction

pytestmark = pytest.mark.xdist_group("evaluation_bank")

FAMILIES = families.load()


@pytest.fixture(scope="module")
def con(bank: Bank) -> Iterator[duckdb.DuckDBPyConnection]:
    with bronze.connect(bank.database, "development") as con:
        yield con


def case(
    customer_id: str,
    *messages: str,
    answers: dict[str, str] | None = None,
    means: dict[str, str] | None = None,
    slots: dict[str, str] | None = None,
    language: str = "es",
) -> dict[str, Any]:
    return {
        "version": 1,
        "case_id": cases.case_id("oracle", 0, 0),
        "set": "oracle",
        "side": "development",
        "group": "reads",
        "situation": "test",
        "source": "natural",
        "language": language,
        "customer_id": customer_id,
        "family_id": messages[0],
        "script": {
            "messages": [{"id": f"{m}/{language}/0", "text": "-"} for m in messages],
            "answers": {
                k: {"id": f"{v}/{language}", "text": "-"} if "-" in v else v
                for k, v in (answers or {}).items()
            },
            "means": means or {},
            "slots": slots or {},
        },
        "fixtures": [],
        "faults": [],
    }


def play(con: duckdb.DuckDBPyConnection, played: dict[str, Any]) -> dict[str, Any]:
    customer = state.read(con, played["customer_id"])
    assert customer is not None
    expected = oracle.expect(customer, played, FAMILIES, contract_words())
    assert cases.problems({**played, "expected": expected}) == []
    return expected


def path(
    expected: dict[str, Any],
) -> list[tuple[str, list[tuple[str | None, str]], str]]:
    return [
        (
            t["sends"],
            [(d["request_label"], d["outcome_class"]) for d in t["decisions"]],
            t["awaiting"],
        )
        for t in expected["turns"]
    ]


def test_a_status_asks_which_card_then_answers_it(
    con: duckdb.DuckDBPyConnection,
) -> None:
    expected = play(
        con,
        case(
            EXAMPLE_CUSTOMER,
            "card_status-01",
            answers={"card": "card_last_four-01"},
            means={"product_id": "PRD-EXAMPLE00008"},
        ),
    )

    assert path(expected) == [
        ("message", [("card_status", "clarify")], "card"),
        ("card", [("card_status", "answer")], "none"),
    ]
    first, last = expected["turns"]
    assert first["tools_required"] == ["list_cards"]
    # The question lists the cards without their statuses (POL-14); the all-cards answer carries them.
    assert first["facts"]["{card_list}"].count("\n") == 4
    assert "{cards}" not in first["facts"]
    assert ":" not in first["facts"]["{card_list}"]
    assert last["facts"] == {
        "{card}": "tarjeta de crédito terminada en 6610",
        "{card.status}": "activa",
        "{card.expiration}": "02/2029",
    }
    assert last["tools_forbidden"] == ["block_card", "file_handoff"]


def test_a_message_that_doesnt_answer_which_card_gets_the_question_again(
    con: duckdb.DuckDBPyConnection,
) -> None:
    # POL-06, version 6: it counts toward no limit (POL-17), so the answer after it is still the first.
    expected = play(
        con,
        case(
            EXAMPLE_CUSTOMER,
            "card_status-01",
            answers={"aside": "aside-01", "card": "card_last_four-01"},
            means={"product_id": "PRD-EXAMPLE00008"},
        ),
    )

    assert path(expected) == [
        ("message", [("card_status", "clarify")], "card"),
        ("aside", [("card_status", "clarify")], "card"),
        ("card", [("card_status", "answer")], "none"),
    ]
    first, again, _ = expected["turns"]
    assert again["facts"] == first["facts"]
    assert "POL-06" in again["decisions"][0]["rules"]


@pytest.mark.parametrize(
    ("card_id", "outcome", "fact"),
    [
        ("PRD-EXAMPLE00008", "answer", ("{credit.available}", "available_credit")),
        ("PRD-EXAMPLE00009", "answer", ("{credit.over_by}", "over_limit_by")),
        ("PRD-EXAMPLE00002", "abstain", None),
        ("PRD-EXAMPLE00005", "decline", None),
        ("PRD-EXAMPLE00007", "decline", None),
    ],
)
def test_available_credit_agrees_with_the_tools_examples(
    con: duckdb.DuckDBPyConnection,
    card_id: str,
    outcome: str,
    fact: tuple[str, str] | None,
) -> None:
    examples = json.loads(
        files("faro.contracts")
        .joinpath("examples/tools.get_available_credit_output.json")
        .read_text(encoding="utf-8")
    )
    [tool] = [
        e["card"] for e in examples if e.get("card", {}).get("card_id") == card_id
    ]
    customer = state.read(con, EXAMPLE_CUSTOMER)
    assert customer is not None
    last_four = customer.card(card_id).last_four

    expected = play(
        con,
        case(
            EXAMPLE_CUSTOMER,
            "available_credit-03",
            slots={"last_four": last_four},
            answers={"handoff_control": "decline"},
        ),
    )

    first = expected["turns"][0]
    assert first["decisions"][0]["outcome_class"] == outcome
    assert first["facts"]["{card}"].endswith(tool["last_four"])
    if fact is not None:
        placeholder, field = fact
        assert first["facts"][placeholder] == f"{tool[field]:,.2f} USD"
        assert first["facts"]["{as_of}"] == "17/06/2026"
    if tool["availability"] == "no_limit":
        assert first["awaiting"] == "handoff_control"
    if tool["availability"] == "not_active":
        assert first["facts"]["{card.status}"] == "cerrada"


def test_a_missing_limit_offers_a_handoff_the_customer_can_accept(
    con: duckdb.DuckDBPyConnection,
) -> None:
    expected = play(
        con,
        case(
            "CLI-EVAL00000006",
            "available_credit-04",
            answers={"handoff_control": "accept"},
        ),
    )

    assert path(expected) == [
        ("message", [("available_credit", "abstain")], "handoff_control"),
        ("accept", [("available_credit", "hand_off")], "none"),
    ]
    assert expected["turns"][1]["handoff"] == {
        "reason_code": "missing_data",
        "trigger": "accepted_offer",
        "queue": "customer_service",
        "priority": "normal",
    }


def test_a_page_then_the_next(con: duckdb.DuckDBPyConnection) -> None:
    expected = play(
        con,
        case("CLI-EVAL00000006", "recent_transactions-01", "recent_transactions-04"),
    )

    first, second = (
        t["facts"]["{transactions}"].splitlines() for t in expected["turns"]
    )
    assert (len(first), len(second)) == (10, 4)
    assert (
        first[0]
        == "- 16/06/2026 12:00 · Compra · Panadería Ejemplo · 1.500,00 ARS · Aprobada"
    )
    assert expected["turns"][0]["facts"]["{window.from}"] == "20/03/2026 06:00"


def test_the_expected_language_and_its_facts_follow_the_conversation_turn_by_turn(
    con: duckdb.DuckDBPyConnection,
) -> None:
    played = case(
        EXAMPLE_CUSTOMER,
        "recent_transactions-07",
        answers={"card": "card_last_four-01"},
        means={"product_id": "PRD-EXAMPLE00008"},
        language="pt",
    )
    # The opener is a word both languages share, which the family marks, so Spanish holds until the answer (POL-50).
    played["script"]["messages"][0]["id"] = "recent_transactions-07/pt/2"

    first, last = play(con, played)["turns"]

    assert (first["language"], last["language"]) == ("es", "pt")
    assert "Tarjeta de" in first["facts"]["{card_list}"]
    assert "Cartão" not in first["facts"]["{card_list}"]
    assert last["facts"]["{card}"].startswith("cartão de crédito final ")

    clear = case(
        EXAMPLE_CUSTOMER,
        "recent_transactions-07",
        answers={"card": "card_last_four-01"},
        means={"product_id": "PRD-EXAMPLE00008"},
        language="pt",
    )

    first, _ = play(con, clear)["turns"]

    assert "Cartão de" in first["facts"]["{card_list}"]


def test_the_reasons_answer_sets_the_language_of_the_confirm_prompt(
    con: duckdb.DuckDBPyConnection,
) -> None:
    played = case(
        "CLI-EVAL00000007",
        "block_card-07",
        answers={"reason": "reason_lost-01", "confirm_control": "confirm"},
        language="pt",
    )
    played["script"]["messages"][0]["id"] = "block_card-07/pt/2"

    turns = play(con, played)["turns"]

    asked = next(t for t in turns if t["awaiting"] == "reason")
    shown = turns[turns.index(asked) + 1]
    assert asked["facts"]["{card}"].startswith("tarjeta de ")
    assert shown["awaiting"] == "confirm_control"
    assert shown["facts"]["{card}"].startswith("cartão de ")


def test_a_card_with_no_transactions_in_the_window(
    con: duckdb.DuckDBPyConnection,
) -> None:
    expected = play(con, case("CLI-EVAL00000007", "recent_transactions-07"))

    [turn] = expected["turns"]
    assert "{transactions}" not in turn["facts"]
    assert turn["facts"]["{window.to}"] == "18/06/2026 06:00"


def test_a_masked_merchant_and_a_charge_abroad_in_a_page(
    con: duckdb.DuckDBPyConnection,
) -> None:
    expected = play(
        con,
        case(
            "CLI-EVAL00000005",
            "recent_transactions-01",
            slots={},
            answers={"card": "card_last_four-01"},
            means={"product_id": "PRD-EVAL00000502"},
        ),
    )

    page = expected["turns"][-1]["facts"]["{transactions}"]
    assert "Pago ****9876 Ejemplo" in page
    assert "4111" not in page
    assert "Gasolinera Ejemplo · 120.00 USD · Aprobada\n" in page + "\n"


def test_a_decline_with_a_listed_code_is_explained(
    con: duckdb.DuckDBPyConnection,
) -> None:
    [turn] = play(con, case("CLI-EVAL00000003", "decline_reason-01"))["turns"]

    assert turn["decisions"] == [
        {
            "request_label": "decline_reason",
            "outcome_class": "answer",
            "rules": ["POL-02", "POL-27", "POL-29"],
        }
    ]
    assert (
        turn["facts"]["{transaction}"]
        == "- 12/06/2026 10:05, Electro Ejemplo, 920.000,00 COP"
    )
    assert turn["facts"]["{transaction.meaning}"] == "- Motivo: fondos insuficientes"


def test_a_decline_with_no_code_abstains(con: duckdb.DuckDBPyConnection) -> None:
    expected = play(
        con,
        case(
            "CLI-EVAL00000006",
            "decline_reason-08",
            answers={"handoff_control": "decline"},
        ),
    )

    assert path(expected) == [
        ("message", [("decline_reason", "abstain")], "handoff_control"),
        ("decline", [("decline_reason", "answer")], "none"),
    ]


def test_several_declines_are_listed_to_choose_from(
    con: duckdb.DuckDBPyConnection,
) -> None:
    customer = state.read(con, "CLI-EVAL00000015")
    assert customer is not None
    newest = customer.cards[0].transactions[0]

    expected = play(
        con,
        case(
            "CLI-EVAL00000015",
            "decline_reason-01",
            answers={"transaction": "transaction_newest-01"},
            means={
                "product_id": newest.product_id,
                "transaction_id": newest.transaction_id,
            },
        ),
    )

    assert path(expected)[0] == (
        "message",
        [("decline_reason", "clarify")],
        "transaction",
    )
    assert expected["turns"][0]["facts"]["{transactions}"].count("\n") == 4


def test_a_lost_card_is_blocked_with_the_control_and_a_replacement_offered(
    con: duckdb.DuckDBPyConnection,
) -> None:
    expected = play(
        con,
        case(
            "CLI-EVAL00000003",
            "block_card-02",
            answers={
                "typed_yes": "typed_yes-01",
                "confirm_control": "confirm",
                "handoff_control": "accept",
            },
        ),
    )

    assert path(expected) == [
        ("message", [("block_card", "block")], "confirm_control"),
        ("typed_yes", [("block_card", "block")], "confirm_control"),
        ("confirm", [("block_card", "block")], "handoff_control"),
        ("accept", [("block_card", "hand_off")], "none"),
    ]
    assert expected["turns"][1]["tools_forbidden"] == ["block_card", "file_handoff"]
    assert expected["turns"][2]["tools_required"] == ["block_card"]
    assert expected["turns"][3]["handoff"]["reason_code"] == "unsupported_request"
    assert expected["blocked"] == ["PRD-EVAL00000301"]


def test_a_block_among_several_active_cards_asks_which_then_why(
    con: duckdb.DuckDBPyConnection,
) -> None:
    expected = play(
        con,
        case(
            "CLI-EVAL00000005",
            "block_card-01",
            answers={
                "card": "card_last_four-01",
                "reason": "reason_customer_request-01",
                "confirm_control": "confirm",
            },
            means={"product_id": "PRD-EVAL00000502"},
        ),
    )

    assert path(expected) == [
        ("message", [("block_card", "clarify")], "card"),
        ("card", [("block_card", "clarify")], "reason"),
        ("reason", [("block_card", "block")], "confirm_control"),
        ("confirm", [("block_card", "block")], "none"),
    ]
    first, second = expected["turns"][:2]
    assert first["facts"]["{card_list}"] == "\n".join(
        [
            "- Tarjeta de crédito terminada en 4417",
            "- Tarjeta de crédito terminada en 7302",
            "- Tarjeta de débito terminada en 6650",
        ]
    )
    assert second["facts"]["{card}"] == "tarjeta de crédito terminada en 4417"
    assert expected["blocked"] == ["PRD-EVAL00000502"]


def test_a_cancelled_block_leaves_the_card(con: duckdb.DuckDBPyConnection) -> None:
    expected = play(
        con,
        case(
            "CLI-EVAL00000003",
            "block_card-01",
            answers={
                "reason": "reason_customer_request-01",
                "confirm_control": "cancel",
            },
        ),
    )

    assert path(expected) == [
        ("message", [("block_card", "clarify")], "reason"),
        ("reason", [("block_card", "block")], "confirm_control"),
        ("cancel", [("block_card", "answer")], "none"),
    ]
    assert expected["blocked"] == []


def test_an_unrecognized_charge_from_yesterday_is_found_blocked_and_handed_off(
    con: duckdb.DuckDBPyConnection,
) -> None:
    expected = play(
        con,
        case(
            "CLI-EVAL00000003",
            "unrecognized_charge-05",
            answers={"confirm_control": "confirm"},
        ),
    )

    first, last = expected["turns"]
    assert first["facts"]["{transaction}"].startswith("- 16/06/2026 19:20")
    assert last["handoff"] == {
        "reason_code": "unrecognized_charge",
        "trigger": "required",
        "queue": "dispute_intake",
        "priority": "normal",
    }
    assert last["tools_required"] == ["block_card", "file_handoff"]


def test_today_is_the_business_date_not_the_as_of_instants_day(
    con: duckdb.DuckDBPyConnection,
) -> None:
    [turn] = play(con, case("CLI-EVAL00000003", "decline_reason-07"))["turns"]

    assert turn["facts"]["{transaction}"].startswith("- 17/06/2026 11:15")
    assert turn["facts"]["{transaction.status}"] == "aprobada"


@pytest.mark.parametrize(
    ("ending", "outcome", "priority", "blocked"),
    [
        ("cancel", "declined", "urgent", []),
        ("confirm", "verified", "normal", ["PRD-EVAL00000301"]),
        ("confirm", "failed", "urgent", []),
    ],
)
def test_a_block_for_an_unrecognized_charge_ends_in_dispute_intake_however_it_ends(
    con: duckdb.DuckDBPyConnection,
    ending: str,
    outcome: str,
    priority: str,
    blocked: list[str],
) -> None:
    played = case(
        "CLI-EVAL00000003",
        "block_card-01",
        answers={
            "reason": "reason_unrecognized_charge-01",
            "confirm_control": ending,
        },
    )
    if outcome == "failed":
        played = faulted(played, "block_card", 3)

    expected = play(con, played)

    assert path(expected) == [
        ("message", [("block_card", "clarify")], "reason"),
        ("reason", [("block_card", "block")], "confirm_control"),
        (ending, [("block_card", "hand_off")], "none"),
    ]
    last = expected["turns"][-1]
    assert last["handoff"] == {
        "reason_code": "unrecognized_charge",
        "trigger": "required",
        "queue": "dispute_intake",
        "priority": priority,
    }
    assert last["tools_required"] == (
        ["file_handoff"] if ending == "cancel" else ["block_card", "file_handoff"]
    )
    assert "POL-39" in last["decisions"][0]["rules"]
    assert expected["blocked"] == blocked


def test_a_cancelled_charge_block_is_handed_off_urgently(
    con: duckdb.DuckDBPyConnection,
) -> None:
    expected = play(
        con,
        case(
            "CLI-EVAL00000003",
            "unrecognized_charge-11",
            slots={"merchant": "Mercado Ejemplo"},
            answers={"confirm_control": "cancel"},
        ),
    )

    assert expected["turns"][-1]["handoff"]["priority"] == "urgent"
    assert expected["blocked"] == []


@pytest.mark.parametrize(
    ("customer_id", "family", "outcome", "reason_code"),
    [
        ("CLI-EVAL00000003", "talk_to_human-01", "hand_off", "customer_request"),
        ("CLI-EVAL00000003", "talk_to_human-05", "hand_off", "complaint"),
        ("CLI-EVAL00000003", "unsupported-01", "hand_off", "unblock_request"),
        ("CLI-EVAL00000008", "card_status-01", "hand_off", "customer_not_active"),
        ("CLI-EVAL00000003", "unsupported-06", "decline", None),
        ("CLI-EVAL00000003", "none-10", "decline", None),
        ("CLI-EVAL00000003", "none-01", "answer", None),
    ],
)
def test_requests_that_end_in_one_turn(
    con: duckdb.DuckDBPyConnection,
    customer_id: str,
    family: str,
    outcome: str,
    reason_code: str | None,
) -> None:
    [turn] = play(con, case(customer_id, family))["turns"]

    assert [d["outcome_class"] for d in turn["decisions"]] == [outcome]
    assert turn.get("handoff", {}).get("reason_code") == reason_code


def test_a_status_the_policy_withholds_is_withheld_unless_a_card_shares_it(
    con: duckdb.DuckDBPyConnection,
) -> None:
    [suspended] = play(con, case("CLI-EVAL00000008", "card_status-01"))["turns"]
    [served] = play(con, case("CLI-EVAL00000003", "card_status-01"))["turns"]
    customer = state.read(con, "CLI-EVAL00000008")
    assert customer is not None
    card = replace(customer.cards[0], status="Suspended")
    [sharing] = oracle.expect(
        replace(customer, cards=(card,)),
        case("CLI-EVAL00000008", "card_status-01"),
        FAMILIES,
        contract_words(),
    )["turns"]

    assert suspended["withheld"] == list(oracle.WITHHELD["Suspended"])
    assert served["withheld"] == []
    assert sharing["withheld"] == []


def test_a_message_with_no_request_or_another_language_has_no_label(
    con: duckdb.DuckDBPyConnection,
) -> None:
    for family in ("none-01", "none-10"):
        [turn] = play(con, case("CLI-EVAL00000003", family))["turns"]
        assert turn["decisions"][0]["request_label"] is None


def test_two_requests_are_served_in_the_policys_order(
    con: duckdb.DuckDBPyConnection,
) -> None:
    [turn] = play(con, case("CLI-EVAL00000003", "recent_transactions-10"))["turns"]

    assert [d["request_label"] for d in turn["decisions"]] == [
        "card_status",
        "recent_transactions",
    ]


def test_last_four_shared_by_two_types_asks_for_the_type(
    con: duckdb.DuckDBPyConnection,
) -> None:
    expected = play(
        con,
        case(
            "CLI-EVAL00000010",
            "card_status-04",
            slots={"last_four": "5150"},
        ),
    )

    [turn] = expected["turns"]
    assert turn["facts"]["{card}"] == "tarjeta de débito terminada en 5150"


def faulted(played: dict[str, Any], tool: str, failures: int) -> dict[str, Any]:
    return {
        **played,
        "group": "tool_failures",
        "source": "harness",
        "faults": [{"tool": tool, "failures": failures, "error": "timeout"}],
    }


@pytest.mark.parametrize(
    ("tool", "family"),
    [
        ("list_cards", "recent_transactions-01"),
        ("get_card", "card_status-01"),
        ("get_available_credit", "available_credit-04"),
        ("find_transactions", "recent_transactions-01"),
        ("find_transactions", "decline_reason-01"),
    ],
)
def test_a_read_that_fails_every_attempt_offers_a_person(
    con: duckdb.DuckDBPyConnection, tool: str, family: str
) -> None:
    played = case("CLI-EVAL00000003", family, answers={"handoff_control": "accept"})
    expected = play(con, faulted(played, tool, 3))

    label = family.rsplit("-", 1)[0]
    assert path(expected) == [
        ("message", [(label, "abstain")], "handoff_control"),
        ("accept", [(label, "hand_off")], "none"),
    ]
    first, last = expected["turns"]
    assert first["tools_required"] == [tool]
    assert first["facts"] == {}
    assert last["handoff"] == {
        "reason_code": "tool_failure",
        "trigger": "accepted_offer",
        "queue": "customer_service",
        "priority": "normal",
    }
    assert "POL-48" in expected["rules"]


@pytest.mark.parametrize("failures", [1, 2])
@pytest.mark.parametrize(
    ("tool", "family"),
    [
        ("list_cards", "available_credit-04"),
        ("get_available_credit", "available_credit-04"),
        ("find_transactions", "recent_transactions-01"),
    ],
)
def test_a_read_that_recovers_within_its_retries_takes_its_path(
    con: duckdb.DuckDBPyConnection, tool: str, family: str, failures: int
) -> None:
    played = case("CLI-EVAL00000003", family)

    assert play(con, faulted(played, tool, failures)) == play(con, played)


@pytest.mark.parametrize(
    ("customer_id", "family", "priority"),
    [
        ("CLI-EVAL00000003", "block_card-02", "urgent"),
        ("CLI-EVAL00000007", "block_card-05", "normal"),
    ],
)
def test_a_block_that_fails_every_attempt_is_handed_off_unverified(
    con: duckdb.DuckDBPyConnection, customer_id: str, family: str, priority: str
) -> None:
    played = case(customer_id, family, answers={"confirm_control": "confirm"})
    expected = play(con, faulted(played, "block_card", 3))

    assert path(expected) == [
        ("message", [("block_card", "block")], "confirm_control"),
        ("confirm", [("block_card", "hand_off")], "none"),
    ]
    last = expected["turns"][-1]
    assert last["tools_required"] == ["block_card", "file_handoff"]
    assert last["handoff"] == {
        "reason_code": "action_not_verified",
        "trigger": "required",
        "queue": "customer_service",
        "priority": priority,
    }
    assert expected["blocked"] == []


def test_a_fixture_card_sharing_type_and_last_four_is_handed_off(
    con: duckdb.DuckDBPyConnection,
) -> None:
    played = case("CLI-EVAL00000007", "card_status-04", slots={"last_four": "3318"})
    played["fixtures"] = [fixture_card("CLI-EVAL00000007", 1)]

    [turn] = play(con, played)["turns"]
    assert [d["outcome_class"] for d in turn["decisions"]] == ["hand_off"]
    assert turn["handoff"]["reason_code"] == "ambiguous_card"


def test_a_fixture_decline_with_an_unlisted_code_abstains(
    con: duckdb.DuckDBPyConnection,
) -> None:
    played = case(
        "CLI-EVAL00000007",
        "decline_reason-01",
        answers={"handoff_control": "decline"},
    )
    played["fixtures"] = [
        fixture_transaction(
            "CLI-EVAL00000007",
            "PRD-EVAL00000701",
            1,
            "2026-06-16 18:22:05",
            transaction_status="Declined",
            response_code="61",
        )
    ]
    expected = play(con, played)

    assert path(expected) == [
        ("message", [("decline_reason", "abstain")], "handoff_control"),
        ("decline", [("decline_reason", "answer")], "none"),
    ]
    assert "POL-32" in expected["turns"][0]["decisions"][0]["rules"]


@pytest.mark.parametrize(
    ("family", "fault"),
    [
        # Past the tool's first call.
        ("card_status-01", {"tool": "get_card", "failures": 4}),
        # Never reached.
        ("card_status-01", {"tool": "block_card", "failures": 3}),
        # Outside one card request.
        ("talk_to_human-01", {"tool": "list_cards", "failures": 3}),
        # A charge's search, whose failure goes into its handoff.
        ("unrecognized_charge-01", {"tool": "find_transactions", "failures": 3}),
    ],
)
def test_unasked_situations_are_refused_rather_than_guessed(
    con: duckdb.DuckDBPyConnection, family: str, fault: dict[str, Any]
) -> None:
    with pytest.raises(oracle.NotCoveredError):
        play(con, case("CLI-EVAL00000003", "available_credit-05"))
    faulty = case("CLI-EVAL00000003", family)
    faulty["faults"] = [{**fault, "error": "timeout"}]
    with pytest.raises(oracle.NotCoveredError):
        play(con, faulty)
