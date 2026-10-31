"""
The traffic figures draw from aggregates, leaving out any value that rests on 1 to 9 rows (SEC-03), and draw
the projection's multiples only where a limit is reached.
"""

import polars as pl

from faro.analysis.capacity import project
from faro.analysis.traffic import STREAMS, Traffic
from faro.analysis.traffic_figures import NAMES, capacity, daily, hourly


def frame(data: object) -> pl.DataFrame:
    assert isinstance(data, pl.DataFrame)
    return data


def test_draws_every_table_in_its_own_panel(traffic_result: Traffic) -> None:
    data = frame(daily(traffic_result).data)

    assert data["table"].dtype == pl.Enum([NAMES[s] for s in STREAMS])
    # Every day of the small bank holds fewer than 10 rows, or none, which is drawn as zero.
    drawn = data.filter(pl.col("rows").is_not_null())
    assert (drawn["rows"] == 0).all()


def test_leaves_out_hours_resting_on_few_rows(traffic_result: Traffic) -> None:
    data = frame(hourly(traffic_result).data)

    assert data.height > 0
    assert data["share"].null_count() == data.height


def test_draws_only_the_limits_reached(traffic_result: Traffic) -> None:
    projection = project(traffic_result)
    data = frame(capacity(projection).data)

    reached = [h for h in projection.headroom if h.multiple is not None]
    assert data.height == len(reached)
    assert (data["multiple"] > 0).all()
