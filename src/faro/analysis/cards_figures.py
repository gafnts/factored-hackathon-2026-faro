"""
Figures for the card support report, drawn from aggregates only: a value resting on 1 to 9 rows isn't drawn (SEC-03).
"""

from collections.abc import Sequence
from datetime import date, timedelta

import polars as pl
from plotnine import (
    aes,
    expand_limits,
    facet_wrap,
    geom_col,
    geom_line,
    geom_point,
    geom_text,
    ggplot,
    labs,
    scale_x_continuous,
    scale_x_date,
    scale_y_continuous,
    scale_y_discrete,
)

from faro.analysis.candidates import DECLINE_REASONS
from faro.analysis.cards import (
    MEANINGS,
    UTILIZATION_STEP,
    YEAR,
    CardSupport,
)
from faro.analysis.plotting import INK, SERIES, base_theme, percent
from faro.analysis.report import SUPPRESS_BELOW

ALL_DECLINES = "All declines"
NO_CODE = "No code"
EVERY_DAY = "Every day in the snapshot"
LAST_YEAR = f"The last {YEAR} days"
OVER = "over"


def _drawn(n: int) -> bool:
    return not 0 < n < SUPPRESS_BELOW


def _ratio(n: int, total: int) -> float | None:
    return n / total if total and _drawn(n) else None


def _after_key(keys: Sequence[float] | Sequence[str]) -> list[str]:
    return [str(k).split("|", 1)[1] for k in keys]


def _thousands(values: Sequence[float] | Sequence[str]) -> list[str]:
    return [f"{float(v):,.0f}" for v in values]


def code_panel(code: str | None) -> str:
    return f"{code} {MEANINGS[code]}" if code in MEANINGS else NO_CODE


def daily_volume(result: CardSupport) -> ggplot:
    days = [
        (date.fromisoformat(str(g.values[0])), g.rows)
        for g in result.activity.daily.groups
    ]
    since = result.business_date - timedelta(days=YEAR - 1)
    rows = [
        (panel, day, float(n) if _drawn(n) else None)
        for day, n in days
        for panel in (EVERY_DAY, LAST_YEAR)
        if panel == EVERY_DAY or day >= since
    ]
    data = pl.DataFrame(
        rows,
        schema={"panel": pl.String, "day": pl.Date, "transactions": pl.Float64},
        orient="row",
    ).with_columns(pl.col("panel").cast(pl.Enum([EVERY_DAY, LAST_YEAR])))
    plot: ggplot = (
        ggplot(data, aes("day", "transactions"))
        + geom_line(color=SERIES[0], size=0.3)
        + facet_wrap("panel", ncol=1, scales="free_x")
        + scale_x_date(date_labels="%b %Y")
        + scale_y_continuous(labels=_thousands)
        + expand_limits(y=0)
        + labs(x="", y="Card transactions per day")
        + base_theme()
    )
    return plot


def declines_by_month(result: CardSupport) -> ggplot:
    totals = {
        str(g.values[0]): (g.rows, g.hits or 0) for g in result.declines.monthly.groups
    }
    codes = {
        (str(g.values[0]), g.values[1]): g.rows
        for g in result.declines.monthly_codes.groups
    }
    panels = [ALL_DECLINES, *(code_panel(c) for c in (*DECLINE_REASONS, None))]
    rows = []
    for month, (n, declined) in totals.items():
        start = date.fromisoformat(f"{month}-01")
        rows.append((ALL_DECLINES, start, _ratio(declined, n)))
        rows += [
            (code_panel(code), start, _ratio(codes.get((month, code), 0), n))
            for code in (*DECLINE_REASONS, None)
        ]
    data = pl.DataFrame(
        rows,
        schema={"panel": pl.String, "month": pl.Date, "share": pl.Float64},
        orient="row",
    ).with_columns(pl.col("panel").cast(pl.Enum(panels)))
    plot: ggplot = (
        ggplot(data, aes("month", "share"))
        + geom_line(color=SERIES[0], size=0.5)
        + facet_wrap("panel", ncol=3)
        + scale_x_date(date_breaks="1 year", date_labels="%Y")
        + scale_y_continuous(labels=percent)
        + expand_limits(y=0)
        + labs(x="", y="Share of the month's card transactions declined")
        + base_theme()
    )
    return plot


