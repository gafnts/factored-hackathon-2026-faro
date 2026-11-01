"""
The live language check reads the development side only, expects the label a family gives, counts what the model said
against it, and writes a page beside the run index (ADR-0005, The development regression set, as amended on
2026-10-02).
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from faro.agent.models import (
    MODEL,
    PROVIDER,
    Declared,
    Models,
    RequestDetails,
    RouterOutput,
    TransactionChoice,
)
from faro.agent.nodes.resolve import ASKS
from faro.evaluation import families, language

LOADED, ANSWERS = families.load(), families.load_answers()
HELD = families.held_out_ids(LOADED, ANSWERS)
BY_FAMILY = {f.family_id: f for f in LOADED}
USAGE = {
    "input_tokens": 100,
    "output_tokens": 10,
    "total_tokens": 110,
    "input_token_details": {"cache_read": 0, "cache_creation": 0},
}


def saying(
    language_of: Any, reason_of: Any = lambda text: None
) -> tuple[Models, list[float]]:
    """
    Models whose three readers say the language language_of(text) gives, and whose extraction reads the block reason
    reason_of(text) gives, recording each call's cost.
    """
    costs: list[float] = []

    def make(purpose: str) -> RunnableLambda[Any, Any]:
        async def invoke(messages: Any) -> Any:
            said = language_of(messages[-1].text)
            if purpose == "route":
                parsed: Any = RouterOutput(
                    requests=[], has_request=False, complaint=False, language=said
                )
            elif purpose == "extract":
                empty = dict.fromkeys(RequestDetails.model_fields)
                parsed = RequestDetails.model_validate(
                    {
                        **empty,
                        "language": said,
                        "block_reason": reason_of(messages[-1].text),
                    }
                )
            else:
                parsed = TransactionChoice(fitting=[1], language=said, question=None)
            raw = AIMessage(content="{}", usage_metadata=USAGE)
            return {"raw": raw, "parsed": parsed, "parsing_error": None}

        return RunnableLambda(invoke)

    async def record(kind: str, **fields: Any) -> None:
        costs.append(fields["cost_usd"])

    return Models(Declared(make, PROVIDER, MODEL), record), costs


def test_the_items_are_the_development_side_filled_and_labeled() -> None:
    items = language.development_items(LOADED, ANSWERS, HELD)

    by_id = {i.id: i for i in items if i.expected is not None}
    assert items
    assert not any(i.id.split("/")[0] in HELD for i in items)
    assert not any("{" in i.text for i in items)
    assert {i.call for i in items} == {"route", "extract", "choose"}
    third = next(
        f for f in LOADED if f.kind == "third_language" and f.family_id not in HELD
    )
    assert by_id[third.messages[0].id].expected == "other"
    marked = next(
        m for f in LOADED for m in f.messages if not m.clear and f.family_id not in HELD
    )
    assert by_id[marked.id].expected == "unclear"
    answer = next(
        a
        for a in ANSWERS
        if a.kind == "transaction_merchant" and a.answer_id not in HELD
    )
    assert (
        by_id[f"{answer.answer_id}/pt"].expected,
        by_id[f"{answer.answer_id}/pt"].call,
    ) == (
        "pt",
        "choose",
    )
    some = language.development_items(LOADED, ANSWERS, HELD, only=[third.family_id])
    assert {i.id.split("/")[0] for i in some} == {third.family_id}
    told = language.development_items(LOADED, ANSWERS, HELD, only=[answer.answer_id])
    assert {i.id for i in told} == {f"{answer.answer_id}/es", f"{answer.answer_id}/pt"}


def test_block_requests_and_reason_answers_also_read_the_reason() -> None:
    items = language.development_items(LOADED, ANSWERS, HELD)
    blocks = [f for f in LOADED if "block_card" in f.labels and f.family_id not in HELD]
    bare = next(f for f in blocks if "block_reason" not in f.extract)
    given = next(f for f in blocks if "block_reason" in f.extract)

    opener = bare.messages[0]
    reads = [i for i in items if i.id == opener.id]
    assert [(i.call, i.expected, i.reason) for i in reads] == [
        ("route", opener.language, None),
        ("extract", None, "none"),
    ]
    assert reads[1].context == ASKS["block_card"]
    assert reads[1].text == opener.text
    told = next(
        i for i in items if i.id == given.messages[0].id and i.call == "extract"
    )
    assert told.reason == given.extract["block_reason"]
    lost = next(
        a for a in ANSWERS if a.kind == "reason_lost" and a.answer_id not in HELD
    )
    card = next(a for a in ANSWERS if a.kind == "card_type" and a.answer_id not in HELD)
    by_id = {i.id: i for i in items if i.expected is not None}
    assert (
        by_id[f"{lost.answer_id}/es"].reason,
        by_id[f"{card.answer_id}/es"].reason,
    ) == (
        "lost",
        None,
    )
    assert all(i.call == "extract" for i in items if i.expected is None)
    assert not any(
        i.expected is None
        for i in items
        if i.id.split("/")[0] not in {f.family_id for f in blocks}
        and not i.id.endswith("/later")
    )


def test_an_answer_by_place_reads_the_card_as_an_answer_and_later() -> None:
    items = language.development_items(LOADED, ANSWERS, HELD)
    placed = next(
        a for a in ANSWERS if a.kind == "card_position" and a.answer_id not in HELD
    )

    reads = [i for i in items if i.id.startswith(f"{placed.answer_id}/es")]

    assert [(i.expected, i.context, i.card) for i in reads] == [
        ("es", language.CARDS, language.PLACED),
        (None, language.LATER, language.PLACED),
    ]
    # The card at that place in both lists.
    assert language.CARDS.endswith(f"ending in {language.PLACED}")
    assert language.LATER.endswith(f"ending in {language.PLACED}")


def test_the_card_read_for_an_answer_by_place_is_counted_apart(tmp_path: Path) -> None:
    answer = language.Item(
        "card_position-01/es",
        "es",
        "La segunda.",
        "extract",
        language.CARDS,
        card=language.PLACED,
    )
    later = language.Item(
        "card_position-01/es/later",
        None,
        "La segunda.",
        "extract",
        language.LATER,
        card=language.PLACED,
    )
    results = [
        language.Result(answer, "es", card="1177"),
        language.Result(later, "es", card="credit"),
    ]

    found = language.report(results, 0.0, datetime(2026, 10, 3, 12, tzinfo=UTC))

    # Only the answer enters the language count.
    assert found["items"] == 1
    assert found["cards_by_place"] == {
        "items": 2,
        "read": 1,
        "by_turn": {
            "answer": {"items": 1, "read": 1},
            "later": {"items": 1, "read": 0},
        },
        "misses": [
            {"id": "card_position-01/es/later", "expected": "1177", "said": "credit"}
        ],
    }
    language.write(found, tmp_path / "language", tmp_path / "language.md")
    rendered = (tmp_path / "language.md").read_text(encoding="utf-8")
    assert "## Cards named by their place, latest live check" in rendered
    assert "| later | 0 of 1 (0.0%) |" in rendered
    assert "segunda." not in rendered


def test_the_check_counts_what_the_model_said_against_the_label(tmp_path: Path) -> None:
    items = [
        language.Item("card_status-01/es/0", "es", "¿Mi tarjeta?", "route"),
        language.Item("card_status-01/pt/0", "pt", "Meu cartão?", "route"),
        language.Item("block_card-07/pt/2", "unclear", "bloquear", "route"),
        language.Item("none-10/en/0", "other", "Hello?", "route"),
        language.Item(
            "card_type-01/pt", "pt", "O de crédito.", "extract", language.CARDS
        ),
        language.Item(
            "transaction_newest-01/es",
            "es",
            "La más reciente.",
            "choose",
            language.LISTING,
        ),
        language.Item(
            "block_card-01/es/0",
            None,
            "Quiero bloquear mi tarjeta.",
            "extract",
            ASKS["block_card"],
            "none",
        ),
        language.Item(
            "reason_lost-01/es", "es", "La perdí.", "extract", language.REASON, "lost"
        ),
    ]
    said = {"Meu cartão?": "es", "bloquear": "pt", "Quiero bloquear mi tarjeta.": "es"}
    # The fake says the model's name; the check reports the code the graph maps it to.
    reasons = {"Quiero bloquear mi tarjeta.": "other_reason", "La perdí.": "lost"}
    models, costs = saying(
        lambda text: (
            said.get(text) or next(i.expected for i in items if i.text == text)
        ),
        reasons.get,
    )

    results = asyncio.run(language.check(models, items, parallel=2))
    found = language.report(results, sum(costs), datetime(2026, 10, 2, 12, tzinfo=UTC))

    # The item that only reads a reason enters no language count.
    assert found["items"] == 7
    assert found["by_language"] == {
        "es": {"items": 3, "read": 3},
        "other": {"items": 1, "read": 1},
        "pt": {"items": 2, "read": 1},
        "unclear": {"items": 1, "read": 0},
    }
    assert found["by_call"]["route"] == {"items": 4, "read": 2}
    assert found["by_call"]["extract"] == {"items": 2, "read": 2}
    assert found["misses"] == [
        {"id": "card_status-01/pt/0", "expected": "pt", "said": "es"},
        {"id": "block_card-07/pt/2", "expected": "unclear", "said": "pt"},
    ]
    assert found["block_reasons"] == {
        "items": 2,
        "read": 1,
        "by_reason": {
            "lost": {"items": 1, "read": 1},
            "none": {"items": 1, "read": 0},
        },
        "misses": [
            {"id": "block_card-01/es/0", "expected": "none", "said": "customer_request"}
        ],
    }
    assert found["cost_usd"] > 0
    assert "text" not in json.dumps(found)

    reports = tmp_path / "language"
    reports.mkdir()
    (reports / "2026-10-02-baseline.json").write_text(
        json.dumps(
            {
                "check": "baseline",
                "by_language": {
                    "es": {"items": 2, "read": 1},
                    "pt": {"items": 2, "read": 2},
                },
            }
        ),
        encoding="utf-8",
    )
    written = language.write(found, reports, tmp_path / "language.md")

    assert written.name == "2026-10-02-live.json"
    rendered = (tmp_path / "language.md").read_text(encoding="utf-8")
    assert "| es | 1 of 2 (50.0%) | 3 of 3 (100.0%) |" in rendered
    assert "| unclear |  | 0 of 1 (0.0%) |" in rendered
    assert "| block_card-07/pt/2 | unclear | pt |" in rendered
    assert "## Block reasons, latest live check" in rendered
    assert "| none | 0 of 1 (0.0%) |" in rendered
    assert "| block_card-01/es/0 | none | customer_request |" in rendered
    assert "bloquear" not in rendered

    # A second run on the same day keeps the first file; the page reads the latest by date.
    later = {**found, "date": "2026-10-02T18:05:00Z", "cost_usd": 0.5}
    del later["block_reasons"]
    again = language.write(later, reports, tmp_path / "language.md")

    assert again.name == "2026-10-02T18-05-live.json"
    assert written.exists()
    rendered = (tmp_path / "language.md").read_text(encoding="utf-8")
    assert "Latest live check: 2026-10-02T18:05:00Z" in rendered
    assert "## Block reasons" not in rendered
