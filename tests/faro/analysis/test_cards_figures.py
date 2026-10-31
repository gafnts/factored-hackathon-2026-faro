"""
The card support figures draw shares and counts from aggregates, leaving out any value that rests on 1 to 9
rows (SEC-03).
"""

from dataclasses import replace

import polars as pl

from faro.analysis.cards import (
    Breakdown,
    CardSupport,
    Group,
    TransactionDates,
)
from faro.analysis.cards_figures import (
    ALL_DECLINES,
    EVERY_DAY,
    LAST_YEAR,
    NO_CODE,
    conflicts,
    daily_volume,
    date_conflicts,
    declines_by_month,
    utilization,
)


def frame(data: object) -> pl.DataFrame:
    assert isinstance(data, pl.DataFrame)
    return data


def test_draws_the_last_year_beside_the_whole_snapshot(
    card_result: CardSupport,
) -> None:
    data = frame(daily_volume(card_result).data)
    days = {
        str(panel): sorted(str(d) for d in group["day"])
        for (panel,), group in data.group_by("panel", maintain_order=True)
    }

    assert days[EVERY_DAY] == ["2025-01-01", "2026-03-01", "2026-06-10", "2026-06-17"]
    assert days[LAST_YEAR] == ["2026-03-01", "2026-06-10", "2026-06-17"]
    assert data["transactions"].null_count() == data.height


def test_draws_each_code_in_its_own_panel(card_result: CardSupport) -> None:
    data = frame(declines_by_month(card_result).data)

    assert data["panel"].dtype == pl.Enum(
        [
            ALL_DECLINES,
            "05 do not honor",
            "14 invalid card number",
            "51 insufficient funds",
            "54 expired card",
            NO_CODE,
        ]
    )
    # Every month declines fewer than ten, or none; a month with none still draws zero.
    march = data.filter(pl.col("month").cast(pl.String) == "2026-03-01")
    assert march["share"].to_list() == [0.0] * 6
    june = data.filter(pl.col("month").cast(pl.String) == "2026-06-01")
    assert june["share"].null_count() == 3


def test_labels_only_the_band_over_the_limit(card_result: CardSupport) -> None:
    bands = Breakdown(
        ("band",),
        (
            Group(("000",), 60),
            Group(("005",), 30),
            Group(("010",), 5),
            Group(("over",), 10),
        ),
    )
    result = replace(
        card_result,
        balances=replace(
            card_result.balances,
            credit=replace(card_result.balances.credit, utilization_bands=bands),
        ),
    )
    data = frame(utilization(result).data)

    assert data["at"].to_list() == [2.5, 7.5, 12.5, 107.5]
    assert data["share"].to_list()[2] is None
    assert data["label"].to_list() == ["", "", "", "over the limit: 9.5%"]


def test_counts_each_conflict_in_its_own_rows(card_result: CardSupport) -> None:
    items = {label: (n, total) for label, n, total in conflicts(card_result)}

    assert items["Active cards past their expiration date"] == (1, 4)
    assert items["Cards whose last_transaction_date isn't their last transaction"] == (
        4,
        5,
    )
    assert items["Active cards whose holder isn't an active customer"] == (1, 4)
    assert items["Card transactions before their card opened"] == (1, 5)
    assert frame(date_conflicts(card_result).data)["share"].null_count() == 8


def test_labels_every_conflict_it_draws(card_result: CardSupport) -> None:
    busy = replace(
        card_result,
        dates=replace(
            card_result.dates,
            transactions=(
                TransactionDates(None, 1_000, 0, 250),
                TransactionDates(30, 100, 0, 50),
            ),
        ),
    )
    data = frame(date_conflicts(busy).data)

    assert data["label"].to_list()[4:] == ["25.0%", "50.0%", "0.0%", "0.0%"]
    assert data["label"].to_list()[:4] == [""] * 4
