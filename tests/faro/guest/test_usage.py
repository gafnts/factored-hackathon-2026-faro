"""
The guest entry counts the guests it makes in a UTC day against the cap item's guests, before it makes one; makes none
while the chat is paused or the cap holds no guests; and never counts past the cap (ADR-0004, decision 21; ADR-0007,
The guest entry; OPS-08).
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from botocore.exceptions import ClientError

from faro.agent import usage as runtime_usage
from faro.guest.usage import CAP_KEY, Cap, admit, counted, read_cap

from .conftest import COUNTERS

NOW = datetime(2026, 11, 8, 23, 59, tzinfo=UTC)
TOMORROW = datetime(2026, 11, 9, 0, 1, tzinfo=UTC)


def with_cap(counters: Any, turns: int, guests: int | None) -> None:
    item = {**CAP_KEY, "turns": {"N": str(turns)}}
    if guests is not None:
        item["guests"] = {"N": str(guests)}
    counters.put_item(TableName=COUNTERS, Item=item)


def test_the_cap_is_the_runtimes_item() -> None:
    assert CAP_KEY == runtime_usage.CAP_KEY


def test_guests_count_up_to_the_cap_and_again_the_next_day(counters: Any) -> None:
    with_cap(counters, 750, 2)

    outcomes = [admit(counters, COUNTERS, NOW) for _ in range(3)]

    assert outcomes == ["counted", "counted", "full"]
    assert counted(counters, COUNTERS, NOW) == 2
    assert admit(counters, COUNTERS, TOMORROW) == "counted"
    item = counters.get_item(
        TableName=COUNTERS,
        Key={"counter": {"S": "guests"}, "window": {"S": "2026-11-08"}},
    )["Item"]
    assert item["expires_at"] == {
        "N": str(int(datetime(2026, 11, 9, tzinfo=UTC).timestamp()))
    }


def test_a_pause_makes_no_guest_and_counts_none(counters: Any) -> None:
    with_cap(counters, 0, 100)

    assert admit(counters, COUNTERS, NOW) == "paused"
    assert counted(counters, COUNTERS, NOW) == 0


def test_a_guests_cap_of_0_is_full(counters: Any) -> None:
    with_cap(counters, 750, 0)

    assert admit(counters, COUNTERS, NOW) == "full"


@pytest.mark.parametrize("guests", [None, "no cap"])
def test_a_missing_cap_or_guests_cap_makes_no_guest(
    counters: Any, guests: str | None
) -> None:
    if guests is None:
        with_cap(counters, 750, None)

    with pytest.raises(LookupError):
        admit(counters, COUNTERS, NOW)
    assert counted(counters, COUNTERS, NOW) == 0


def test_the_cap_is_read_with_its_guests(counters: Any) -> None:
    with_cap(counters, 750, 100)

    assert read_cap(counters, COUNTERS) == Cap(750, 100)


def test_a_failure_other_than_the_cap_is_raised(counters: Any) -> None:
    with_cap(counters, 750, 100)

    with pytest.raises(ClientError):
        admit(counters, "another-table", NOW)
