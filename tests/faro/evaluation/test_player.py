"""
The in-process player on the bank: a case plays through the entrypoint and the graph with the scripted models, its
evidence holds what the harness keeps, Cedar's rule holds on every tool call, and a case the player can't play as
written is its own error.
"""

import asyncio
import copy
import json
from typing import Any

import httpx
import pytest
from langchain_core.messages import HumanMessage

from faro.agent.check import failures
from faro.agent.nodes.replies import SHAPES
from faro.agent.texts import FIXED, placeholders
from faro.evaluation import bronze, families, generator, player
from faro.evaluation.facts import contract_words
from faro.evaluation.scripted import ScriptedModels, laid_out, listed

from .bank import Bank

pytestmark = pytest.mark.xdist_group("evaluation_bank")

LOADED, ANSWERS = families.load(), families.load_answers()
BY_FAMILY = {f.family_id: f for f in LOADED}
BY_ANSWER = {a.answer_id: a for a in ANSWERS}


@pytest.fixture(scope="module")
def drawn(bank: Bank) -> list[dict[str, Any]]:
    held = families.held_out_ids(LOADED, ANSWERS)
    with bronze.connect(bank.database, "development") as con:
        drawing = generator.Generator(
            con, "development", LOADED, ANSWERS, held, contract_words(), reuse=True
        )
        return drawing.draw("regression", 7).cases


def items(bank: Bank, case: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in bank.items if i["pk"] in ("META", case["customer_id"])]


def play(bank: Bank, case: dict[str, Any]) -> dict[str, Any]:
    held = items(bank, case)
    models = ScriptedModels(case, BY_FAMILY, BY_ANSWER, held)
    return asyncio.run(player.play(case, held, models))


