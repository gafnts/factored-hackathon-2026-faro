"""
The generator on the bank: the regression set's composition is drawn whole, the same seed draws the same set, every
case passes the case format's and the split's checks with its slots filled, and a manifest names no customer or record.
"""

import json
import re
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import duckdb
import pytest

from faro.evaluation import bronze, cases, families, generator, heldout, oracle
from faro.evaluation.facts import contract_words

from . import outside
from .bank import Bank

pytestmark = pytest.mark.xdist_group("evaluation_bank")

LOADED, ANSWERS = families.load(), families.load_answers()
HELD = families.held_out_ids(LOADED, ANSWERS)


@pytest.fixture(scope="module")
def con(bank: Bank) -> Iterator[duckdb.DuckDBPyConnection]:
    with bronze.connect(bank.database, "development") as con:
        yield con


def draw(
    con: duckdb.DuckDBPyConnection, set_name: str = "regression"
) -> generator.Drawn:
    drawing = generator.Generator(
        con, "development", LOADED, ANSWERS, HELD, contract_words(), reuse=True
    )
    return drawing.draw(set_name, 7)


@pytest.fixture(scope="module")
def regression(con: duckdb.DuckDBPyConnection) -> generator.Drawn:
    return draw(con)


def test_the_regression_composition_is_drawn_whole_on_the_bank(
    regression: generator.Drawn,
) -> None:
    wanted = sum(
        n * len(generator.BY_NAME[name].languages)
        for name, n in generator.COMPOSITIONS["regression"].items()
    )

    assert regression.short == []
    assert len(regression.cases) == wanted
    assert {c["language"] for c in regression.cases} == {"es", "pt"}
    assert {c["group"] for c in regression.cases} >= {
        "reads",
        "block",
        "handoffs",
        "missing_data",
        "confirmation",
        "prompt_injection",
        "tool_failures",
    }
    assert {c["source"] for c in regression.cases} == {"natural", "built", "harness"}


def test_the_same_seed_draws_the_same_set(
    con: duckdb.DuckDBPyConnection, regression: generator.Drawn
) -> None:
    assert draw(con).cases == regression.cases


def test_every_message_is_filled_and_development_only(
    con: duckdb.DuckDBPyConnection, regression: generator.Drawn
) -> None:
    for case in [*regression.cases, *draw(con, "selection").cases]:
        texts = [m["text"] for m in case["script"]["messages"]]
        texts += [a["text"] for a in cases.texts(case["script"]["answers"])]
        assert not any(re.search(r"\{[a-z_]+\}", t) for t in texts), case["situation"]
        assert case["family_id"] not in HELD
        assert case["phrasing"] == "development"


def test_each_situation_takes_its_path(regression: generator.Drawn) -> None:
    for case in regression.cases:
        situation = generator.BY_NAME[case["situation"]]
        path = [
            (t["decisions"][-1]["outcome_class"], t["awaiting"])
            for t in case["expected"]["turns"]
        ]
        if situation.asks_card and path[0] == ("clarify", "card"):
            path = path[1:]
        assert path[: len(situation.path)] == situation.path