def utilization(result: CardSupport) -> ggplot:
    bands = result.balances.credit.utilization_bands
    total = bands.rows
    over_at = 100 + 1.5 * UTILIZATION_STEP
    rows = []
    for g in bands.groups:
        band = str(g.values[0])
        at = over_at if band == OVER else int(band) + UTILIZATION_STEP / 2
        share = _ratio(g.rows, total)
        label = f"over the limit: {share:.1%}" if band == OVER and share else ""
        rows.append((at, share, label))
    data = pl.DataFrame(
        rows,
        schema={"at": pl.Float64, "share": pl.Float64, "label": pl.String},
        orient="row",
    )
    plot: ggplot = (
        ggplot(data, aes("at", "share"))
        + geom_col(fill=SERIES[0], width=UTILIZATION_STEP * 0.85)
        + geom_text(
            aes(label="label"),
            color=INK,
            size=7,
            ha="right",
            va="bottom",
            nudge_y=0.005,
        )
        + scale_x_continuous(
            breaks=[0, 25, 50, 75, 100, over_at],
            labels=["0%", "25%", "50%", "75%", "100%", OVER],
        )
        + scale_y_continuous(labels=percent)
        + labs(
            x=f"Balance as a share of the credit limit, in bands of {UTILIZATION_STEP}%",
            y="Active credit cards with a limit",
        )
        + base_theme()
    )
    return plot


def conflicts(result: CardSupport) -> list[tuple[str, int, int]]:
    active = next((c for c in result.dates.cards if c.product_status == "Active"), None)
    cards = result.dates.cards
    last = result.dates.last_transaction
    history, *_, recent = result.dates.transactions
    holders = {str(g.values[0]): g.rows for g in result.holders.holder_status.groups}
    return [
        (
            "Active cards past their expiration date",
            active.expired if active else 0,
            active.cards if active else 0,
        ),
        (
            "Cards updated after the as-of instant",
            sum(c.updated_after_as_of for c in cards),
            sum(c.cards for c in cards),
        ),
        (
            "Cards whose last_transaction_date isn't their last transaction",
            last.cards - last.equal - last.neither,
            last.cards,
        ),
        (
            "Active cards whose holder isn't an active customer",
            sum(n for status, n in holders.items() if status != "Active"),
            sum(holders.values()),
        ),
        (
            "Card transactions after their card expired",
            history.after_expiration,
            history.transactions,
        ),
        (
            f"The same, in the last {recent.days} days",
            recent.after_expiration,
            recent.transactions,
        ),
        (
            "Card transactions before their card opened",
            history.before_opening,
            history.transactions,
        ),
        (
            f"The same, in the last {recent.days} days",
            recent.before_opening,
            recent.transactions,
        ),
    ]


def date_conflicts(result: CardSupport) -> ggplot:
    items = conflicts(result)
    # Keyed by position, since two labels repeat.
    keys = [f"{i}|{label}" for i, (label, _, _) in enumerate(items)]
    rows = [
        (key, share, "" if share is None else f"{share:.1%}")
        for key, (_, n, total) in zip(keys, items, strict=True)
        for share in (_ratio(n, total),)
    ]
    data = pl.DataFrame(
        rows,
        schema={"conflict": pl.String, "share": pl.Float64, "label": pl.String},
        orient="row",
    ).with_columns(pl.col("conflict").cast(pl.Enum(keys[::-1])))
    plot: ggplot = (
        ggplot(data, aes("share", "conflict"))
        + geom_point(color=SERIES[0], size=2.2)
        + geom_text(aes(label="label"), color=INK, size=7, nudge_y=0.4, ha="center")
        + scale_x_continuous(limits=(0, 1), labels=percent)
        + scale_y_discrete(labels=_after_key)
        + labs(x="Share of the rows each conflict is counted in", y="")
        + base_theme()
    )
    return plot