def situation(drawn: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(c for c in drawn if c["situation"] == name)


def decisions(evidence: dict[str, Any]) -> list[tuple[str | None, str, str]]:
    return [
        (e["request_label"], e["outcome_class"], e["awaiting"])
        for e in evidence["record"]
        if e["kind"] == "decision"
    ]


def expected(case: dict[str, Any]) -> list[tuple[str | None, str, str]]:
    return [
        (
            d["request_label"],
            d["outcome_class"],
            t["awaiting"] if d is t["decisions"][-1] else "none",
        )
        for t in case["expected"]["turns"]
        for d in t["decisions"]
    ]


def test_a_read_that_asks_which_card_plays_the_oracles_path(
    bank: Bank, drawn: list[dict[str, Any]]
) -> None:
    case = situation(drawn, "status.which_card")

    evidence = play(bank, case)

    assert evidence["error"] is None
    assert [t["sends"] for t in evidence["turns"]] == [
        t["sends"] for t in case["expected"]["turns"]
    ]
    assert decisions(evidence) == expected(case)
    assert {e["source"] for e in evidence["record"]} == {"evaluation"}
    assert all(
        t["events"][-1]["event"]["type"] == "RUN_FINISHED" for t in evidence["turns"]
    )


def test_a_confirmed_block_uses_the_confirmation_the_runtime_created(
    bank: Bank, drawn: list[dict[str, Any]]
) -> None:
    case = situation(drawn, "block.reason_given")

    evidence = play(bank, case)

    assert decisions(evidence) == expected(case)
    [confirmation] = evidence["sandbox"]["confirmations"]
    assert (confirmation["status"], confirmation["outcome"]) == ("consumed", "verified")
    blocked = [
        i["card_id"]
        for i in evidence["sandbox"]["overlay"]
        if i["product_status"] == "Blocked"
    ]
    assert blocked == case["expected"]["blocked"]


def test_a_handoff_is_filed_by_file_handoffs_own_code(
    bank: Bank, drawn: list[dict[str, Any]]
) -> None:
    case = situation(drawn, "person.asked")

    evidence = play(bank, case)

    assert decisions(evidence) == expected(case)
    filed = [c for c in evidence["cases"] if c.get("status") == "filed"]
    assert [c["reason_code"] for c in filed] == ["customer_request"]
    assert filed[0]["customer_id"] == case["customer_id"]


def test_cedars_rule_denies_a_call_for_another_customer_or_sign_in(
    bank: Bank, drawn: list[dict[str, Any]]
) -> None:
    case = situation(drawn, "status.one_card")
    stack = player.Stack(
        items(bank, case), ScriptedModels(case, BY_FAMILY, BY_ANSWER, items(bank, case))
    )
    who = player.SignIn(case["customer_id"])

    def call(**arguments: str) -> dict[str, Any]:
        body = {"params": {"name": "reads___list_cards", "arguments": arguments}}
        request = httpx.Request(
            "POST",
            player.GATEWAY_URL,
            json=body,
            headers={"Authorization": f"Bearer {who.token}"},
        )
        answered: dict[str, Any] = json.loads(stack.gateway(request).content)
        return answered

    own = call(customer_id=case["customer_id"], origin_jti=who.origin_jti)
    other = call(customer_id="CLI-EXAMPLE00001", origin_jti=who.origin_jti)
    elsewhere = call(customer_id=case["customer_id"], origin_jti="another-sign-in")

    assert own["result"]["isError"] is False
    assert other["error"]["code"] == elsewhere["error"]["code"] == -32002


def test_a_text_the_case_doesnt_hold_is_the_players_error(
    bank: Bank, drawn: list[dict[str, Any]]
) -> None:
    case = situation(drawn, "status.one_card")
    held = items(bank, case)
    models = ScriptedModels(case, BY_FAMILY, BY_ANSWER, held)
    changed = copy.deepcopy(case)
    changed["script"]["messages"][0]["text"] = "Un mensaje que el caso no tiene."

    evidence = asyncio.run(player.play(changed, held, models))

    assert evidence["error"] is not None
    assert evidence["error"].startswith("script:")


def test_the_scripted_models_say_the_language_a_reader_would(
    bank: Bank, drawn: list[dict[str, Any]]
) -> None:
    third = situation(drawn, "none.third_language")
    models = ScriptedModels(third, BY_FAMILY, BY_ANSWER, items(bank, third))
    text = third["script"]["messages"][0]["text"]

    said = asyncio.run(models.route([HumanMessage(text)]))

    assert said["parsed"].language == "other"

    case = copy.deepcopy(situation(drawn, "status.one_card"))
    marked = BY_FAMILY["block_card-07"].in_language("pt")[2]
    case["script"]["messages"][0] = {"id": marked.id, "text": marked.text}
    models = ScriptedModels(case, BY_FAMILY, BY_ANSWER, items(bank, case))

    said = asyncio.run(models.route([HumanMessage(marked.text)]))

    assert (marked.clear, said["parsed"].language) == (False, "unclear")


@pytest.mark.parametrize("answer", sorted(SHAPES))
def test_the_scripted_reply_lays_its_placeholders_out_as_the_prompt_asks(
    answer: str,
) -> None:
    # So a case played with the scripted models states the model's answer, not the fixed one the check falls back to.
    names = list(dict.fromkeys(placeholders(FIXED[answer]["es"])))

    written = laid_out([f"{{{name}}}" for name in names])

    assert failures(written, dict.fromkeys(names, "valor")) == []


def test_the_scripted_extraction_names_any_other_reason_as_the_model_does(
    bank: Bank, drawn: list[dict[str, Any]]
) -> None:
    case = situation(drawn, "block.typed_yes")
    models = ScriptedModels(case, BY_FAMILY, BY_ANSWER, items(bank, case))
    answer = case["script"]["answers"]["reason"]["text"]

    said = asyncio.run(models.extract([HumanMessage(answer)]))

    assert said["parsed"].block_reason == "other_reason"
    assert said["parsed"].details()["block_reason"] == "customer_request"

    told = copy.deepcopy(case)
    opener = BY_FAMILY["block_card-05"].in_language("es")[0]
    told["script"]["messages"][0] = {"id": opener.id, "text": opener.text}
    models = ScriptedModels(told, BY_FAMILY, BY_ANSWER, items(bank, told))

    said = asyncio.run(models.extract([HumanMessage(opener.text)]))

    assert said["parsed"].block_reason == "other_reason"

    evidence = play(bank, case)

    assert decisions(evidence) == expected(case)
    [confirmation] = evidence["sandbox"]["confirmations"]
    assert confirmation["reason"] == "customer_request"


def test_the_choice_reads_the_listing_whatever_the_merchants_name_holds() -> None:
    context = (
        "The customer reports a charge they don't recognize.\nToday is Wednesday 2026-06-17.\n"
        "1. Sunday 2026-06-14 21:07:33, Purchase, 189.9 USD, Tienda, Sucursal Norte, Approved, USA\n"
        "2. Friday 2026-06-12 12:00:00, Purchase, 25.0 MXN, merchant not recorded, Declined, México"
    )

    shown = listed(context)

    assert [(n, day, str(amount), name) for n, day, amount, name in shown] == [
        (1, "2026-06-14", "189.9", "Tienda, Sucursal Norte"),
        (2, "2026-06-12", "25.0", "merchant not recorded"),
    ]