def test_a_situations_asides_are_drawn_in_order_each_a_phrasing_of_its_own(
    con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    situation = replace(
        generator.BY_NAME["status.aside"],
        asides=("aside", "aside", "aside"),
        path=[*[("clarify", "card")] * 3, ("abstain", "handoff_control")],
    )
    monkeypatch.setitem(generator.BY_NAME, situation.name, situation)
    drawing = generator.Generator(
        con, "development", LOADED, ANSWERS, HELD, contract_words(), reuse=True
    )

    drawn = drawing.draw("asides", 7, {situation.name: 1})

    assert drawn.cases
    for case in drawn.cases:
        asides = case["script"]["answers"]["aside"]
        assert len({a["id"] for a in asides}) == 3
        assert [t["sends"] for t in case["expected"]["turns"]][1:4] == ["aside"] * 3


def test_a_block_among_several_active_cards_asks_which_then_why(
    regression: generator.Drawn,
) -> None:
    drawn = [c for c in regression.cases if c["situation"] == "block.which_card"]

    assert {c["language"] for c in drawn} == {"es", "pt"}
    for case in drawn:
        first, second = case["expected"]["turns"][:2]
        assert (first["awaiting"], second["awaiting"]) == ("card", "reason")
        assert first["facts"]["{card_list}"].count("\n") >= 1
        assert "{cards}" not in first["facts"]
        assert case["expected"]["blocked"] == [case["script"]["means"]["product_id"]]


def test_a_manifest_names_the_situations_left_out_only_when_given(
    regression: generator.Drawn,
) -> None:
    situation = regression.cases[0]["situation"]

    named = generator.manifest("regression", 7, regression, {}, left_out=[situation])

    assert named["left_out"] == [situation]
    assert "left_out" not in generator.manifest("regression", 7, regression, {})
    with pytest.raises(generator.GeneratorError, match="no situation named"):
        generator.manifest("regression", 7, regression, {}, left_out=["none.such"])


def test_the_held_out_studys_left_out_situations_are_its_own() -> None:
    assert set(heldout.LEFT_OUT) <= set(generator.held_out(600))


def test_a_manifest_names_no_customer_or_record(regression: generator.Drawn) -> None:
    written = generator.manifest("regression", 7, regression, {"snapshot": "bank"})
    text = json.dumps(written)

    assert written["cases"] == len(regression.cases)
    assert not re.search(r"(CLI|PRD|TRX)-", text)
    for case in regression.cases:
        for message in case["script"]["messages"]:
            assert message["text"] not in text


BUILT_AND_FAULTED = (
    "decline.unlisted_code",
    "status.collision",
    "transactions.page.merchant_injection",
    "read.recovers",
    "read.fails.accepted",
    "read.fails.declined",
    "block.not_verified",
)


def taken(case: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (t["decisions"][-1]["outcome_class"], t["awaiting"])
        for t in case["expected"]["turns"]
    ]


def test_built_and_fault_situations_are_drawn_on_the_bank(
    con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        generator.COMPOSITIONS, "new", dict.fromkeys(BUILT_AND_FAULTED, 1)
    )
    drawn = draw(con, "new")

    assert drawn.short == []
    assert len(drawn.cases) == 2 * len(BUILT_AND_FAULTED)
    for case in drawn.cases:
        situation = generator.BY_NAME[case["situation"]]
        assert taken(case)[: len(situation.path)] == situation.path
        if situation.fault is not None:
            [fault] = case["faults"]
            assert case["source"] == "harness"
            assert case["fixtures"] == []
            assert fault["tool"] in situation.fault.tools
            assert fault["failures"] in situation.fault.failures
        else:
            assert case["source"] == "built"
            assert case["faults"] == []
            assert all("FIXTURE" in f["item"] for f in case["fixtures"])
            assert {f["customer_id"] for f in case["fixtures"]} == {case["customer_id"]}


def test_a_situation_short_of_natural_customers_is_topped_up_with_built_ones(
    con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    natural = generator.BY_NAME["transactions.next_page"]
    monkeypatch.setitem(
        generator.BY_NAME, natural.name, replace(natural, where="false")
    )
    monkeypatch.setitem(generator.COMPOSITIONS, "top_up", {natural.name: 1})
    drawn = draw(con, "top_up")

    assert drawn.short == []
    for case in drawn.cases:
        assert case["situation"] == natural.name
        assert case["source"] == "built"
        assert taken(case) == natural.path
        assert listed(case) > oracle.PAGE


def test_a_situation_whose_customers_run_out_shares_them_across_languages(
    con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    situation = generator.BY_NAME["status.one_card"]
    one = (
        f"{situation.where} and customer_id = "
        f"(select min(customer_id) from eval_summary where {situation.where})"
    )
    monkeypatch.setitem(
        generator.BY_NAME, situation.name, replace(situation, where=one)
    )
    monkeypatch.setitem(generator.COMPOSITIONS, "spent", {situation.name: 1})
    drawing = generator.Generator(
        con, "development", LOADED, ANSWERS, HELD, contract_words()
    )
    drawn = drawing.draw("spent", 7)

    assert drawn.short == []
    assert [c["language"] for c in drawn.cases] == ["es", "pt"]
    assert len({c["customer_id"] for c in drawn.cases}) == 1
    assert generator.manifest("spent", 7, drawn, {})["shared"] == [
        {"situation": situation.name, "language": "es", "cases": 1},
        {"situation": situation.name, "language": "pt", "cases": 1},
    ]


def test_a_situation_with_customers_to_spare_shares_none(
    con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    situation = generator.BY_NAME["status.one_card"]
    monkeypatch.setitem(generator.COMPOSITIONS, "spare", {situation.name: 1})
    drawing = generator.Generator(
        con, "development", LOADED, ANSWERS, HELD, contract_words()
    )
    drawn = drawing.draw("spare", 7)

    assert drawn.short == []
    assert len({c["customer_id"] for c in drawn.cases}) == 2
    assert generator.manifest("spare", 7, drawn, {})["shared"] == []


def test_an_earlier_held_out_sets_customers_are_taken_last_and_counted(
    con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    situation = generator.BY_NAME["status.one_card"]
    monkeypatch.setitem(generator.COMPOSITIONS, "spare", {situation.name: 1})

    def drawing(earlier: frozenset[str] = frozenset()) -> generator.Generator:
        return generator.Generator(
            con, "development", LOADED, ANSWERS, HELD, contract_words(), earlier=earlier
        )

    first = drawing().draw("spare", 7)
    earlier = frozenset(c["customer_id"] for c in first.cases)
    again = drawing(earlier).draw("spare", 7)

    assert len(again.cases) == len(first.cases) == 2
    assert not {c["customer_id"] for c in again.cases} & earlier
    assert generator.manifest("spare", 7, again, {}, earlier)["reused"] == []
    assert generator.manifest("spare", 7, first, {}, earlier)["reused"] == [
        {"situation": situation.name, "language": "es", "cases": 1},
        {"situation": situation.name, "language": "pt", "cases": 1},
    ]
    assert "reused" not in generator.manifest("spare", 7, again, {})


def test_a_held_out_set_borrows_no_wording(bank: Bank) -> None:
    every = (*LOADED, *outside.FAMILIES)
    every_answer = (*ANSWERS, *outside.ANSWERS)
    held = families.held_out_ids(every, every_answer)
    # The outside fixture opens a page of transactions but writes nothing to send after it, and nothing about a decline.
    unworded = {"transactions.next_page": 1, "decline.listed_code": 1}
    with bronze.connect(bank.database, "held_out") as held_con:
        drawing = generator.Generator(
            held_con,
            "held_out",
            every,
            every_answer,
            held,
            contract_words(),
            reuse=True,
        )
        with pytest.raises(generator.GeneratorError) as raised:
            drawing.draw("unworded", 7, {**unworded, "status.one_card": 1})

    named = str(raised.value)
    for situation in unworded:
        assert f"{situation} in es" in named and f"{situation} in pt" in named
    assert "status.one_card" not in named


def listed(case: dict[str, Any]) -> int:
    """
    How many transactions the case's two pages list between them.
    """
    first, second = case["expected"]["turns"]
    return sum(t["facts"]["{transactions}"].count("\n") + 1 for t in (first, second))


def test_the_selection_composition_follows_the_held_out_groups(
    con: duckdb.DuckDBPyConnection,
) -> None:
    drawn = draw(con, "selection")
    groups: dict[str, Any] = generator.counts(drawn.cases, "group")

    assert groups["reads"] > groups["block"] > 0
    assert groups["expired_sessions"] == 2


def test_the_selection_set_holds_an_expired_session_per_language_and_the_regression_set_none(
    con: duckdb.DuckDBPyConnection, regression: generator.Drawn
) -> None:
    drawn = draw(con, "selection")
    expired = [c for c in drawn.cases if c["situation"] == "session.expired"]

    assert sorted(c["language"] for c in expired) == ["es", "pt"]
    for case in expired:
        family = next(f for f in LOADED if f.family_id == case["family_id"])
        [message] = case["script"]["messages"]
        assert (case["group"], case["source"]) == ("expired_sessions", "harness")
        assert family.labels == ("card_status",) and not family.slots
        assert message["id"].split("/")[1] == case["language"]
        assert case["script"]["actions"] == [
            {"before_turn": 1, "action": "wait_past_token"}
        ]
        assert case["expected"]["turns"] == []
        assert case["expected"]["rules"] == ["POL-09"]
    assert not any(c["group"] == "expired_sessions" for c in regression.cases)


def test_the_selection_set_holds_the_access_cases_and_the_regression_set_none(
    con: duckdb.DuckDBPyConnection, regression: generator.Drawn
) -> None:
    drawn = draw(con, "selection")
    access = [c for c in drawn.cases if c["situation"].startswith("access.")]

    assert generator.counts(access, "situation") == {
        "access.direct.other": 2,
        "access.direct.own": 2,
    }
    for case in access:
        assert case["group"] == "unauthorized_access"
        assert case["source"] == "harness"
        assert case["family_id"] is None
        assert case["script"]["messages"] == []
        assert case["expected"]["turns"] == []
        assert case["fixtures"] == [] and case["faults"] == []
        if case["situation"] == "access.direct.other":
            other = case["script"]["means"]["other_customer_id"]
            assert other != case["customer_id"]
    assert not any(c["situation"].startswith("access.") for c in regression.cases)
