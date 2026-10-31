"""
The capacity projection scales measured contacts under named assumptions, cites a source for every limit and
price it compares against (OPS-08), and finds where each limit is reached.
"""

from dataclasses import replace

import pytest

from faro.analysis.capacity import (
    ASSUMPTIONS,
    EXACT_BELOW,
    LIMITS,
    PRICES,
    PROFILES,
    SEARCH,
    headroom,
    load,
    poisson_quantile,
    project,
    scenarios,
)
from faro.analysis.traffic import Traffic


def test_poisson_quantile_is_exact_when_small_and_continuous_at_the_switch() -> None:
    assert poisson_quantile(0) == 0
    # A mean of 50 reaches 73 at the 99.9% quantile.
    assert poisson_quantile(50) == 73
    assert poisson_quantile(1e-6) == 0
    assert poisson_quantile(0.01) == 1
    below, above = poisson_quantile(EXACT_BELOW), poisson_quantile(EXACT_BELOW + 1e-9)
    assert abs(above - below) <= 1


def test_takes_each_scenario_from_the_measured_contacts(
    traffic_result: Traffic,
) -> None:
    chat, transactional, everything = scenarios(traffic_result)

    # One of the year's three contacts is a chat and one is Transaccional, with its own handle time.
    assert chat.share == pytest.approx(1 / 3)
    assert transactional.share == pytest.approx(1 / 3)
    assert (chat.conversation_seconds, transactional.conversation_seconds) == (
        250.0,
        300.0,
    )
    assert (everything.share, everything.conversation_seconds) == (1.0, 250.0)


def test_scales_demand_and_cost_with_the_bank(traffic_result: Traffic) -> None:
    everything = scenarios(traffic_result)[-1]
    flat, business = PROFILES
    one = load(traffic_result, everything, flat, 1)
    ten = load(traffic_result, everything, flat, 10)

    # This bank's size scales the development customers by 1.5: the busiest day's 3 contacts become 4.5.
    assert one.busiest_day == pytest.approx(4.5)
    assert ten.conversations_per_day == pytest.approx(10 * one.conversations_per_day)
    for a, b in zip(one.costs, ten.costs, strict=True):
        assert b.per_day == pytest.approx(10 * a.per_day)
        assert b.per_conversation == a.per_conversation
    assert load(
        traffic_result, everything, business, 1
    ).arrivals_per_hour == pytest.approx(2 * one.arrivals_per_hour)


def test_sessions_outlive_their_conversations(traffic_result: Traffic) -> None:
    everything = scenarios(traffic_result)[-1]
    result = load(traffic_result, everything, PROFILES[0], 1_000)

    assert result.sessions > result.active
    assert result.cold_starts_per_hour == result.arrivals_per_hour
    assert result.tokens_per_minute == pytest.approx(
        result.input_tokens_per_minute + result.output_tokens_per_minute
    )


def test_finds_the_multiple_at_which_a_limit_is_reached(
    traffic_result: Traffic,
) -> None:
    everything = scenarios(traffic_result)[-1]
    profile = PROFILES[0]
    limit = next(x for x in LIMITS if x.key == "bedrock_tokens")

    multiple = headroom(traffic_result, everything, profile, limit)

    assert multiple is not None
    assert (
        load(traffic_result, everything, profile, multiple).tokens_per_minute
        >= limit.value
    )
    assert (
        load(traffic_result, everything, profile, multiple * 0.999).tokens_per_minute
        < limit.value
    )
    unreachable = replace(limit, value=1e30)
    assert headroom(traffic_result, everything, profile, unreachable) is None
    assert SEARCH[1] > 1e6


def test_every_limit_and_price_cites_its_source() -> None:
    # OPS-08: a capacity limit is explained with where it comes from.
    for limit in LIMITS:
        assert limit.source.startswith(("https://", "../adr/"))
        assert limit.value > 0
    for price in PRICES:
        assert price.source.startswith("https://")
    assert len({a.key for a in ASSUMPTIONS}) == len(ASSUMPTIONS)
    assert all(a.basis for a in ASSUMPTIONS)


def test_projects_every_scenario_profile_and_scale(traffic_result: Traffic) -> None:
    result = project(traffic_result)

    assert len(result.loads) == len(result.scenarios) * len(PROFILES) * len(
        result.scales
    )
    assert len(result.headroom) == len(result.scenarios) * len(PROFILES) * len(LIMITS)
    assert result.bank_factor == 1.5
