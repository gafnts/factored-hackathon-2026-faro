"""
The card support report prints no card number, suppresses small counts in every output (SEC-03), and a rerun
writes the same bytes (OPS-07).
"""

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from faro.analysis.cards import Association, CardSupport
from faro.analysis.cards_report import to_json, to_markdown, write

# The card bank's card numbers, held-out and unopened cards included.
NUMBERS = (
    "4000000000000002",
    "4111111111110002",
    "4000000000000010",
    "4222222222222222",
    "4333333333333333",
    "4444444444444444",
    "4555555555555555",
)


def _with_association(result: CardSupport, v: float) -> CardSupport:
    association = (Association("channel", 1_000, 5, 6, v),)
    return replace(
        result,
        declines=replace(result.declines, association=association),
        fraud=replace(result.fraud, association=association),
    )


def test_prints_no_card_number(card_result: CardSupport) -> None:
    for text in (to_markdown(card_result), to_json(card_result)):
        assert not any(n in text for n in NUMBERS)
        # A run of digits after a decimal point is a fraction, not a number printed whole.
        assert not re.search(r"(?<![.\d])\d{12,}", text)


def test_json_suppresses_small_counts_and_the_spreads_they_carry(
    card_result: CardSupport,
) -> None:
    data = json.loads(to_json(card_result))

    assert data["customers"] == "<10"
    assert data["holders"]["numbers"]["cards"] == "<10"
    assert data["holders"]["status"]["groups"][0] == {
        "values": ["Tarjeta Crédito", "Active"],
        "rows": "<10",
        "hits": None,
    }
    assert data["balances"]["credit"]["utilization"]["quantiles"] is None
    assert data["currency"]["conversions"][0]["ratio"] is None
    assert data["dates"]["transactions"][1]["days"] == 365
    assert data["settings"]["windows"] == [30, 90, 365]
    assert data["as_of"] == "2026-06-18 06:00:00"


def test_markdown_suppresses_small_counts(card_result: CardSupport) -> None:
    markdown = to_markdown(card_result)

    assert "(<10 of the <10 registered by the as-of instant)" in markdown
    assert "| `Tarjeta Crédito` | <10 | 0 | 0 | 0 | <10 |" in markdown
    assert "the median gap is n/a days" in markdown
    assert "`amount_usd` converts nothing" in markdown
    assert "| 2026-03-01 to 2026-06-17 | 3 | <10 | n/a | <10 | <10 |" in markdown


def test_findings_follow_the_numbers(card_result: CardSupport) -> None:
    markdown = to_markdown(card_result)
    numbers = card_result.holders.numbers
    unique = replace(
        card_result,
        holders=replace(
            card_result.holders,
            numbers=replace(numbers, shared_last_four_active_same_type=0),
        ),
    )

    assert (
        "**Confirming a card by its last four digits (CTL-02) needs a fallback**"
        in markdown
    )
    assert (
        "**The last four digits identify every active card of a type**"
        in to_markdown(unique)
    )
    assert "**Mexico has no pesos.**" in markdown
    assert "**Most active cards have a transaction in the last 30 days.**" in markdown
    assert (
        "Pending and reversed transactions carry the same four codes (<10 and 0"
        in markdown
    )
    assert "**No association could be measured**" in markdown
    assert "are unrelated to the fields" in to_markdown(
        _with_association(card_result, 0.004)
    )
    assert "**Decline codes and fraud marks follow the fields" in to_markdown(
        _with_association(card_result, 0.3)
    )


def test_names_decline_codes_by_their_meaning(card_result: CardSupport) -> None:
    markdown = to_markdown(card_result)

    assert "`05` do not honor, `14` invalid card number" in markdown
    assert "| `transaction_status` | `00` | `05` | `51` | (missing) |" in markdown


# Every value in the card bank rests on fewer than ten rows, so the figures skip them all.
@pytest.mark.filterwarnings("ignore::plotnine.exceptions.PlotnineWarning")
def test_writes_the_report_its_data_and_its_figures_the_same_twice(
    card_result: CardSupport, tmp_path: Path
) -> None:
    first = write(card_result, tmp_path / "a")
    second = write(card_result, tmp_path / "b")
    markdown = first[0].read_text()

    assert [p.name for p in first[:2]] == ["card-support.md", "card-support.json"]
    assert [p.suffix for p in first[2:]] == [".svg"] * 4
    for figure in first[2:]:
        assert f"figures/{figure.name}" in markdown
    for a, b in zip(first, second, strict=True):
        assert a.read_bytes() == b.read_bytes()
